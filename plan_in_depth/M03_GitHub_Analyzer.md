# M3 — GitHub Analyzer & Authorship Forensics

> **Master plan reference:** §29 · **Phase:** 1 · **Owner:** E3 · **Depends on:** M1 (claims), M4 (ontology) · **Feeds:** M5, M6, M9, M13, M19

---

## 1. In one paragraph

The student connects GitHub (or pastes a repo URL). We snapshot the repo, read what's actually in it, work out how much of it they actually wrote, extract the **decisions** embedded in the code — the thresholds, the model choices, the schema, the error handling — and turn each one into a question only the real author can answer. The output is two things: an unfakeable question source for the project round, and a *verified* skill signal. The emotional product is the shift from "I'll just say I built it" to "I'd better actually understand my own repo." The ethical constraint, which is absolute: **a bad or absent repo never lowers a score.**

---

## 2. Reverse-thinking: the user

### 2.1 The four students who arrive here

| Student | Repo reality | What they fear | What they need |
|---|---|---|---|
| **Real builder** | 400 commits over 4 months, tests, CI | Nothing — wants to show off | Hard questions. If we ask "what does this project do?" we've lost him |
| **Tutorial finisher** | Followed a YouTube series, 6 commits, all "update" | Being called a fraud | Honest framing + a path to make it real. Not an accusation |
| **Group-project member** | Real repo, but they wrote 20% of it | Being asked about the 80% they didn't write | Questions targeted at *their* commits |
| **No GitHub at all** | Empty or nonexistent | Being penalised | A visible, dignified alternative path |

**Design consequence:** the copy on every surface of this module must work for all four simultaneously. That rules out anything resembling a verdict.

### 2.2 The emotional arc

```
BEFORE   "It's on my resume, I'll wing it."                       (bluffing)
   ↓
DURING   "It's asking why I set the batch size to 32."            (ownership shock)
   ↓
AFTER    "I need to actually re-read my own code."                (ownership)
   ↓
NEXT     Runs the "explain your own code" gate and passes.        (earned confidence)
```

**The arc we must never cause:**
```
"It said my project is 'mostly copied'."   → shame, disengagement, and quite possibly wrong
```

### 2.3 The journey

#### Screen 1 — Connect

```
┌──────────────────────────────────────────────────────────────┐
│  Show me what you've built                                   │
│                                                              │
│  [ Connect GitHub ]        ← reads public repos only         │
│                                                              │
│  or paste a repo link                                        │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ github.com/…                                           │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  Why: so I can ask you about your actual code instead of     │
│  generic project questions. Nothing here affects your score  │
│  — it only changes what I ask you.                           │
│                                                              │
│  [ I don't have a repo → ]                                   │
└──────────────────────────────────────────────────────────────┘
```

*"Nothing here affects your score"* is the single most important sentence in this module and it must be true. It is what lets the tutorial-finisher press the button.

#### Screen 2 — Repo picker (post-connect)

Repos ranked by *interview usefulness*, not by stars:

```
  Pick the project you'd talk about in an interview

  ⭐ crop-disease-detection        Python · 340 commits · has tests
     Matches "Crop Disease Detection" on your resume     [ Use this ]

  ○ portfolio-website             HTML/CSS · 12 commits
  ○ dsa-practice                  C++ · 890 commits
     ↳ This looks like practice code, not a project — usually not
       worth an interview round, but you can pick it.
  ○ ml-course-assignments         Jupyter · 24 commits
```

The inline note on `dsa-practice` is the kind of thing a mentor says and a tool usually doesn't. It costs one heuristic and buys a lot of trust.

#### Screen 3 — Analysis (narrated, 40–90 s)

```
Reading crop-disease-detection…
  ✓ 47 files, 3,200 lines (excluding libraries)
  ✓ Python, TensorFlow, Flask
  ✓ 340 commits between Jan and Apr 2025 — steady, not last-minute
  ⋯ Working out which parts you wrote
  ○ Finding the decisions worth asking about
```

