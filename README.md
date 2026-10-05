# M03: GitHub analyzer

Reads a student's repository and answers three questions without ever giving a verdict:

1. **What is in it?** Lines of code excluding vendored, generated and binary files; the stack; tests, CI, Docker.
2. **How much of it did the student write?** A *range* from two independent methods, with the method stated. If the history
   is squashed it says "I can't tell from this history" and gives no number.
3. **What could we ask about?** Up to 12 decisions found in the code (a threshold, a hyperparameter, a bare `except`, a
   schema, a cache), each with a `file:line` citation, a four-step question ladder and a gentle fallback.

Nothing in the repository is ever executed. No model is used. No student-facing text contains words like *plagiarism*,
*copied* or *original*, and a committed secret is reported only as a private "rotate it" notice.

## Run it

```bash
pip install pytest
pip install -r requirements.txt          # tree-sitter grammars are optional; without them non-Python code gets regex checks only
python -m pytest -q                      # 133 pass, 1 skipped; about 2 minutes (many tests build real git repositories)
python -m gh_analyzer C:/path/to/repo --me you@example.com your-name --md out.md --json out.json
python -m gh_analyzer owner/name --me you@example.com      # clones with git (needs network); a fork is detected and only your additions counted
python -m gh_analyzer <repo> --reference corpus.json       # set aside code that matches reference material (see below)
```

Without `--me`, it lists the identities in the history ("are these commits yours?") and computes nothing about you.

## Layout

```
gh_analyzer/
  snapshot.py      safe access to a repo, history export, caps, symlink and file-count checks
  inventory.py     what is in the repo, minus the world
  contribution.py  share range, cadence, squashed-history and shallow-clone handling
  secrets.py       find, redact, report privately
  decisions.py     twelve kinds of decision (Python by AST)
  ts_detect.py    the same decisions for JavaScript, TypeScript, Java and Go, read with tree-sitter
  modgraph.py     import graph (centrality) for JavaScript, TypeScript, Java and Go
  forks.py         fork detection: count only what was added on top of the parent
  reference.py     MinHash match against a reference corpus; only ever removes questions, never shown
  ranking.py       interest score, authored-confidence, selection rules
  questions.py     question ladder, citation check, wording check
  selection.py     which of a student's repos to use; the no-repo path
  pipeline.py      everything together; student-facing and private output kept apart
docs/M03_ALGORITHMS.md   every number, where it came from, and how good the detectors are
results/                 saved real runs (see results/README.txt for which code version made each)
tests/                   unit, end-to-end and safety-gate tests
```

## What is true and what is not

* The pipeline runs on real repositories (checked on thirteen here) and on synthetic ones with known authorship.
* **No number has been validated against human judgement.** Detector precision was judged by one rater, the author: about
  half to two-thirds of the decisions it picks are ones an interviewer would sensibly ask about, and 6–10% are wrong
  (see `docs/M03_ALGORITHMS.md`).
* One test is skipped on Windows: the refusal of a symlink that points outside the repository needs symlink permission, which this machine lacks. The check is written but has not been exercised here.
* Python is read with `ast`; JavaScript, TypeScript, Java and Go with tree-sitter (if installed). Other languages, and these four without the grammars, get a short list of regex checks, and the output says so. The tree-sitter detectors have **not** been through a precision audit yet.
* Built but unmeasured: fork detection (needs the GitHub API or an `upstream` remote; never fetches into a repository on your disk) and reference matching (no corpus ships, so it does nothing until one is built).
* Not built: the model-written summary, the GitHub login, the service and database layer. The full list is in the algorithms document.
* `repo-agent/` next to this folder is a different thing: an agent that judges whether a repository is safe to adopt.
  M03 is about the student's own code, and does not judge.
