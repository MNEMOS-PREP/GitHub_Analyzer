"""Tier 0 repo index: tree, signatures, entry points, import edges, graph quality."""

from __future__ import annotations

from pathlib import Path

import pytest

from gh_analyzer import ranking, repomap, ts_detect


def _write(root: Path, files: dict[str, str]) -> list[str]:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return sorted(files)


def test_signatures_python(tmp_path):
    analysed = _write(tmp_path, {"m.py": (
        "def load(path: str) -> dict:\n    return {}\n\n"
        "async def fetch(url, retries=3):\n    pass\n\n"
        "class Store(Base):\n    def get(self, key: str) -> int:\n        return 1\n")})
    t = repomap.build_tier0(tmp_path, analysed, {"Python": 9})
    sigs = {(s.name, s.kind, s.line): s.signature for s in t.symbols["m.py"]}
    assert sigs[("load", "function", 1)] == "def load(path: str) -> dict"
    assert sigs[("fetch", "function", 4)] == "async def fetch(url, retries=3)"
    assert sigs[("Store", "class", 7)] == "class Store(Base)"
    assert sigs[("get", "method", 8)] == "def get(self, key: str) -> int"


def test_entry_points(tmp_path):
    analysed = _write(tmp_path, {
        "pkg/__main__.py": "x = 1\n",
        "run.py": 'if __name__ == "__main__":\n    pass\n',
        "main.py": "x = 1\n",
        "web/app.py": "x = 1\n",
        "web/index.js": "export const a = 1;\n",
        "lib.py": "x = 1\n"})
    t = repomap.build_tier0(tmp_path, analysed, {})
    assert t.entry_points == ["main.py", "pkg/__main__.py", "run.py", "web/app.py", "web/index.js"]


def test_syntax_error_skipped(tmp_path):
    analysed = _write(tmp_path, {"bad.py": "def broken(:\n", "ok.py": "import bad\ndef f():\n    pass\n"})
    t = repomap.build_tier0(tmp_path, analysed, {"Python": 3})
    assert t.symbols.get("bad.py", []) == []
    assert [s.name for s in t.symbols["ok.py"]] == ["f"]
    assert ("ok.py", "bad.py") in t.edges          # imports resolve by name; the bad file itself yields none
    assert not any(a == "bad.py" for a, _ in t.edges)


def test_unreadable_file_does_not_raise(tmp_path):
    t = repomap.build_tier0(tmp_path, ["missing.py"], {"Python": 1})
    assert t.files == ["missing.py"] and t.symbols.get("missing.py", []) == []


def test_graph_quality_tree_only_for_rust_only_repo(tmp_path):
    analysed = _write(tmp_path, {"src/main.rs": "fn main() {}\n", "src/lib.rs": "pub fn f() {}\n"})
    t = repomap.build_tier0(tmp_path, analysed, {"Rust": 2})
    assert t.graph_quality == "tree_only" and t.edges == set()


def test_graph_quality_full_and_partial(tmp_path):
    analysed = _write(tmp_path, {"a.py": "x = 1\n", "b.rs": "fn b() {}\n"})
    assert repomap.build_tier0(tmp_path, ["a.py"], {"Python": 1}).graph_quality == "full"
    assert repomap.build_tier0(tmp_path, analysed, {"Python": 1, "Rust": 1}).graph_quality == "partial"


def test_graph_quality_full_for_js_without_treesitter(tmp_path, monkeypatch):
    analysed = _write(tmp_path, {"a.js": "import './b';\n", "b.js": "export const b = 1;\n"})
    monkeypatch.setattr(ts_detect, "available", lambda: False)
    t = repomap.build_tier0(tmp_path, analysed, {})
    assert t.graph_quality == "full" and t.symbols.get("b.js", []) == [] and ("a.js", "b.js") in t.edges