*"steady, not last-minute"* — this is the human read of commit cadence, delivered as a compliment. It also quietly signals that we *can* tell, which changes behaviour.

#### Screen 4 — The repo read-back (the payoff screen)

```
┌────────────────────────────────────────────────────────────────┐
│  crop-disease-detection                                        │
│                                                                │
│  WHAT I UNDERSTOOD                                             │
│  A CNN image classifier for plant leaf disease, trained on     │
│  PlantVillage, served through a Flask endpoint with a simple   │
│  upload form. Model in model/cnn.py, training in train.py,     │
│  serving in app.py.                                            │
│                        [ That's right ]  [ Not quite — fix ]   │
│                                                                │
│  YOUR CONTRIBUTION                                             │
│  ~78% of the non-library code, across 340 commits              │
│  Most of your work: model/ and train.py                        │
│  Also in this repo: 2 other contributors (mostly in ui/)       │
│                                                                │
│  12 THINGS I COULD ASK YOU ABOUT                               │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ train.py:41 — you set threshold = 0.7                    │  │
│  │ "How did you pick 0.7? What changes at 0.5?"             │  │
│  │                                          [ Try this ]     │  │
│  ├──────────────────────────────────────────────────────────┤  │
│  │ model/cnn.py:18 — 3 conv layers, no batch norm           │  │
│  │ "Why this depth? Did you try adding normalisation?"      │  │
│  ├──────────────────────────────────────────────────────────┤  │
│  │ app.py:64 — no input size validation on upload           │  │
│  │ "What happens if someone uploads a 40MB TIFF?"           │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                │
│  [ Start the project round → 12 min ]                          │
└────────────────────────────────────────────────────────────────┘
```

**Note what is absent: any verdict.** No "authenticity score", no "originality 62%". The contribution figure is stated as a fact with a method behind it, not as a judgement. And "Also in this repo: 2 other contributors" is framed neutrally — group projects are normal and good.

#### Screen 5 — The "explain your own code" gate

A random function from *their* highest-contribution file, shown with line numbers, 90 seconds to explain by voice:

```
  Explain what this does and why it's written this way.
  (90 seconds — just talk, like you would to a teammate)

  38  def preprocess(img):
  39      img = cv2.resize(img, (224, 224))
  40      img = img.astype("float32") / 255.0
  41      if img.mean() < 0.2:
  42          img = cv2.equalizeHist(...)
  43      return np.expand_dims(img, axis=0)

                                             [ 🎤 Start ]
```

Line 41–42 is the interesting one — a conditional the student either remembers writing or doesn't. This is the highest-signal 90 seconds in the entire platform, and it costs almost nothing to run.

### 2.4 The no-repo path (must be as good as the main path)

```
  No repo? That's completely normal.

  I'll ask about your projects the way a professor would —
  design, trade-offs, and what you'd do differently — instead
  of asking about specific code.

  [ Continue without a repo ]

  Want one before your next drive? Here are three small builds
  that give you something real to talk about, each ~6 hours:
  → A REST API with auth and tests
  → A small data pipeline with a scheduled job
  → A model trained on a public dataset with a proper eval
  [ Show me ]
```

This is not a consolation prize; it is a genuinely useful product surface, and it converts a moment of shame into a task.

---

## 3. Reverse-thinking: the developer

### 3.1 The five traps

**Trap 1 — Building a plagiarism detector.** The instinct is to output "this is 40% copied". Resist it completely. False positives here are *devastating* to a student, the ground truth is unknowable (learning from a tutorial and then extending it is legitimate and normal), and the accusation adds no product value. *Counter: we output contribution share and question targets, never an authenticity verdict. Similarity to tutorial corpora is used only to* **avoid asking about code they didn't write** *— which is a helpful use of the exact same signal.*

That reframing is the whole design: **the near-duplicate detector's job is to pick better questions, not to catch cheats.**

