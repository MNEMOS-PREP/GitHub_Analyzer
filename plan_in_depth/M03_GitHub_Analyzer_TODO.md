# M03 — TODO · GitHub Analyzer & Authorship Forensics

> **Completion: ~89%** — 14 of 18 functional requirements met, 4 partly (`(14 + ½ × 4) ÷ 18`), counted from the spec on 2026-10-22. The analysis pipeline, the HTTP service and the safety gates are built and tested (174 tests). **What is left is not code:** the front-end, a database and object store, and every number that needs real students' repositories.
>
> _Method: `(met + ½ × partial) ÷ total`, counted from the spec. One measure applied the same way to all nineteen modules so they compare; it counts requirements, not screens or gates. It is generous to this module in one way: FR-3.18 is "met" on the back end only, because there is no screen yet._

Task list for taking **M3 — GitHub Analyzer & Authorship Forensics** to 100% of [M03_GitHub_Analyzer.md](M03_GitHub_Analyzer.md).

> **Read this first when starting a session on M03.** It is the running state of
> the module: what is done, what is next, and what is deliberately deferred.
> Update it as things land — a stale list is worse than no list, because the
> next session trusts it. (This one said "0%, no code" for six weeks after the
> code existed. Do not let that happen again.)

- **Repo:** `GitHub_Analyzer`
- **Tests:** `python -m pytest -q` — 174 passed, 1 skipped (a symlink test that needs Windows permission), about 3 minutes.

Legend: ✅ done · 🟡 partly done · ⬜ not started

---

## 0. Ideas and scouting

Things worth building that did not come from the spec. **New ideas go at the top of this section.**

The rule we keep arriving at: borrow a **technique**, not a **component**. The
platform's contracts — character offsets, per-fact provenance, permanent
evidence — are more demanding than general-purpose tools assume, so integration
usually costs more than reimplementing the one idea we actually wanted.

- **Decisions inside notebooks.** A `.ipynb` is JSON, so a `file:line` citation into it does not resolve to anything a student can open. Citing `notebook.ipynb:cell 7` needs a different citation shape through `questions.py` and the UI. Worth doing, not a one-afternoon job.

---

## 1. Where the module stands

| | |
|---|---|
| Status | **Built and tested; not yet validated on real student repositories, and no UI.** |
| Pipeline | snapshot → inventory → contribution (with forks) → decisions (Python by `ast`, JS/TS/Java/Go by tree-sitter) → ranking → questions, plus secrets, reference matching, resume link, explain gate, no-repo round. No model is used anywhere. |
| Service | `service.py` keeps a SHA-pinned snapshot per repo until deleted, caches per commit, and ends every failure in the no-repo path. `api.py` serves the spec's section 7 routes. |
| Branches | The code was split across two branches that did not share history (`feature/m03-updates` had the core modules, `todo` had the tests and later modules). They are combined on `m03-complete`. |

### What is honestly not done

| Gap | Why it matters |
|---|---|
| **No front-end** | Spec section 8 lists nine components. None exist. FR-3.18 and FR-3.12 work as API responses only. |
| **No database or object store** | Records are in memory and snapshots are on local disk. A restart loses every record. The spec's R2 storage is not wired. |
| **GitHub sign-in never run against GitHub** | The flow is tested against a stub transport. It needs a registered OAuth app and one real sign-in to be called done. |
| **No real-data validation** | AC-3.4, 3.6, 3.7, 3.9 need the 30-repo corpus, the 10-repo audit set, and rated sessions. The code records what those need (`service.metrics()`), but nobody has run them. |
| **Zip upload for private repos** | EC-3.6 names the path and the API offers it (`upload_zip`), but no upload route exists. |
| **No `security_events` log** | EC-3.16 refuses the repo and says why to the student; the spec also wants it logged. |

---

## 2. Functional requirements

Spec §4. This is the authoritative list.

