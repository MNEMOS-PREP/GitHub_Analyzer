# Context Layer and Diagrams Implementation Plan (phase 1 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the analyzer a model-free, token-bounded repo map and auto-generated diagrams (Mermaid + JSON graph) that Archify can render.

**Architecture:** Expose the import *edges* the analyzer already walks (today only in-degree counts survive), build a Tier 0 structural index from them, derive a capped Tier 1 text map and a diagram graph from Tier 0, and add them to the pipeline output behind a new `visualise` stage. No LLM is called anywhere in this plan. Phases 2 (PR agent) and 3 (conflict agent) get their own plans and consume Tier 0/1 from here.

**Tech Stack:** Python 3.12, `ast`, existing `ts_detect`/`modgraph` (tree-sitter optional), pytest, FastAPI (existing `api.py`).

**Spec:** `docs/superpowers/specs/2026-11-18-visualize-and-agents-design.md`

## Global Constraints

- Analyzer core stays model-free and never executes repo code (M03 guarantee). Read files as text only.
- A bad or missing repo never changes a score; nothing here touches scoring or `ranking.select_top` results.
- No student-facing text says *plagiarism*, *copied* or *original*.
- Every diagram edge comes from the static import graph; never inferred. Missing an edge is acceptable, inventing one is not.
- Languages without a real graph (regex-fallback) get a tree-only diagram, labelled as such in the output `notes`.
- Tier 1 map is hard-capped at `TIER1_BUDGET = 3000` tokens; token estimate is `ceil(len(text) / 4)`.
- Diagram size caps: `DIAGRAM_TOP_N = 25` files by in-degree, so output size does not grow with repo size.
- Cache key is the pinned commit SHA; unchanged SHA means no rebuild.
- Build in `GitHub_Analyzer/`; run tests from that folder with `python -m pytest <path> -q`.
- This folder is **not under version control**. Wherever a task says "checkpoint", run the full suite (`python -m pytest -q`, about 2 minutes) instead of committing. Recommend `git init` first if you want commits.

## Review Focus