**Trap 2 — `git blame` at HEAD as "contribution".** Blame attributes the *last toucher*, so a formatting pass or a merge rewrites attribution wholesale. *Counter: combine blame-at-HEAD with commit-level additions/deletions and treat the pair as a range, not a number (§6.4).*

**Trap 3 — Counting LOC without excluding the world.** `node_modules`, `venv`, `dist/`, `.min.js`, generated protobufs, migrations, lockfiles, dataset CSVs. A repo that is 95% `node_modules` will otherwise look enormous. *Counter: a maintained exclusion list + generated-file heuristics + `linguist`-style vendored patterns (§6.3).*

**Trap 4 — Live GitHub calls during an interview.** Rate limits, network hiccups, and the student pushing a commit mid-session all break reproducibility. *Counter: snapshot at a pinned SHA; every question and every evaluation reads the snapshot.*

**Trap 5 — Cloning arbitrary user-supplied repos into your worker.** That's remote code you're now hosting, plus zip-bombs, plus 4 GB repos, plus symlink escapes. *Counter: sandboxed clone with `--depth` limits, size caps, path sanitisation, no build/execute step ever (§6.2).*

### 3.2 The non-obvious hard parts

1. **Squashed history destroys everything.** A repo with 1 commit ("initial commit", 3,000 files) has no contribution signal at all. Detect and *say so* rather than inventing a number: "I can't tell contribution from this history — it looks like it was uploaded in one go."
2. **Committer identity ≠ student.** Students commit as `root@localhost`, `Your Name`, or a different GitHub account. Identity resolution needs email set matching + a "these commits are you?" confirmation step.
3. **Notebooks.** `.ipynb` diffs are JSON noise. Parse with `nbformat`, strip outputs, and diff the source cells only — otherwise every notebook repo looks like 90% churn.
4. **The most interesting code is often the smallest file.** Ranking questions by file size finds `models.py` with 400 lines of boilerplate and misses the 12-line function with the magic constant. Rank by *decision density*, not size.
5. **Monorepos and forks.** A fork with 3 of the student's commits on top of 8,000 upstream commits must be detected via the GitHub API's `parent` field, and analysed as *the diff against upstream*.

---

## 4. Functional requirements

| ID | Requirement | Priority |
|---|---|---|
| FR-3.1 | Connect via GitHub OAuth (read-only, public repos) or accept a public URL | P0 |
| FR-3.2 | Snapshot the repo at a pinned SHA into immutable storage | P0 |
| FR-3.3 | Never execute repository code, at any stage | P0 |
| FR-3.4 | Compute LOC excluding vendored, generated, and binary files | P0 |
| FR-3.5 | Detect language/framework/dependency stack | P0 |
| FR-3.6 | Resolve committer identity to the student, with a confirmation step | P0 |
| FR-3.7 | Compute contribution share as a range with a stated method | P0 |
| FR-3.8 | Detect fork/upstream and analyse only the student's diff | P1 |
| FR-3.9 | Extract an architecture summary and a module graph | P0 |
| FR-3.10 | Extract ≥5 interrogable decisions for a non-trivial repo | P0 |
| FR-3.11 | Generate a code-grounded question per decision with a file:line citation | P0 |
| FR-3.12 | Run the "explain your own code" gate on a function from a high-contribution file | P1 |
| FR-3.13 | Link repos to resume claims (M1) and set `verifiability` | P1 |
| FR-3.14 | **Never lower any score due to repo quality or absence** | P0 |
| FR-3.15 | Never surface an authenticity/plagiarism verdict to the student | P0 |
| FR-3.16 | Handle squashed history by reporting "cannot determine", not a fabricated number | P0 |
| FR-3.17 | Complete p95 in ≤ 90 s for a median student repo | P0 |
| FR-3.18 | Support the full no-repo path with an equivalent project round | P0 |

---

