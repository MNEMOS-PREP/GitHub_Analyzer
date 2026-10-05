# M03 GitHub analyzer: what it does and where every number comes from

M03's job, from the module plan: read a student's repository, work out how much of it they wrote, find the decisions in
the code that only the author can explain, and turn each into a question with a `file:line` citation. Its absolute
rule: **it never produces a verdict**, and a bad or missing repository never lowers a score.

This document describes what is **built** (a deterministic pipeline: no model, no network except `git clone` and, for a fork, one GitHub API request),
what is **not built**, and how good the detectors actually are.

## Labels used for every number

| Label | Meaning |
|---|---|
| **PLAN** | Written in the M03 plan. Taken as given; nobody has fitted or validated it. |
| **CONVENTION** | A round number chosen so the behaviour is sensible. Not fitted. |
| **PRIOR** | A hand-set weight or ordering. Not fitted. |
| **EMPIRICAL (1 rater)** | Set after reading real output, checked by one person (the author). Not agreement with anyone. |
| **MEASURED** | Reported against human-rated data. **Nothing in M03 has this label yet.** |

## Pipeline

| Stage | File | What it does |
|---|---|---|
| 1 Selection | `selection.py` | Ranks a student's repositories by interview usefulness; flags practice repositories gently; the no-repo path |
| 2 Snapshot | `snapshot.py` | Safe access to a repository and its history. **Never executes repository code** |
| 3 Inventory | `inventory.py` | Lines of code excluding vendored, generated, binary and oversized files; stack; tests, CI, Docker, README, licence |
| 4 Contribution | `contribution.py` | A share range from two independent estimates; cadence; squashed-history detection |
| 5 Secrets | `secrets.py` | Finds committed secrets, redacts them everywhere, and reports them privately |
| 5 Reference match | `reference.py` | Sets aside decisions whose code matches a reference corpus. Optional; the match is never shown |
| 7 Decisions | `decisions.py`, `ts_detect.py` | Twelve kinds of decision found by deterministic detectors (Python by `ast`; JS, TS, Java, Go by tree-sitter) |
| 7b Ranking | `ranking.py` | Orders decisions; never asks about code the student did not write |
| 8 Questions | `questions.py` | Four-depth ladder per decision, each with a citation and a gentle fallback |

Stage numbers follow the plan. Stage 5 is built as `reference.py` but ships with no corpus. Stage 6's model-written paragraph is **not built** (see "Not built").

## The numbers

### Safety (stage 2)

| Number | Value | Label | Reason |
|---|---|---|---|
| Clone timeout | 120 s, then one retry at depth 50 | PLAN | Plan §6.2 |
| Clone depth | 500 commits | PLAN | Plan §6.2 |
| File cap | 20,000 files, refused above | PLAN | Plan §6.2. Excluded directories are not counted: they are never read, so they cannot hurt us |
| Size cap | 500 MB → "I skipped the data files" | PLAN, **reworded** | The plan says both "reject if >500 MB" and "skip large files, cap at 500 MB" (§6.2 and EC-3.4). We follow EC-3.4: a large repository is analysed, not refused |
| Symlink pointing outside the root | refused | PLAN | Plan §6.2 |

### Inventory (stage 3)

| Number | Value | Label |
|---|---|---|
| Excluded folders and file patterns | `node_modules`, `venv`, `dist`, `build`, `target`, `vendor`, `migrations`, `*.min.js`, lock files, `*_pb2.py`… | PLAN (list extended by us) |
| Largest file read | 500 KB | PLAN |
| Generated-file markers | "Code generated", "@generated", "DO NOT EDIT" in the first five lines | PLAN |
| README quality | `0.5·min(1, words/150) + 0.25·has code block + 0.25·has setup/usage heading` | CONVENTION. Not used in any score; shown only as a fact |
| Scaffold ratio | not computed | not built |

### Contribution (stage 4)

| Number | Value | Label | Why |
|---|---|---|---|
| Estimates | additions share and blame share | PLAN | Two independent methods, so one method's blind spot (a formatting pass rewrites blame; a squash hides additions) is visible as a disagreement |
| Range widening | ±5 points around min and max | PLAN | Honest about measurement error. Not fitted |
| Disagreement note | estimates differ by more than 30 points | PLAN | Says "attribution is unclear" instead of picking one |
| Only one estimate available | widened to ±15 points (the normal 5 plus 10 more) | CONVENTION | Three times the normal width, because there is no second method to cross-check |
| Single dump | ≥80% of additions within one 24-hour window | PLAN | Not an accusation: a student may develop locally and push once |
| Steady | ≥8 distinct days across ≥21 days | PLAN | |
| Bursty | 2 or 3 sessions separated by gaps of more than 7 days | PLAN, **order changed** | We test `bursty` before `steady`, otherwise three tight bursts a month apart would be called steady |
| Fewer than 5 commits | cadence "unknown" | PLAN | |
| Squashed history | ≤2 commits, or everything within 1 hour with ≥80% in one commit | PLAN | No number is produced; the sentence is "I can't tell from this history" |
| Blame files read | first 400 analysed files | CONVENTION | Bounds time on large repositories; a note says so |
| Rounding in the sentence | nearest 5 points | CONVENTION | Avoids false precision ("73.4%") |