def test_graph_quality_ignored_languages(tmp_path):
    analysed = _write(tmp_path, {"a.py": "x = 1\n", "n.ipynb": "{}", "s.sql": "select 1;"})
    assert repomap.build_tier0(tmp_path, analysed, {}).graph_quality == "full"
    assert repomap.build_tier0(tmp_path, ["n.ipynb"], {}).graph_quality == "tree_only"


def _sigs(tmp_path, rel: str, text: str) -> dict[str, tuple[str, str, int]]:
    if not ts_detect.available():
        pytest.skip("tree-sitter grammars not installed")
    analysed = _write(tmp_path, {rel: text})
    t = repomap.build_tier0(tmp_path, analysed, {})
    return {s.name: (s.kind, s.signature, s.line) for s in t.symbols[rel]}


def test_js_symbols_signatures(tmp_path):
    got = _sigs(tmp_path, "a.js", "function go(x) {\n  return x;\n}\nclass Box { run(a, b) { return a; } }\n"
                "function wide(\n  alpha,\n  beta\n) {\n  return 1;\n}\n")
    assert got["go"] == ("function", "function go(x)", 1)
    assert got["Box"] == ("class", "class Box", 4)
    assert got["run"] == ("method", "run(a, b)", 4)
    assert got["wide"] == ("function", "function wide(alpha, beta)", 5)


def test_java_signature_skips_annotation(tmp_path):
    got = _sigs(tmp_path, "A.java", "public class A {\n    @Override\n    public String toString() { return \"a\"; }\n}\n")
    assert got["A"][:2] == ("class", "public class A")
    assert got["toString"][:2] == ("method", "public String toString()")


def test_ts_signature(tmp_path):
    got = _sigs(tmp_path, "a.ts", "export function add(a: number, b: number): number { return a + b; }\n")
    assert got["add"] == ("function", "function add(a: number, b: number): number", 1)


def test_go_signature(tmp_path):
    got = _sigs(tmp_path, "a.go", "package main\n\nfunc Add(a int, b int) int {\n\treturn a + b\n}\n")
    assert got["Add"] == ("function", "func Add(a int, b int) int", 3)


def test_in_degree_matches_edges(tmp_path):
    analysed = _write(tmp_path, {
        "a.py": "import b\nimport c\n", "b.py": "import c\n", "c.py": "x = 1\n",
        "u.js": "import './v';\n", "v.js": "export const v = 1;\n", "notes.rs": "fn x() {}\n"})
    t = repomap.build_tier0(tmp_path, analysed, {})
    assert t.in_degree["c.py"] == 2 and t.in_degree["b.py"] == 1 and t.in_degree["a.py"] == 0
    assert t.in_degree["v.js"] == 1 and t.in_degree["notes.rs"] == 0
    assert set(t.in_degree) == set(analysed)
    expect = ranking.import_graph(tmp_path, analysed)
    assert {k: t.in_degree[k] for k in expect} == expect


def test_init_is_a_normal_node(tmp_path):
    analysed = _write(tmp_path, {"pkg/__init__.py": "", "pkg/b.py": "x = 1\n", "pkg/c.py": "from . import b\n"})
    t = repomap.build_tier0(tmp_path, analysed, {})
    assert t.edges == {("pkg/c.py", "pkg/b.py"), ("pkg/c.py", "pkg/__init__.py")}


def test_empty_repo(tmp_path):
    t = repomap.build_tier0(tmp_path, [], {})
    assert t.files == [] and t.edges == set() and t.graph_quality == "tree_only"


# ---- final fix wave ---------------------------------------------------------------------------------------------

def test_entry_names_cover_pipeline_list():
    from gh_analyzer import pipeline
    assert set(pipeline.ENTRY_NAMES) <= set(repomap.ENTRY_NAMES)


def test_entry_points_ts_tsx_nested(tmp_path):
    analysed = _write(tmp_path, {"server/src/index.ts": "export const a = 1;\n", "client/src/main.tsx": "export const b = 1;\n",
                                 "lib/util.ts": "export const c = 1;\n"})
    t = repomap.build_tier0(tmp_path, analysed, {"TypeScript": 3})
    assert {"server/src/index.ts", "client/src/main.tsx"} <= set(t.entry_points)
    assert "lib/util.ts" not in t.entry_points