## 5. Data contracts

```python
class RepoSnapshot(BaseModel):
    id: UUID
    candidate_id: UUID
    provider: Literal["github"]
    owner: str
    name: str
    pinned_sha: str
    default_branch: str
    is_fork: bool
    upstream: str | None
    storage_uri: str
    snapshot_at: datetime
    size_bytes: int
    truncated: bool             # hit a cap

class CodeInventory(BaseModel):
    total_files: int
    analysed_files: int
    excluded_files: int
    loc_total: int
    loc_excluded: int
    languages: dict[str, int]        # lang -> loc
    frameworks: list[str]
    dependencies: list[str]
    has_tests: bool
    test_loc: int
    has_ci: bool
    has_dockerfile: bool
    has_readme: bool
    readme_quality: float            # 0–1
    licence: str | None
    scaffold_ratio: float            # fraction that is framework boilerplate

class ContributionAnalysis(BaseModel):
    history_usable: bool             # False for squashed/single-commit repos
    reason_if_unusable: str | None
    total_commits: int
    candidate_commits: int
    candidate_identities: list[str]  # emails/logins matched, after confirmation
    other_contributors: int
    additions_share: float
    blame_share: float
    share_low: float                 # the RANGE we report
    share_high: float
    cadence: Literal["steady","bursty","single_dump","unknown"]
    active_days: int
    span_days: int
    primary_areas: list[str]         # directories where their work concentrates
    commit_message_quality: float
    uses_branches: bool
    uses_prs: bool

class SimilarityFinding(BaseModel):
    """Used ONLY to steer question selection away from non-authored code."""
    path: str
    span: tuple[int, int]
    match_source: Literal["tutorial_corpus","stackoverflow","scaffold","upstream_fork"]
    similarity: float
    # deliberately: no 'verdict' field, no student-facing surface

class CodeDecision(BaseModel):
    id: UUID
    repo_id: UUID
    path: str
    line_start: int
    line_end: int
    snippet: str
    kind: Literal["magic_constant","algorithm_choice","model_arch","hyperparameter",
                  "schema_design","concurrency","error_handling","caching",
                  "security","dependency_choice","data_split","api_contract"]
    summary: str                     # "threshold set to 0.7 with no comment"
    authored_confidence: float       # from blame + similarity; low → don't ask about it
    interest_score: float            # drives ranking
    concepts: list[str]

class RepoQuestion(BaseModel):
    decision_id: UUID
    depth: Literal[0,1,2,3]
    question: str
    expects: list[str]
    citation: str                    # "train.py:41"
    fallback_if_unanswered: str      # a gentler reframe, not a gotcha
```

---

## 6. The pipeline in minute detail

### 6.1 Stage 1 — Repo selection & ranking
Fetch the user's repo list via API. Rank by `interview_usefulness`:
```
usefulness = 0.30·loc_norm + 0.20·commit_span_norm + 0.15·has_readme
           + 0.15·resume_title_match + 0.10·has_tests + 0.10·(1 - is_practice_repo)
is_practice_repo: name matches /dsa|leetcode|practice|assignment|hackerrank|striver/i
                  OR >70% of files are single-function solutions
```

### 6.2 Stage 2 — Safe snapshot
```
git clone --depth 500 --single-branch --no-tags <url> /sandbox/<uuid>
  timeout 120s · disk quota 500 MB · no submodules · no LFS fetch
  reject if: >20k files, >500 MB, any path escapes the root after realpath
  never run: npm install, pip install, make, or any repo script
```
Then `git log --numstat` is exported to a JSON sidecar (so later stages need no git), the tree is copied to R2 with `retention_class=derived_features`, and the sandbox is destroyed.

### 6.3 Stage 3 — Inventory
Exclusion list (maintained YAML): `node_modules/ venv/ .venv/ dist/ build/ target/ vendor/ __pycache__/ .git/ *.min.js *.lock *-lock.json *.pb.go migrations/ .next/ coverage/` + binary sniff + files > 500 KB + generated-header detection (`// Code generated by`, `@generated`, `DO NOT EDIT`).