**Shallow clones (our addition, tested).** A clone limited to N commits makes its oldest commit look like it added the whole
tree. Crediting that to its author would show a recent contributor as the author of code that predates the history. We
treat those additions and lines as *unknown* and stretch the range to cover both extremes. Test:
`test_a_recent_contributor_is_not_credited_with_code_older_than_the_history`.

**Git detail found while testing.** `git blame` marks the root commit of any repository as "boundary", not only shallow
clones. Using that flag wrongly dropped a normal first commit from the denominator. We now identify boundary commits from
the clone's own `shallow` file.

### Forks (stage 4, FR-3.8)

| Rule | Value | Label |
|---|---|---|
| Parent found by | the caller, then the GitHub API `parent` field (8 s timeout, silent on failure), then a git remote named `upstream` | CONVENTION |
| Parent history | fetched (depth 500) only into a clone we made ourselves; for a repository on the student's disk only refs it already has are used, so analysing it never changes it | CONVENTION; tested |
| What is counted | commits reachable from the parent's tip are left out of the additions share and out of the blame denominator | PLAN (§4: "analyse only the student's diff") |
| History usable | judged on the commits added on top of the fork, so a fork with two commits on top still gets an answer | CONVENTION |
| Parent history unreachable | whole repository analysed, with a note saying so. Never guessed | CONVENTION |

### Reference matching (stage 5)

| Number | Value | Label |
|---|---|---|
| Normalisation | comments dropped; identifiers, numbers and strings replaced by placeholders (keywords kept), so a renamed copy matches | CONVENTION |
| Shingle / signature | 6 tokens / 64-value MinHash; 16 bands of 4 rows to find candidates | CONVENTION |
| Window | corpus: 30 lines, step 15; lookup: decision line ± 12 | CONVENTION |
| Match threshold | estimated Jaccard ≥ 0.6 | CONVENTION: **not fitted; the false-match rate on real tutorials is unknown** |
| Effect | `authored_confidence` capped at 0.4, below the 0.5 never-ask line. It can only lower confidence | PLAN (hard rule: never changes a score, never shown) |

### Decisions (stage 7)

JavaScript, TypeScript, Java and Go are read with tree-sitter and produce the same twelve kinds where the language has the
construct (for example Express/Spring/Gin routes, `@Cacheable`, goroutines, `synchronized`, empty `catch`, `InsecureSkipVerify`,
Mongoose and JPA schemas). Numbers inside log calls, template strings and `status` comparisons are ignored, as in Python.
Their rules are copied from the Python ones, so they carry the same labels: **none has been audited yet**.

Twelve kinds, found by `ast` for Python and by a short list of regular expressions for other languages:
`magic_constant`, `hyperparameter`, `model_arch`, `data_split`, `error_handling`, `concurrency`, `caching`, `security`,
`schema_design`, `api_contract`, `algorithm_choice`, `dependency_choice`.

| Rule | Value | Label |
|---|---|---|
| Numbers that are never "magic" | 0, 1, -1, 2 | PLAN |
| A number is explained if | a comment on the same line, or a comment of ≥3 words on the line above | CONVENTION |
| Model class with fewer than N fields is not a schema decision | N = 6 | EMPIRICAL (1 rater) |
| Ignored keyword arguments | `ge`, `le`, `gt`, `lt`, `min_length`, `max_length`, `status_code`, `random_state`, `test_size`, `seed`, `figsize`, `timeout`… | EMPIRICAL (1 rater) |
| Slices and arguments inside f-strings and `log`/`print` calls | ignored | EMPIRICAL (1 rater) |
| Comparisons mentioning "status" | ignored | EMPIRICAL (1 rater) |
| `sorted(..., key=...)` | not a decision | EMPIRICAL (1 rater): flagged four times in one repository, always trivial |
| Seed, fixture and mock-data files; test files; notebooks | not scanned | CONVENTION |

### Ranking (stage 7b)

```
interest = 0.30·kind_weight + 0.25·authored_confidence + 0.20·centrality
         + 0.15·(1 − has_explanatory_comment) + 0.10·resume_claim_linkage
```

