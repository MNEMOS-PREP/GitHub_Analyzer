# M03 extension: codebase visualisation and helper agents — design

Status: APPROVED by the user on 2026-11-18, including the "My calls" section. Archify hand-off decided: option (a), the analyzer emits Mermaid + JSON and Archify renders it; no in-product renderer for now.

## Intent and why

M03 turns a student's repo into interview questions. Two additions:

1. **See your codebase.** Auto-generated diagrams (architecture, module/import graph, entry-point flow) so a student can understand and explain their own project. Archify renders them.
2. **Helper agents** (a PR agent, then a merge-conflict agent) that help a student with the git workflow around their repo.

Both must stay cheap in tokens: neither README-only (shallow, reflects what the author says) nor whole-repo (expensive, breaks on large repos).

## Goals

- A student gets accurate diagrams of their own repo with zero model cost.
- Agents read only what a task needs, and report the tokens they used.
- Every diagram edge and every agent claim traces to evidence (a file, a line, a diff hunk).
- Re-running on an unchanged commit costs nothing.

## Non-goals

- No verdicts, no scoring, no effect on any score (M03's absolute rule).
- Agents never push, merge, force or write to GitHub on their own. They produce suggestions and patch text the student applies.
- No executing repo code, ever.
- No new analysis of authorship or quality.

## Anti-goals (failure even if it technically works)

- A diagram that invents or guesses an edge.
- An agent that does the student's thinking for them with no explanation (see "Open assumption").
- Token use that grows with repo size.
- Any student-facing wording like *plagiarism*, *copied*, *original*.

## Constraints

- M03 guarantees hold for the analyzer core: model-free, never executes repo code, secrets only as a private notice. Agents live in a **separate, opt-in package** so the core's guarantees stay literally true.
- Python 3.12. Build in `GitHub_Analyzer/` (the fuller copy; `github-analyzer/` looks like an older one).
- Reuse the existing pipeline: `inventory.py`, `modgraph.py`, `ranking.import_graph`, `ts_detect.py`, `service.py`/`api.py`.
- Reuse the pattern from `repo-agent/ghagent` (numbered evidence E1..En, findings must cite evidence, separate verifier). Do not modify `repo-agent`.

## Approaches considered

| Option | Trade-off | Verdict |
|---|---|---|
| README only | Cheapest; describes claims, not code; shallow diagrams, guessing agents | Rejected |
| Whole repo to the model | Accurate; expensive, slow, fails on big repos | Rejected |
| **Tiered, static first** | Needs a good Tier 0, which M03 mostly has already | **Chosen by the user** |

## Design: tiered context

- **Tier 0 (free, no model):** file tree, stack, import graph with centrality, entry points, function/class signatures (`ast`; tree-sitter for JS/TS/Java/Go). Built mostly from existing stages; new: signatures and entry points.
- **Tier 1 (cheap, bounded):** one "repo map" of a few thousand tokens: README + tree + top ~10 files by centrality + signatures only. Hard token cap, truncation order documented.
- **Tier 2 (on demand):** agents fetch full file bodies or diff hunks only when a task needs them, through budgeted tools (max chars per read, max reads per run).
- **Cache** keyed by commit SHA for Tier 0/1 and for diagram output.
- Every run records tokens used per tier.

## Design: visualisation (build first, no model)

- New pipeline stage `visualise` consumes Tier 0 and emits a graph (JSON) plus Mermaid text.
- Diagrams: architecture/module map, import graph (top-N by centrality so it stays readable), entry-point flow. Each edge comes from the static import graph, never inferred.
- Archify turns that into the explorable standalone HTML and SVG/image export. The result is added to the analysis output as an artifact the frontend can show.
- Languages without a graph (the regex-fallback ones) get a tree-only diagram, labelled as such, matching how M03 already says so in its output.

## Design: PR agent (phase 2)

- Input: a PR or branch diff plus the Tier 1 map. Tier 2 reads only touched files and direct callers.
- Output: a plain-language summary of what changed and why it might matter, a checklist of things to review, and suggested PR title/description. Findings cite diff hunks; a verifier drops any unsupported claim (same pattern as `ghagent`).
- Read-only on GitHub. No comments posted unless the student copies them.

## Design: conflict agent (phase 3)

- Input: conflicted hunks only, plus the surrounding function and callers via Tier 0/2.
- Output: for each hunk, what each side intended, a suggested resolution as patch text, and why. The student applies it.
- Never writes to the working tree or runs git commands that change state.

## Parts decided by the user

- Option 3 (tiered, static first).
- Visualisation via Archify; PR agent and conflict agent as the helper agents.

## My calls (not yet confirmed — please check)

- Student-facing, **teach-first**: agents explain and suggest; the student does the action.
- Agents are a separate opt-in package inside `GitHub_Analyzer/`, not a change to the analyzer core.
- Build order: context layer + diagrams, then PR agent, then conflict agent.
- Existing `repo-agent` stays untouched; its pattern is borrowed.

## Left to the builder

- Exact token caps per tier and truncation order.
- Mermaid vs direct JSON as the hand-off to Archify (see open question).
- Frontend placement of the diagram view.
- Test strategy: golden-graph tests for diagrams on fixture repos; agent tests with a fake LLM client.

## Open question for the user

Archify is a Claude skill, not a library. For "automation" at runtime the analyzer needs a way to call it, or to produce its input so it can be rendered. Either (a) the analyzer emits Mermaid/JSON and you render it with Archify when you want diagrams, or (b) we ship a small renderer inside the product. (a) is quick; (b) is more work but makes it a real in-product feature.