Scaffold detection: fingerprint against known scaffolds (CRA, Vite, Next, Django `startproject`, Spring Initializr, cookiecutter-data-science) by file-tree shape + exact-content match on their default files. `scaffold_ratio` is reported so a 30k-LOC repo that is 29k scaffold is described honestly — to us, not as a judgement to the student.

### 6.4 Stage 4 — Contribution analysis

**Identity resolution first.** Collect all `author_email`/`author_name` pairs. Match against the student's GitHub emails (API) and account name. Anything ambiguous goes to a one-question UI: *"Are these commits yours? `student@gmail.com`, `root@DESKTOP-4F2`"*. Cheap, accurate, and it makes the student complicit in the measurement, which is good.

**Two independent estimates, reported as a range:**
```
additions_share = Σ(additions by candidate on analysed files) / Σ(all additions)
blame_share     = lines attributed to candidate at HEAD / total analysed lines
share_low, share_high = min/max of the two, widened by ±5%
```
If the two disagree by > 30%, report the range and note *"attribution is unclear in this repo (looks like a big refactor or a formatting pass)"*.

**Cadence classification:**
```
single_dump : ≥80% of additions land within one 24-hour window
bursty      : commits cluster in ≤3 sessions with gaps > 7 days
steady      : commits on ≥8 distinct days across ≥3 weeks
unknown     : <5 commits or squashed history
```
`single_dump` is **not** an accusation — a student may develop locally and push once. It only downgrades our confidence in cadence-based statements and suppresses the "steady, not last-minute" compliment.

**Squashed history (FR-3.16):** `total_commits ≤ 2` or all commits within 1 hour with the entire tree added → `history_usable=false`, and every downstream statement about contribution is suppressed. We say "I can't tell from this history", which is honest and costs nothing.

### 6.5 Stage 5 — Similarity findings (internal only)
MinHash + LSH (`datasketch`, 128 permutations, shingle size 5 tokens) over normalised code (comments stripped, identifiers preserved, whitespace collapsed) against:
- a local tutorial corpus (~2,000 popular tutorial repos, built once, stored as signatures not source),
- known scaffold contents,
- upstream (for forks) via direct diff.

Output feeds exactly one place: `CodeDecision.authored_confidence`. **Decisions with `authored_confidence < 0.5` are excluded from question generation.** That's it. No verdict, no student-facing number, no `similarity` field in any API response the frontend can reach.

### 6.6 Stage 6 — Architecture extraction
1. Parse imports per language (`ast` for Python, `tree-sitter` for JS/TS/Java/C++) → module graph in `networkx`.
2. Identify entry points (`main`, `app.py`, `index.js`, `@SpringBootApplication`), config surface, and external I/O (HTTP routes, DB calls, file reads, model loads).
3. For ML repos specifically, detect the pipeline shape: dataset load → transform → model definition → training loop → evaluation → serialisation. Missing stages are themselves interesting ("there's no separate eval script — how did you measure 94%?").
4. LLM pass (mid tier) over a curated context — README + entry points + the module graph + 15 highest-centrality file headers — producing the human-readable "what I understood" paragraph on Screen 4. **This paragraph must be confirmable by the user** (`That's right / Not quite`), because a wrong understanding leads to wrong questions, and one click fixes it.

### 6.7 Stage 7 — Decision extraction (the core value)

Deterministic detectors first, LLM to rank and phrase:

