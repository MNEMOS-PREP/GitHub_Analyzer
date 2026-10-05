<div align="center">

# M03 · GitHub Analyzer

**Turns a student's repository into interview questions about their own code.**
*It never gives a verdict.*

![python](https://img.shields.io/badge/python-3.12-blue) ![tests](https://img.shields.io/badge/tests-136%20passing-brightgreen) ![executes repo code](https://img.shields.io/badge/executes%20repo%20code-never-success) ![model](https://img.shields.io/badge/model%20used-none-lightgrey)

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
| Repo selection and the no-repo path | `selection.py` |
| Everything together; student and private output kept apart | `pipeline.py` |

Every number and where it came from: [docs/M03_ALGORITHMS.md](docs/M03_ALGORITHMS.md)

---

## Status

| | |
|---|---|
| **Built** | Everything above, with 136 tests passing |
| **Not validated** | No number has been checked against human judgement. One rater (the author) judged the Python detectors: about half to two-thirds of the decisions chosen are worth asking, and 6–10% are wrong. The tree-sitter detectors, fork detection and reference matching are unmeasured |
| **Not built** | GitHub login, the service and database layer, the model-written summary, scaffold detection, the "explain your own code" gate |
| **Known limits** | Other languages (and the four above without the grammars) get a short list of regex checks, and the output says so. One test is skipped on Windows because it needs symlink permission |

> Reference matching ships **without a corpus**, so it does nothing until you build one:
>
> ```bash
> python -m gh_analyzer.reference build <folder of reference code> -o corpus.json
> ```
