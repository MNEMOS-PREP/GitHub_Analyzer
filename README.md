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
| Everything together; student and private output kept apart; per-stage timings | `pipeline.py` |
| Jobs, SHA-pinned snapshots kept until you delete them, per-commit cache | `service.py` |
| The `/v1` HTTP routes, and the last check before anything is sent | `api.py`, `guard.py` |

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