| Detector | Rule |
|---|---|
| Magic constant | Numeric literal (not 0/1/-1/2) in a conditional, slice, or kwarg, with no named constant and no comment |
| Hyperparameter | Known kwarg names (`lr`, `learning_rate`, `batch_size`, `epochs`, `n_estimators`, `max_depth`, `dropout`, `temperature`) |
| Model architecture | Layer stacks, `nn.Module` subclasses, `Sequential([...])` |
| Data split | `train_test_split`, manual slicing, any `random_state`/`seed` |
| Algorithm choice | Sort/search/data-structure selection where alternatives are obvious |
| Schema design | Model/table definitions, index declarations, denormalised fields |
| Concurrency | Threads, async, locks, queues, `n_jobs` |
| Error handling | Bare `except:`, swallowed exceptions, missing validation on external input |
| Caching | Any memoisation, TTL, or cache key construction |
| Security | Hardcoded secrets (redact immediately, flag privately), missing authz on a route, unvalidated upload |
| Dependency choice | A heavy dependency used for one function |

```
interest_score = 0.30·decision_kind_weight
               + 0.25·authored_confidence
               + 0.20·centrality_in_module_graph
               + 0.15·(1 - has_explanatory_comment)
               + 0.10·resume_claim_linkage
```
Take the top 12. Ensure diversity: max 3 decisions per file, at least 3 distinct `kind`s.

**Secrets found in code** (`AKIA…`, `sk-…`, private keys) are never shown in a question, are redacted in storage, and generate a private, kind notification: *"Heads up — there's what looks like an API key committed in `config.py:12`. Rotate it and add it to `.gitignore`."* That single feature will earn more goodwill than most of the roadmap.

### 6.8 Stage 8 — Question generation
Per decision, a 4-depth ladder with a mandatory `file:line` citation and a `fallback_if_unanswered` that reframes rather than escalates:

> **Q:** "You set `threshold = 0.7` in `train.py:41`. How did you pick that?"
> **Fallback:** "No worries if you don't remember the exact reason — what would you check to decide it now?"

The fallback exists because the goal is to *find out what they understand*, not to enjoy watching them fail. A student who can reason about how to pick a threshold, even if they've forgotten why they picked this one, has demonstrated the thing we care about.

---

## 7. API surface

```
GET  /v1/github/authorize                 OAuth start (read-only public scope)
GET  /v1/github/repos                     ranked list
POST /v1/repos                            {url|repo_id} → 202 snapshot job
GET  /v1/repos/{id}                       inventory + architecture + contribution
GET  /v1/repos/{id}/stream                SSE progress
POST /v1/repos/{id}/identities/confirm    {emails: [...]}
POST /v1/repos/{id}/understanding/confirm {corrected_summary?}
GET  /v1/repos/{id}/decisions             ranked, with citations
GET  /v1/repos/{id}/questions?depth=
POST /v1/repos/{id}/explain-gate          → creates a 90-second explain session
GET  /v1/repos/{id}/security-notices      private, self-only
DELETE /v1/repos/{id}
```

**API guarantee:** no endpoint returns `SimilarityFinding` or any authenticity score. It is not exposed to the frontend at all, so it cannot leak through a UI change.

---

## 8. UI specification

**Components:** `GitHubConnectCard` · `RepoPicker` (with usefulness hints) · `AnalysisProgress` · `UnderstandingCard` (confirmable) · `ContributionPanel` · `DecisionList` (with syntax-highlighted snippet + citation) · `ExplainGate` (code + 90s recorder) · `NoRepoPath` · `SecurityNotice`

**States:**

| Component | Loading | Empty | Error | Partial |
|---|---|---|---|---|
| RepoPicker | skeleton rows | "No public repos — paste a link or continue without" | "Couldn't reach GitHub — paste a link instead" | private repos hidden with a note |
| ContributionPanel | shimmer | "Can't determine from this history" (squashed) | — | range shown wide, with the reason |
| DecisionList | skeleton | "This repo is mostly configuration — I'll ask design questions instead" | — | fewer than 5, supplemented by M1 claims |

**Copy rules [enforced in review]:**
- Never the words: *plagiarism, copied, fake, authentic, original* in any student-facing string.
- Contribution is always a **range** with a method note: "roughly 70–85% of the code, based on commits and current file ownership".
- Other contributors are stated neutrally and never framed as a reduction.
- Squashed history: "I can't tell from this history" — never "suspicious".