| FR | | Priority | Where / what is left |
|---|---|---|---|
| FR-3.1 | 🟡 Connect via GitHub OAuth (read-only, public repos) or accept a public URL | P0 | `intake.py`, `api.py`. URL path is real (it clones). OAuth asks for no scope and is tested against a stub only |
| FR-3.2 | 🟡 Snapshot the repo at a pinned SHA into immutable storage | P0 | `snapshot.py`, `service.py`. Pinned and kept until deleted. "Immutable storage" is a local folder, not R2 |
| FR-3.3 | ✅ Never execute repository code, at any stage | P0 | Static scan in CI (`test_AC_3_3_*`) plus runtime tests: only `git` is ever run |
| FR-3.4 | ✅ Compute LOC excluding vendored, generated, and binary files | P0 | `inventory.py` |
| FR-3.5 | ✅ Detect language/framework/dependency stack | P0 | `inventory.py` |
| FR-3.6 | ✅ Resolve committer identity to the student, with a confirmation step | P0 | `identity.py`; no share exists until the student confirms; a typo is refused, not read as 0% |
| FR-3.7 | ✅ Compute contribution share as a range with a stated method | P0 | `contribution.py` |
| FR-3.8 | ✅ Detect fork/upstream and analyse only the student's diff | P1 | `forks.py` |
| FR-3.9 | 🟡 Extract an architecture summary and a module graph | P0 | Module graph and a deterministic file map: yes. The model-written paragraph: deliberately not built (no model) |
| FR-3.10 | ✅ Extract ≥5 interrogable decisions for a non-trivial repo | P0 | Fewer than five is topped up with resume and design questions, never shortened. The 80% target (AC-3.4) is unmeasured |
| FR-3.11 | ✅ Generate a code-grounded question per decision with a file:line citation | P0 | `questions.py`; a citation that does not resolve drops the decision |
| FR-3.12 | ✅ Run the "explain your own code" gate on a function from a high-contribution file | P1 | `explain_gate.py`. Coaching only, as the spec's decision 4 says. Offered only for a function that blame says is mostly the student's |
| FR-3.13 | ✅ Link repos to resume claims (M1) and set `verifiability` | P1 | `resume_link.py`. Values: `supported`, `partly_supported`, `not_checkable`. There is no "contradicted" |
| FR-3.14 | ✅ **Never lower any score due to repo quality or absence** | P0 | The module emits no score. Every object that could touch one carries `score_effect: 0` / `affects_score: false` and a test pins it |
| FR-3.15 | ✅ Never surface an authenticity/plagiarism verdict to the student | P0 | `guard.py` fails closed on any payload with a forbidden key or banned word |
| FR-3.16 | ✅ Handle squashed history by reporting "cannot determine", not a fabricated number | P0 | `contribution.py` |
| FR-3.17 | 🟡 Complete p95 in ≤ 90 s for a median student repo | P0 | Per-stage timings are recorded. A 60-commit synthetic repo analyses in a few seconds locally. Not measured with a real clone over the network, or on real repos |
| FR-3.18 | ✅ Support the full no-repo path with an equivalent project round | P0 | `no_repo.py`: same four-step ladder, professor-style, plus three 6-hour starter builds. Back end only |

---

## 3. Acceptance gates

Spec §13. A **[GATE]** is release-blocking; the rest are tracked.

| AC | | Target | State |
|---|---|---|---|
| AC-3.1 **[GATE]** | ✅ No authenticity/similarity value reachable from any client | verified by bundle + schema scan in CI | Source scan, payload guard on every route, and the API module cannot import the matcher. There is no front-end bundle yet, so the bundle half applies when one exists |
| AC-3.2 **[GATE]** | 🟡 Repo absence or quality never changes a score | design review + Phase-4 cohort check | The design half is pinned by tests. The cohort check needs real data |
| AC-3.3 **[GATE]** | ✅ Repository code is never executed | sandbox audit | Static scan plus runtime tests. A full sandbox audit by someone else is still worth doing before release |
| AC-3.4 | ⬜ Decisions extracted for non-trivial repos | ≥5 for 80% of them | Needs the 20 non-trivial repos |
| AC-3.5 | 🟡 Every question cites a real, resolvable `file:line` | 100% | 100% on every test repo, enforced in code. Not run on a corpus |
| AC-3.6 | ⬜ Contribution range brackets manual ground truth | ≥80% of the 10-repo audit set | Needs the audit set |
| AC-3.7 | 🟡 Understanding paragraph accepted without correction | ≥70% | Recorded by `/understanding/confirm`; no data |
| AC-3.8 | 🟡 p95 analysis time | ≤ 90 s | Recorded per run; measured only locally |
| AC-3.9 | 🟡 Students who ran the explain gate rate it "useful" | ≥75% | Recorded by `/explain-gate/rating`; no data |

---

## 4. Edge cases

Spec §12.