| Number | Value | Label |
|---|---|---|
| The five weights | 0.30 / 0.25 / 0.20 / 0.15 / 0.10 | PLAN |
| Per-kind weight | hyperparameter and model_arch 1.0 · magic_constant and data_split 0.9 · algorithm, schema, concurrency 0.8 · error handling, caching 0.7 · security, API 0.6 · dependency 0.5 | PRIOR: ordered by how naturally the question "why that value?" arises |
| Never ask below | `authored_confidence < 0.5` | PLAN |
| Neutral confidence when identity or blame is unavailable | 0.5 | CONVENTION: neither asked more nor less |
| Centrality | in-degree in the Python import graph, divided by the largest in-degree | PLAN (simplified: the plan says "centrality in module graph") |
| Output size | 12, at most 3 per file, at least 3 kinds when available | PLAN |
| No two decisions with the same kind and wording | enforced | EMPIRICAL (1 rater): `temperature = 0.1` appeared three times in one repository |

`authored_confidence` is the share of the decision's own lines that `git blame` attributes to the student. It is the only
use of authorship in ranking, and it only ever removes questions.

## What it produces for a student

The output has two parts, kept apart in code and tested:

* **Student-facing:** what was read, a contribution sentence that is always a range with its method
  ("roughly 70–85% of the code, based on commits and current file ownership"), and the questions.
* **Private:** secret notices ("rotate it and add it to `.gitignore`") and an audit trail.

Gates that tests enforce: no field named similarity, authenticity, verdict or plagiarism exists anywhere in the output;
none of *plagiarism, copied, fake, authentic, original, cheat, suspicious, fraud* appears in any student-facing string;
a committed secret appears nowhere except as a private notice; a README that says "ignore previous instructions" is never
read into any output; analysing a repository never changes it and never runs anything in it.

## How good are the detectors? (honest numbers)

One rater (the author) judged each decision the pipeline chose: **worth asking** (an interviewer would sensibly ask), **weak**
(real but trivial, such as truncating a log message), or **wrong** (not a decision at all).

| Sample | n | Worth asking | Weak | Wrong | Status |
|---|---|---|---|---|---|
| Five MNEMOS repositories, **before** any fix | 60 | 32 (53%) | 22 (37%) | 6 (10%) | The first look. Not tuned to |
| ET_ and Platform_Core, **after** the fixes (Unthinkable is TypeScript and yielded none) | 18 | 12 (67%) | 5 (28%) | 1 (6%) | Fresh repositories, so not tuned to. One more rule was added after this look (log-call arguments), so the 67% is slightly stale |

What the fixes were, each driven by something seen in the first 60: HTTP status codes passed as `status_code=404` (all 6
"wrong"), truncated log messages and f-strings, small data-carrier models, repeated identical settings.

**Read these numbers carefully.** One rater, who also wrote the detectors. 18 decisions is too few to say 67% rather than
50% or 80%. "Worth asking" is a judgement, not a ground truth. The plan's own targets (AC-3.4 decisions for 80% of non-trivial
repositories; AC-3.6 range brackets truth on 80% of a 10-repository audit set) need repositories where the true authorship
is known, which we do not have. What we do have is synthetic repositories whose authorship is known by construction. In the
two where a true share exists (an 80% group project, and a shallow clone where the true share is about 5%) the range brackets
it; a third (a rewrite) is correctly reported as "attribution is unclear" (see `tests/test_contribution.py`). Three cases
show the logic works as designed; they do not show it is accurate on real repositories.

Not covered by any number here: non-Python languages (regex only, and the output says so), notebooks (counted in lines, not
scanned for decisions), and whether students find the questions useful.

## Not built

| Item | Plan stage | Effect |
|---|---|---|
| A reference corpus | 5 | The matcher is built but nothing is bundled, so until one is collected tutorial code is still asked about |
| Model-written "what I understood" paragraph | 6 | A deterministic file map is shown instead (the plan's own fallback) |
| Module graph beyond JS/TS/Java/Go (regex on imports, so a dynamic import is missed) | 6 | Centrality is zero for other languages |
| GitHub OAuth, repo picker against the live API | 1 | `selection.rank_repos` takes plain metadata, so it works once a connection exists |
| Scaffold fingerprints | 3 | `scaffold_ratio` is not reported |
| Explain-your-own-code gate | 12 | Needs the voice module |
| Model phrasing of questions | 8 | Fixed templates only. A model could be added behind a gate like the behavioural demo's |
| API service, storage, async jobs | — | This is a library and a command line, not a service |
| Link to resume claims (M01) | 13 | `resume_keywords` is a plain list the caller supplies |

## Using it

```
python -m gh_analyzer <folder | owner/name | github link> --me you@example.com your-github-name --md out.md --json out.json
```

Without `--me` it lists the identities in the history and computes nothing about the student. With `--keywords`, decisions
that mention words from the resume rank higher.