**Accessibility:** code snippets have a "read aloud" alternative and full text in the DOM (not canvas); the explain gate offers typing instead of speaking; syntax highlighting meets contrast requirements in both themes.

---

## 9. Edge cases

| ID | Case | Behaviour |
|---|---|---|
| EC-3.1 | Empty repo | "Nothing to read here yet" + no-repo path |
| EC-3.2 | Single "initial commit" with 3,000 files | `history_usable=false`; contribution suppressed; questions still generated from code |
| EC-3.3 | Fork with 3 commits on top | Analyse the diff only; explicitly frame: "you contributed X to a fork of Y" |
| EC-3.4 | 4 GB repo with datasets | Cap at 500 MB; skip large files; note "I skipped the data files" |
| EC-3.5 | Notebook-only repo | `nbformat` source-cell extraction; outputs stripped |
| EC-3.6 | Private repo | Not accessible with our scope; offer URL+zip upload path or skip |
| EC-3.7 | Repo deleted after snapshot | Snapshot is ours; analysis continues; live links disabled |
| EC-3.8 | Student pushes mid-session | Session uses the pinned SHA; a note offers re-analysis after |
| EC-3.9 | 40 contributors (a class project) | Contribution range widened; questions restricted to their directories |
| EC-3.10 | All code is scaffold | "This is mostly generated setup — tell me what you added" + design questions |
| EC-3.11 | Language we can't parse (Rust, Go, Kotlin) | Fall back to regex detectors + LLM reading; note reduced depth |
| EC-3.12 | Committed secrets | Redact, private notice, never in a question |
| EC-3.13 | Repo contains someone else's PII | Detect + do not include in any prompt or UI |
| EC-3.14 | Repo URL is not a repo (gist, org, user) | Named error + suggestion |
| EC-3.15 | GitHub rate limit hit | Queue with backoff; user sees "GitHub is busy, I'll keep trying" |
| EC-3.16 | Zip-bomb / symlink escape | Rejected at clone stage; logged to `security_events` |
| EC-3.17 | Student has 200 repos | Rank, show top 8, search box for the rest |

---

## 10. Failure modes & fallbacks

| Failure | Fallback | User sees |
|---|---|---|
| Clone timeout | Retry once shallower (`--depth 50`) | "Big repo — I read the recent history" |
| Parse failure on a language | Regex + LLM reading | Fewer decisions, no error |
| LLM architecture pass fails | Deterministic module graph only | Understanding card shows the file map instead of prose |
| Fewer than 5 decisions found | Supplement with M1 resume-claim probes + design questions | Seamless; the round still runs |
| Snapshot storage down | Analyse in-memory, don't persist, warn | "I couldn't save this — questions will work now but I'll need to re-read next time" |
| Everything fails | No-repo path | Full project round, professor-style |

---

## 11. Performance & cost budget

| Stage | p95 | Cost |
|---|---|---|
| Repo list + ranking | 2 s | — |
| Clone + snapshot | 25 s | — |
| Inventory + exclusions | 3 s | — |
| Contribution analysis | 5 s | — |
| Similarity (MinHash) | 4 s | — |
| Module graph parse | 6 s | — |
| Architecture LLM pass | 8 s | ₹1.20 |
| Decision detection (deterministic) | 4 s | — |
| Decision ranking + question gen (LLM, batched) | 12 s | ₹1.80 |
| **Total** | **≤ 90 s** | **≈ ₹3.0** |

Once per repo per SHA, cached. Re-analysis only on explicit request or a new pinned SHA.

---

## 12. Test plan

**Unit** — exclusion list against 15 real repo trees · scaffold fingerprints · cadence classifier on synthetic commit histories · magic-constant detector precision · secret detector (must catch AWS/OpenAI/private-key patterns, must not fire on `version = "1.0.2"`).