1. Empty repo or zero analysed files: diagrams are empty with a note, no crash.
2. Very large repo (thousands of files): output is capped at `DIAGRAM_TOP_N` nodes and `TIER1_BUDGET` tokens.
3. File paths with quotes, brackets, spaces or unicode: Mermaid stays valid (IDs sanitised, labels escaped).
4. A Python file with a syntax error: skipped for signatures and edges, no crash, no invented edge.
5. Mostly regex-fallback languages (e.g. C#, Rust) or notebooks: tree-only diagram plus a note saying so.

## File Structure

- Modify `gh_analyzer/ranking.py`: split `import_graph` into `import_edges` + in-degree derivation (behaviour unchanged).
- Modify `gh_analyzer/modgraph.py`: add `edges`, derive `in_degree` from it (behaviour unchanged).
- Create `gh_analyzer/repomap.py`: Tier 0 index (tree, signatures, entry points, edges).
- Create `gh_analyzer/context.py`: Tier 1 capped text map, token estimate, SHA-keyed cache.
- Create `gh_analyzer/visualise.py`: graph JSON and Mermaid generation.
- Modify `gh_analyzer/pipeline.py`, `service.py`, `api.py`, `__main__.py`: wire in the stage, endpoint and CLI flag.
- Tests: `tests/test_import_edges.py`, `tests/test_repomap.py`, `tests/test_context.py`, `tests/test_visualise.py`, `tests/test_pipeline_visualise.py`.

---

### Task 1: Expose import edges

**Files:**
- Modify: `gh_analyzer/ranking.py:41` (`import_graph`), `gh_analyzer/modgraph.py` (`in_degree`)
- Test: `tests/test_import_edges.py`

**Interfaces:**
- Produces: `ranking.import_edges(root: Path, analysed: list[str]) -> set[tuple[str, str]]` returning `(importer, imported)` relative paths, both inside `analysed`, never self-edges. `modgraph.edges(root, analysed) -> set[tuple[str, str]]` with the same contract for JS/TS/Java/Go. `ranking.import_graph` and `modgraph.in_degree` keep their current signatures and results.

- [ ] **Step 1: Write the failing tests** in `tests/test_import_edges.py`: `test_python_edges_relative_and_absolute_imports` (files `a.py` doing `import pkg.b`, `pkg/b.py`, `pkg/c.py` doing `from . import b`; assert `{("a.py","pkg/b.py"),("pkg/c.py","pkg/b.py")}`), `test_js_edges_resolve_only_local` (`import x from "./util"` gives an edge, `import "react"` does not), `test_in_degree_unchanged` (on a fixture repo, `import_graph` equals counts derived from `import_edges`), `test_syntax_error_file_yields_no_edge`.
- [ ] **Step 2: Run** `python -m pytest tests/test_import_edges.py -q`. Expected: FAIL (`import_edges` not defined).
- [ ] **Step 3: Implement** `import_edges` by moving the loop body of `ranking.import_graph` so it adds `(rel, t)` pairs instead of incrementing counts; reimplement `import_graph` as in-degree over `import_edges | modgraph.edges(...)`. In `modgraph`, do the same for `in_degree`. Keep the exact resolution rules already in the file.
- [ ] **Step 4: Run** the new tests plus `tests/test_snapshot_inventory.py` and `tests/test_ts_detect.py`. Expected: PASS (proves no behaviour change).
- [ ] **Step 5: Checkpoint** full suite.

### Task 2: Tier 0 repo index

**Files:**
- Create: `gh_analyzer/repomap.py`
- Test: `tests/test_repomap.py`

**Interfaces:**
- Consumes: `ranking.import_edges`, `modgraph.edges`, `inventory.Inventory.analysed`.
- Produces:
  - `@dataclass Symbol(name: str, kind: str, line: int, signature: str)` where `kind` is `"function" | "class" | "method"`.
  - `@dataclass Tier0(files: list[str], edges: set[tuple[str, str]], in_degree: dict[str, int], symbols: dict[str, list[Symbol]], entry_points: list[str], languages: dict[str, int], graph_quality: str)` with `graph_quality` one of `"full"`, `"partial"` (some languages regex-only), `"tree_only"`.
  - `build_tier0(root: Path, analysed: list[str], languages: dict[str, int]) -> Tier0`.

- [ ] **Step 1: Write failing tests**: `test_signatures_python` (def with args and annotations, class, method; assert exact `signature` strings like `"def load(path: str) -> dict"`), `test_entry_points` (`__main__.py`, a file with `if __name__ == "__main__"`, `main.py`, `app.py`, `index.js`), `test_syntax_error_skipped`, `test_graph_quality_tree_only_for_rust_only_repo`, `test_in_degree_matches_edges`.
- [ ] **Step 2: Run** `python -m pytest tests/test_repomap.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `build_tier0`. Python symbols by `ast` (signature rendered from the node, body omitted). JS/TS/Java/Go: reuse the regex/tree-sitter availability from `ts_detect`; if unavailable, no symbols for that language and `graph_quality` becomes `"partial"`. `"tree_only"` when no analysed file is in a graphed language. Never raise on one bad file.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Checkpoint** full suite.

### Task 3: Tier 1 capped map and SHA cache

**Files:**
- Create: `gh_analyzer/context.py`
- Test: `tests/test_context.py`

**Interfaces:**
- Consumes: `repomap.Tier0`.
- Produces:
  - Constants `TIER1_BUDGET = 3000`, `TOP_FILES = 10`, `SIGS_PER_FILE = 12`.
  - `estimate_tokens(text: str) -> int` (`ceil(len(text) / 4)`).
  - `build_tier1(t0: Tier0, readme: str, budget: int = TIER1_BUDGET) -> tuple[str, int]` returning `(map_text, tokens)` with `tokens <= budget` always.
  - `cached(cache_dir: Path, sha: str, kind: str, builder: Callable[[], dict]) -> tuple[dict, bool]` returning `(value, was_cache_hit)`; value is JSON on disk at `cache_dir/<sha>/<kind>.json`.

- [ ] **Step 1: Write failing tests**: `test_budget_never_exceeded` (synthetic Tier0 with 500 files and 50 symbols each, assert `tokens <= 3000`), `test_truncation_order` (assert signatures per file shrink to 6 first, then tree depth to 2, then README to 600 chars, then lowest in-degree files are dropped; assert the highest in-degree file survives everything), `test_estimate_tokens`, `test_cache_hit_skips_builder` (builder call count 1 after two calls, second returns `True`), `test_cache_corrupt_file_rebuilds`.
- [ ] **Step 2: Run** `python -m pytest tests/test_context.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** both functions. Map layout: stack line, README excerpt, indented tree, then the top `TOP_FILES` files by in-degree each with up to `SIGS_PER_FILE` signatures. Apply the four truncation steps in the order the test asserts, re-measuring after each, until under budget. `cached` writes atomically (temp file then replace) and treats unreadable JSON as a miss.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Checkpoint** full suite.

### Task 4: Diagram graph and Mermaid

**Files:**
- Create: `gh_analyzer/visualise.py`
- Test: `tests/test_visualise.py`

**Interfaces:**
- Consumes: `repomap.Tier0`.
- Produces:
  - `DIAGRAM_TOP_N = 25`.
  - `build_graph(t0: Tier0, top_n: int = DIAGRAM_TOP_N) -> dict` with `{"nodes": [{"id", "path", "group", "in_degree", "entry": bool}], "edges": [{"from", "to"}], "quality": str, "truncated": bool}`; nodes are the top-N by in-degree plus entry points, edges only between kept nodes.
  - `to_mermaid(graph: dict, kind: str) -> str` for `kind` in `"modules"` (nodes grouped by top-level folder as subgraphs), `"imports"` (flat file graph), `"entry_flow"` (entry points and what they import, depth 2). Raises `ValueError` on any other kind.
  - `diagrams(t0: Tier0) -> dict` returning `{"graph": <build_graph>, "mermaid": {"modules": str, "imports": str, "entry_flow": str}, "notes": list[str]}`.

- [ ] **Step 1: Write failing tests**: `test_edges_only_from_import_graph` (every edge in output is in `t0.edges`), `test_top_n_cap_and_truncated_flag` (300 synthetic files give at most 25 nodes plus entries, `truncated` true), `test_mermaid_ids_sanitised` (paths `a "b".py`, `x[1].py`, `ünï.py` produce IDs matching `^[A-Za-z0-9_]+$` and escaped labels; also assert no raw `"` inside a label), `test_empty_repo` (no files gives empty graph and a note, all three Mermaid strings are valid minimal `flowchart LR`), `test_tree_only_note` (`quality == "tree_only"` adds the note and modules diagram still renders from folders), `test_unknown_kind_raises`.
- [ ] **Step 2: Run** `python -m pytest tests/test_visualise.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** with `flowchart LR` output. ID = `n` + index of node in sorted order, label = escaped path (replace `"` with `#quot;`, strip brackets). Do not add any edge that is not in `t0.edges`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Checkpoint** full suite.

### Task 5: Wire into pipeline, API and CLI

**Files:**
- Modify: `gh_analyzer/pipeline.py` (`analyse_snapshot`, after `inventory` stage), `gh_analyzer/service.py`, `gh_analyzer/api.py`, `gh_analyzer/__main__.py`, `README.md`
- Test: `tests/test_pipeline_visualise.py`

**Interfaces:**
- Consumes: `repomap.build_tier0`, `context.build_tier1`, `context.cached`, `visualise.diagrams`.
- Produces:
  - `result["student"]["diagrams"]` = the `visualise.diagrams` dict plus `"repo_map_tokens": int`.
  - `result["private"]["audit"]["context"]` = `{"tier1_tokens": int, "cache_hit": bool}` and a `"visualise"` entry in `timings_s`.
  - `AnalyzerService.diagrams(rec: Record) -> dict` and `GET /v1/repos/{repo_id}/diagrams` returning `{"diagrams": ...}` under the same auth as `/decisions`.
  - CLI flag `--mermaid DIR` writing `modules.mmd`, `imports.mmd`, `entry_flow.mmd` and `graph.json` into DIR.

- [ ] **Step 1: Write failing tests**: `test_result_has_diagrams` (run `analyse_repo` on a `tmp_repo` fixture with 3 Python files importing each other; assert `diagrams.graph.edges` is non-empty and `repo_map_tokens <= 3000`), `test_scoring_outputs_unchanged` (decisions and questions identical with and without the new stage, by comparing to a run where `visualise` is monkeypatched to raise and is caught), `test_visualise_failure_does_not_fail_analysis` (monkeypatch `visualise.diagrams` to raise; assert result `status == "ok"`, `diagrams` is an empty graph and a note says diagrams were unavailable), `test_cache_second_run_hits` (second call on same SHA reports `cache_hit True`), `test_endpoint_diagrams` (using the existing API test client pattern in `tests/test_service_api.py`), `test_cli_mermaid_flag_writes_files`.
- [ ] **Step 2: Run** `python -m pytest tests/test_pipeline_visualise.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the stage in `analyse_snapshot` using `stages.done("visualise")`. Wrap in `try/except Exception` so a diagram failure only adds a note (spec rule: nothing here may fail or change an analysis). Cache dir is `<workdir>/.context_cache` when a workdir is given, otherwise skip caching. Add the service method and route next to `decisions`, and the CLI flag in `__main__.py`. Add a short "Diagrams" section to `README.md` covering how to render the `.mmd` files with Archify.
- [ ] **Step 4: Run** `python -m pytest tests/test_pipeline_visualise.py tests/test_service_api.py -q`. Expected: PASS.
- [ ] **Step 5: Checkpoint** full suite, then run `python -m gh_analyzer . --mermaid out_diagrams` on this repo and open `out_diagrams/imports.mmd` through Archify to confirm it renders. Expected: a readable graph of at most 25 files.
