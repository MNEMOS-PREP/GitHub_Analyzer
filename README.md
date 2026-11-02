<div align="center">

# M03 · GitHub Analyzer

**Turns a student's repository into interview questions about their own code.**
*It never gives a verdict.*

![python](https://img.shields.io/badge/python-3.12-blue) ![tests](https://img.shields.io/badge/tests-174%20passing-brightgreen) ![executes repo code](https://img.shields.io/badge/executes%20repo%20code-never-success) ![model](https://img.shields.io/badge/model%20used-none-lightgrey)

</div>

---

## What it does

| Question | Answer |
|---|---|
| **What is in it?** | Lines of code (minus vendored, generated and binary files), stack, tests, CI, Docker |
| **How much did they write?** | A *range* from two methods, with the method stated. Squashed history gives "I can't tell", never a made-up number |
| **What can we ask about?** | Up to 12 decisions (a threshold, a hyperparameter, a bare `except`, a schema, a cache), each with a `file:line` citation, a four-step question ladder and a gentle fallback |

### Guarantees

- Repository code is **never executed**, and no model is used.
- A bad or missing repo **never changes a score**.
- No student-facing text says *plagiarism*, *copied* or *original*.
- A committed secret appears only as a private "rotate it" notice.

---

## Quick start

```bash
pip install -r requirements.txt
python -m gh_analyzer <folder | owner/name | github link> --me you@example.com --md out.md --json out.json
```

| Option | Effect |
|---|---|
| `--me <emails or names>` | Which commits are yours. Without it, identities are listed and no share is computed |
| `--keywords <words>` | Resume terms; decisions that mention them rank higher |
| `--fork-of owner/name` | Parent repo, if it can't be found automatically |
| `--reference corpus.json` | Set aside code that matches reference material (tutorials, templates) |

Run the tests with `python -m pytest -q` (about 2 minutes; many build real git repositories).

---

## How it works

```
snapshot  →  inventory  →  contribution  →  decisions  →  ranking  →  questions
```

| Stage | File |
|---|---|
| Safe access, history export, size and symlink checks | `snapshot.py` |
| Files, languages, stack | `inventory.py` |
| Share range, cadence, squashed and shallow history | `contribution.py` |
| Fork handling: count only what was added on top of the parent | `forks.py` |
| Committed secrets: find, redact, report privately | `secrets.py` |
| Decisions: Python by `ast`; JS, TS, Java, Go by tree-sitter | `decisions.py`, `ts_detect.py` |
| Import graph (centrality) | `ranking.py`, `modgraph.py` |
| Reference matching (only ever removes questions) | `reference.py` |
| Ranking, question ladder, citation and wording checks | `ranking.py`, `questions.py` |
| Repo selection and the no-repo path | `selection.py`, `no_repo.py` |
| Intake: GitHub sign-in (read-only), pasted links, rate-limit backoff, named errors | `intake.py` |
| Which commits are yours: list, suggest, confirm | `identity.py` |
| "Explain your own code": a 90-second session on a function you wrote (coaching only) | `explain_gate.py` |
| Resume claims linked to the repo, as `verifiability` with zero effect on any score | `resume_link.py` |
| Competitive-programming profiles (Codeforces, LeetCode, CodeChef): read, ownership check, questions | `coding_profiles.py` |
| Everything together; student and private output kept apart; per-stage timings | `pipeline.py` |
| Jobs, SHA-pinned snapshots kept until you delete them, per-commit cache | `service.py` |
| The `/v1` HTTP routes, and the last check before anything is sent | `api.py`, `guard.py` |

### Coding profiles

A student links their Codeforces, LeetCode and CodeChef usernames (or pastes the profile links). We read the public
profile and turn the numbers into interview questions about the problems behind them. Same rules as the rest of M03:
no combined score, `score_effect` is always 0, and a platform we cannot read is "unavailable", never a mark against the student.

| Platform | Read through | If it breaks |
|---|---|---|
| Codeforces | the official API (`user.info`, `user.rating`, `user.status`), one call per two seconds | named error; the others still load |
| LeetCode | the public GraphQL endpoint the site uses (not an official API) | "unavailable"; last good numbers stay, marked stale |
| CodeChef | the public profile page, parsed tolerantly | "partial" or "unavailable"; never a guessed number |

Charts on the screen: submissions per day (a month with ‹ ›, the last 30 days, or the last 12 months, one colour per site), rating over time (each site on its own scale), and the level mix. The sites report *when* you submitted, not how long you worked, so time spent is shown as submissions and active days and the page says so. Colours come from the data-viz categorical order and were validated on the shell's light and dark surfaces; green and amber are left alone because they mean confirmed and reported here. Every chart has a hover readout and a "View as table".

Ownership: each handle gets a short code (`gha-xxxxxxxx`). The student puts it in a public profile field and calls
`/verify`. Until then the numbers are shown as self-reported. Reads are cached for six hours.

Just the numbers, from the command line (any one, two or all three sites; links work too):

```bash
python -m gh_analyzer.coding_profiles --leetcode abhinavnair28 --codeforces <user> --codechef <user> --json stats.json --csv stats.csv
```

| Route | Does |
|---|---|
| `PUT /v1/coding-profiles` | link, change or unlink handles (`""` unlinks); returns the verification codes |
| `POST /v1/coding-profiles/refresh` | read all linked profiles now |
| `POST /v1/coding-profiles/verify` | read again and look for the codes |
| `GET /v1/coding-profiles` | the profiles and every number read from them |
| `GET /v1/coding-profiles/insights` | plain-sentence readings of those numbers (mix of levels, contest trend, topics with few solves, steadiness), each with its figures; descriptive, never a mark |
| `GET /v1/coding-profiles/questions` | interview questions from the numbers (built, hidden on the screen for now) |
| `DELETE /v1/coding-profiles` | forget everything |

### Run the service

```bash
pip install -r requirements.txt
uvicorn --factory gh_analyzer.api:create_app
```

Every request carries `X-Candidate-Id`. Set `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` and `GITHUB_REDIRECT_URI` to turn on "Connect GitHub"; without them students paste a link. Storage is `$GHA_STORAGE` (default `./gha_storage`).

Every number and where it came from: [docs/M03_ALGORITHMS.md](docs/M03_ALGORITHMS.md)

---

## Status

| | |
|---|---|
| **Built** | Everything above, with 174 tests passing and 1 skipped (see the TODO for status per requirement) |
| **Not validated** | No number has been checked against human judgement. One rater (the author) judged the Python detectors: about half to two-thirds of the decisions chosen are worth asking, and 6–10% are wrong. The tree-sitter detectors, fork detection and reference matching are unmeasured |
| **Not built** | The front-end components (spec section 8), a database and object store (the service keeps records in memory and snapshots on local disk), the model-written summary (deliberate: no model is used), scaffold detection, decisions inside notebooks, someone else's personal data in a repo (EC-3.13). GitHub sign-in is tested against a stub, not against GitHub itself |
| **Known limits** | Other languages (and the four above without the grammars) get a short list of regex checks, and the output says so. One test is skipped on Windows because it needs symlink permission |

> Reference matching ships **without a corpus**, so it does nothing until you build one:
>
> ```bash
> python -m gh_analyzer.reference build <folder of reference code> -o corpus.json
> ```