**Integration** — 30 real student repos (consented, collected from seniors), spanning: real project, tutorial clone, group project, notebook-only, fork, squashed. Assert: no crashes, ≥5 decisions for the 20 non-trivial ones, contribution range sane on the 10 where we know ground truth.

**Safety tests [GATE]**
- Grep the entire frontend bundle and API response schemas for `similarity`, `plagiar`, `authentic` → must be zero.
- A repo that is 100% tutorial code must still produce a working round and must **not** produce any negative-tone string.
- Repo absence must produce the same score distribution as repo presence on a matched cohort (checked in Phase 4 with real data).

**Adversarial** — symlink escape, zip bomb, 20k files, a file named `../../etc/passwd`, a repo containing a fork bomb (never executed — assert we never execute anything), a README with prompt injection.

**Manual QA** (10 min): connect → pick a repo → verify the understanding paragraph is actually right → check 3 decisions cite real lines → run the explain gate → confirm no judgemental language anywhere → run the no-repo path end to end.

---

## 13. Metrics & acceptance gates

| ID | Metric | Target |
|---|---|---|
| AC-3.1 **[GATE]** | No authenticity/similarity value reachable from any client | verified by bundle + schema scan in CI |
| AC-3.2 **[GATE]** | Repo absence or quality never changes a score | verified by design review + Phase-4 cohort check |
| AC-3.3 **[GATE]** | Repository code is never executed | verified by sandbox audit |
| AC-3.4 | Decisions extracted for non-trivial repos | ≥5 for 80% of them |
| AC-3.5 | Every question cites a real, resolvable `file:line` | 100% |
| AC-3.6 | Contribution range brackets manual ground truth | ≥80% of the 10-repo audit set |
| AC-3.7 | Understanding paragraph accepted without correction | ≥70% |
| AC-3.8 | p95 analysis time | ≤ 90 s |
| AC-3.9 | Students who ran the explain gate rate it "useful" | ≥75% |

---

## 14. Build order

| Day | Task |
|---|---|
| 1 | GitHub OAuth (read-only), repo list, ranking heuristic |
| 2 | Sandboxed clone + caps + path sanitisation + snapshot to R2 |
| 3 | Inventory: exclusions, language detection, LOC, scaffold fingerprints |
| 4 | Commit log export + identity resolution UI |
| 5 | Contribution: additions share + blame share + range + cadence |
| 6 | Squashed-history detection and suppression logic |
| 7 | MinHash tutorial corpus build + `authored_confidence` |
| 8 | Module graph (Python + JS first) |
| 9 | Decision detectors (magic constants, hyperparameters, error handling first) |
| 10 | Architecture LLM pass + confirmable understanding card |
| 11 | Decision ranking + question ladder generation |
| 12 | Repo read-back UI (Screen 4) with syntax highlighting |
| 13 | Explain-your-own-code gate |
| 14 | Secret detection + private notice; no-repo path; safety scan in CI |

---

## 15. Open decisions

1. **Do we ever show contribution share to a placement officer?** Recommendation: **no, not in Year 1.** It is a number that will be misread as a character judgement by a non-technical reader. Revisit only with an explicit consent flow.
2. **Tutorial corpus sourcing** — building signatures from 2,000 public repos is fine for research use; confirm licence posture with the faculty advisor before shipping. We store MinHash signatures, not source, which helps.
3. **Do we support GitLab / Bitbucket?** Recommendation: not in Year 1. GitHub covers >95% of campus usage; the abstraction (`provider` field) is there for later.
4. **Should the explain gate be scored or coaching-only in Phase 1?** Proposal: coaching-only until we have 100 rated examples, then scored — consistent with the platform-wide validity rule.
5. **"Build this to prove it" projects** — who writes and maintains the three starter briefs? Proposal: E3 writes them in week 2; faculty reviews once.
