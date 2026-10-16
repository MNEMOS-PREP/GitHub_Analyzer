# M03 — TODO · GitHub Analyzer & Authorship Forensics

> **Completion: 0%** — 0 of 18 functional requirements met. **No repository.** The spec is written and, for several modules, reviewed; nothing is built. The requirement count is the spec's own, so this is a measure of scope ahead rather than of progress behind.
>
> _Method: `(met + ½ × partial) ÷ total`, counted from the spec, 2026-08-28. One measure applied the same way to all nineteen modules so they compare; it counts requirements, not screens or gates._

Task list for taking **M3 — GitHub Analyzer & Authorship Forensics** to 100% of [M03_GitHub_Analyzer.md](M03_GitHub_Analyzer.md).

> **This is the branch that holds the plan.** The `todo` branch carries the
> spec and this list and nothing else, so the module's own history stays
> about its code. Sync it here when something lands.

> **Read this first when starting a session on M03.** It is the running state of
> the module: what is done, what is next, and what is deliberately deferred.
> Update it as things land — a stale list is worse than no list, because the
> next session trusts it.

- **Repo:** `GitHub_Analyzer`

Legend: ✅ done · 🟡 partly done · ⬜ not started

---

## 0. Ideas and scouting

Things worth building that did not come from the spec — usually from reading
somebody else's implementation. **New ideas go at the top of this section.**

The rule we keep arriving at: borrow a **technique**, not a **component**. The
platform's contracts — character offsets, per-fact provenance, permanent
evidence — are more demanding than general-purpose tools assume, so integration
usually costs more than reimplementing the one idea we actually wanted.

_Nothing scouted for this module yet._

---

## 1. Where the module stands

| | |
|---|---|
| Status | **Not started.** No code exists — no backend, no frontend, no tests. |
| Spec | Written, and the source of every row below. |
| Scope | 18 functional requirements (15 of them P0), 9 acceptance metrics (3 release-blocking), 17 edge cases. |

Everything below is ⬜ because nothing is built. That is the honest state and
it is worth writing down rather than leaving three sections saying
_To fill in._ — the point of this list is that the next session can start from
it instead of re-reading a six-hundred-line spec.

---

## 2. Functional requirements

Spec §4, verbatim. This is the authoritative list.

| FR | | Priority |
|---|---|---|
| FR-3.1 | ⬜ Connect via GitHub OAuth (read-only, public repos) or accept a public URL | P0 |
| FR-3.2 | ⬜ Snapshot the repo at a pinned SHA into immutable storage | P0 |
| FR-3.3 | ⬜ Never execute repository code, at any stage | P0 |
| FR-3.4 | ⬜ Compute LOC excluding vendored, generated, and binary files | P0 |
| FR-3.5 | ⬜ Detect language/framework/dependency stack | P0 |
| FR-3.6 | ⬜ Resolve committer identity to the student, with a confirmation step | P0 |
| FR-3.7 | ⬜ Compute contribution share as a range with a stated method | P0 |
| FR-3.8 | ⬜ Detect fork/upstream and analyse only the student's diff | P1 |
| FR-3.9 | ⬜ Extract an architecture summary and a module graph | P0 |
| FR-3.10 | ⬜ Extract ≥5 interrogable decisions for a non-trivial repo | P0 |
| FR-3.11 | ⬜ Generate a code-grounded question per decision with a file:line citation | P0 |
| FR-3.12 | ⬜ Run the "explain your own code" gate on a function from a high-contribution file | P1 |
| FR-3.13 | ⬜ Link repos to resume claims (M1) and set `verifiability` | P1 |
| FR-3.14 | ⬜ **Never lower any score due to repo quality or absence** | P0 |
| FR-3.15 | ⬜ Never surface an authenticity/plagiarism verdict to the student | P0 |
| FR-3.16 | ⬜ Handle squashed history by reporting "cannot determine", not a fabricated number | P0 |
| FR-3.17 | ⬜ Complete p95 in ≤ 90 s for a median student repo | P0 |
| FR-3.18 | ⬜ Support the full no-repo path with an equivalent project round | P0 |

---

## 3. Acceptance gates

Spec §13. A **[GATE]** is release-blocking; the rest are tracked.