| EC | | Expected behaviour | State |
|---|---|---|---|
| EC-3.1 | ✅ Empty repo | "Nothing to read here yet" + no-repo path | Tested |
| EC-3.2 | ✅ Single "initial commit" with 3,000 files | `history_usable=false`; contribution suppressed; questions still generated | Tested |
| EC-3.3 | ✅ Fork with 3 commits on top | Analyse the diff only; frame as "you contributed X to a fork of Y" | Tested |
| EC-3.4 | ✅ 4 GB repo with datasets | Cap at 500 MB; skip large files; say so | In `snapshot.py` / `inventory.py` |
| EC-3.5 | 🟡 Notebook-only repo | `nbformat` source-cell extraction | Lines are counted; decisions are not extracted (see section 0) |
| EC-3.6 | 🟡 Private repo | Not accessible with our scope; offer URL+zip upload path or skip | Named error and offers; no zip-upload route |
| EC-3.7 | 🟡 Repo deleted after snapshot | Snapshot is ours; analysis continues | The snapshot is kept; no test deletes the upstream |
| EC-3.8 | ✅ Student pushes mid-session | Session uses the pinned SHA | Tested: confirming identity re-reads the snapshot, same SHA |
| EC-3.9 | 🟡 40 contributors (a class project) | Contribution range widened; questions restricted to their directories | Other contributors are counted and questions follow blame; widening is not separately tested |
| EC-3.10 | ⬜ All code is scaffold | "This is mostly generated setup — tell me what you added" | `scaffold_ratio` is not computed |
| EC-3.11 | ✅ Language we can't parse (Rust, Go, Kotlin) | Fall back to regex detectors; note reduced depth | Tested; the output says it |
| EC-3.12 | ✅ Committed secrets | Redact, private notice, never in a question | Tested, including the explain-gate code |
| EC-3.13 | ⬜ Repo contains someone else's PII | Detect + do not include in any prompt or UI | Not built |
| EC-3.14 | ✅ Repo URL is not a repo (gist, org, user) | Named error + suggestion | Tested through the API |
| EC-3.15 | ✅ GitHub rate limit hit | Queue with backoff; "GitHub is busy, I'll keep trying" | Tested: waits, retries, and gives a named error if it never clears |
| EC-3.16 | 🟡 Zip-bomb / symlink escape | Rejected at clone stage; logged to `security_events` | Rejected and tested; not logged |
| EC-3.17 | ✅ Student has 200 repos | Rank, show top 8, search box for the rest | Tested |

---

## 5. Data model

Spec §5 declares 6 types. They exist as dataclasses, not Pydantic models.

| Type | | |
|---|---|---|
| `RepoSnapshot` | ✅ | `snapshot.Snapshot`. No `candidate_id` / `storage_uri` UUID record: `service.Record` carries them |
| `CodeInventory` | ✅ | `inventory.Inventory`. `scaffold_ratio` is always `None` |
| `ContributionAnalysis` | ✅ | `contribution.Contribution` |
| `SimilarityFinding` | 🟡 | `reference.py`; internal only, by design, and never serialised to a client |
| `CodeDecision` | ✅ | `decisions.Decision` |
| `RepoQuestion` | ✅ | `questions.RepoQuestion` |

---

## 6. Deferred — inter-module

Not gaps in this module. Each needs a module that is not built or not wired
yet, and each is listed so it is not mistaken for an oversight.

The spec names **M13, M19**. Until those are wired, anything depending on them
is blocked rather than unstarted. M1 (resume) claims arrive as plain
`resume_claims` / `resume_titles` fields on `POST /v1/repos`; the real M1 feed
is not connected.

---

## 7. What to do next

In this order, because each unlocks the next:

1. **Register a GitHub OAuth app and sign in once for real.** Closes FR-3.1.
2. **Put the service on a database and the object store.** Today a restart loses every record. Closes FR-3.2 and the "Immutable storage" half of EC-3.7.
3. **Build the front-end** (spec section 8), starting with `RepoPicker`, `UnderstandingCard` and `NoRepoPath`. Then the bundle half of AC-3.1 becomes checkable.
4. **Collect the 30 consented repos and the 10-repo audit set.** This is the only way to close AC-3.4, 3.5, 3.6 and to find out whether "half to two-thirds of decisions are worth asking" survives a second rater.
5. **Smaller items:** zip upload route, `security_events` log, scaffold detection (EC-3.10), PII detection (EC-3.13), notebook decisions.