def test_quality_partial_for_at_slash_alias(tmp_path):
    analysed = _write(tmp_path, {"src/a.ts": "import { x } from '@/lib/x';\nexport const a = x;\n", "src/lib/x.ts": "export const x = 1;\n"})
    assert repomap.build_tier0(tmp_path, analysed, {"TypeScript": 2}).graph_quality == "partial"


@pytest.mark.parametrize("spec", ["~/lib/x", "#internal/x"])
def test_quality_partial_for_other_aliases(tmp_path, spec):
    analysed = _write(tmp_path, {"a.js": f"const x = require('{spec}');\n"})
    assert repomap.build_tier0(tmp_path, analysed, {"JavaScript": 1}).graph_quality == "partial"


def test_quality_partial_for_tsconfig_paths_with_comments(tmp_path):
    analysed = _write(tmp_path, {"src/a.ts": "export const a = 1;\n",
                                 "tsconfig.json": '{\n // comment\n "compilerOptions": {"paths": {"app/*": ["src/*"]}}\n}\n'})
    assert repomap.build_tier0(tmp_path, analysed, {"TypeScript": 1}).graph_quality == "partial"


def test_quality_partial_for_nested_jsconfig_paths(tmp_path):
    analysed = _write(tmp_path, {"web/a.js": "export const a = 1;\n", "web/jsconfig.json": '{"compilerOptions": {"paths": {}}}'})
    assert repomap.build_tier0(tmp_path, analysed, {"JavaScript": 1}).graph_quality == "partial"


def test_quality_full_without_aliases(tmp_path):
    analysed = _write(tmp_path, {"src/a.ts": "import { b } from './b';\nimport React from '@scope/pkg';\nexport const a = b;\n",
                                 "src/b.ts": "export const b = 1;\n", "tsconfig.json": '{"compilerOptions": {"strict": true}}'})
    assert repomap.build_tier0(tmp_path, analysed, {"TypeScript": 2}).graph_quality == "full"


def test_tsconfig_in_excluded_dir_ignored(tmp_path):
    analysed = _write(tmp_path, {"a.ts": "export const a = 1;\n", "node_modules/p/tsconfig.json": '{"paths": {}}'})
    assert repomap.build_tier0(tmp_path, analysed, {"TypeScript": 1}).graph_quality == "full"


def test_one_bad_file_keeps_other_symbols(tmp_path, monkeypatch):
    analysed = _write(tmp_path, {"bad.py": "def boom():\n    pass\n", "good.py": "def ok():\n    pass\n"})
    real = repomap._py_symbols

    def flaky(tree):
        if any(getattr(n, "name", "") == "boom" for n in tree.body):
            raise RecursionError("deep")
        return real(tree)
    monkeypatch.setattr(repomap, "_py_symbols", flaky)
    t = repomap.build_tier0(tmp_path, analysed, {"Python": 4})
    assert "bad.py" not in t.symbols and [s.name for s in t.symbols["good.py"]] == ["ok"]


@pytest.mark.skipif(not ts_detect.available(), reason="tree-sitter not installed")
def test_one_bad_ts_file_keeps_other_symbols(tmp_path, monkeypatch):
    analysed = _write(tmp_path, {"bad.js": "function boom() {}\n", "good.js": "function ok() {}\n"})
    real = repomap._ts_symbols

    def flaky(src, lang):
        if "boom" in src:
            raise RuntimeError("x")
        return real(src, lang)
    monkeypatch.setattr(repomap, "_ts_symbols", flaky)
    t = repomap.build_tier0(tmp_path, analysed, {"JavaScript": 2})
    assert "bad.js" not in t.symbols and [s.name for s in t.symbols["good.js"]] == ["ok"]