| AC | | Target |
|---|---|---|
| AC-3.1 **[GATE]** | ⬜ No authenticity/similarity value reachable from any client | verified by bundle + schema scan in CI |
| AC-3.2 **[GATE]** | ⬜ Repo absence or quality never changes a score | verified by design review + Phase-4 cohort check |
| AC-3.3 **[GATE]** | ⬜ Repository code is never executed | verified by sandbox audit |
| AC-3.4 | ⬜ Decisions extracted for non-trivial repos | ≥5 for 80% of them |
| AC-3.5 | ⬜ Every question cites a real, resolvable `file:line` | 100% |
| AC-3.6 | ⬜ Contribution range brackets manual ground truth | ≥80% of the 10-repo audit set |
| AC-3.7 | ⬜ Understanding paragraph accepted without correction | ≥70% |
| AC-3.8 | ⬜ p95 analysis time | ≤ 90 s |
| AC-3.9 | ⬜ Students who ran the explain gate rate it "useful" | ≥75% |

---

## 4. Edge cases

Spec §12. Listed in full, because several are one afternoon each and are the
difference between a demo and a product.

| EC | | Expected behaviour |
|---|---|---|
| EC-3.1 | ⬜ Empty repo | "Nothing to read here yet" + no-repo path |
| EC-3.2 | ⬜ Single "initial commit" with 3,000 files | `history_usable=false`; contribution suppressed; questions still generated from code |
| EC-3.3 | ⬜ Fork with 3 commits on top | Analyse the diff only; explicitly frame: "you contributed X to a fork of Y" |
| EC-3.4 | ⬜ 4 GB repo with datasets | Cap at 500 MB; skip large files; note "I skipped the data files" |
| EC-3.5 | ⬜ Notebook-only repo | `nbformat` source-cell extraction; outputs stripped |
| EC-3.6 | ⬜ Private repo | Not accessible with our scope; offer URL+zip upload path or skip |
| EC-3.7 | ⬜ Repo deleted after snapshot | Snapshot is ours; analysis continues; live links disabled |
| EC-3.8 | ⬜ Student pushes mid-session | Session uses the pinned SHA; a note offers re-analysis after |
| EC-3.9 | ⬜ 40 contributors (a class project) | Contribution range widened; questions restricted to their directories |
| EC-3.10 | ⬜ All code is scaffold | "This is mostly generated setup — tell me what you added" + design questions |
| EC-3.11 | ⬜ Language we can't parse (Rust, Go, Kotlin) | Fall back to regex detectors + LLM reading; note reduced depth |
| EC-3.12 | ⬜ Committed secrets | Redact, private notice, never in a question |
| EC-3.13 | ⬜ Repo contains someone else's PII | Detect + do not include in any prompt or UI |
| EC-3.14 | ⬜ Repo URL is not a repo (gist, org, user) | Named error + suggestion |
| EC-3.15 | ⬜ GitHub rate limit hit | Queue with backoff; user sees "GitHub is busy, I'll keep trying" |
| EC-3.16 | ⬜ Zip-bomb / symlink escape | Rejected at clone stage; logged to `security_events` |
| EC-3.17 | ⬜ Student has 200 repos | Rank, show top 8, search box for the rest |

---

## 5. Data model

Spec §5 declares 6 types. None exist.

| Type | | |
|---|---|---|
| `RepoSnapshot` | ⬜ | Not written |
| `CodeInventory` | ⬜ | Not written |
| `ContributionAnalysis` | ⬜ | Not written |
| `SimilarityFinding` | ⬜ | Not written |
| `CodeDecision` | ⬜ | Not written |
| `RepoQuestion` | ⬜ | Not written |

---

## 6. Deferred — inter-module

Not gaps in this module. Each needs a module that is not built or not wired
yet, and each is listed so it is not mistaken for an oversight.

The spec names **M13, M19**. Until those are wired, anything
depending on them is blocked rather than unstarted, and the distinction
matters: one is work waiting to be done and the other is work that cannot
begin.

---

## 7. First moves

When work begins, the order that has worked on M01 and M02:

1. **The manifest and the ports.** `module.json` with `depends_on` declaring
   every upstream as OPTIONAL, each with an `on_missing` sentence a student
   can act on. Rule 2: a missing dependency narrows scope, never quality, and
   never silently.
2. **Identity before endpoints.** Router-level `enforce_access` on
   `ai_core.identity`, so no route can be written without it. A per-route
   check is the one somebody omits on the sixteenth endpoint, and the
   sixteenth endpoint is the one that leaks.
3. **The P0 that carries the module's name**, not the easiest one.
4. **Contract gates from the first commit** — every route has a client, every
   hook has a screen, every response field is declared on the frontend. M01
   shipped ten routes nobody could reach before these existed.
5. **A golden corpus before the third extraction fix**, not after. On M02 it
   found four real bugs within an hour of existing.

