"""Tests for the diagram graph and Mermaid output (visualise.py)."""

from __future__ import annotations

import json
import re

import pytest

from gh_analyzer import visualise
from gh_analyzer.repomap import Tier0

ID_RE = re.compile(r"^[A-Za-z0-9_]+$")
NODE_LINE = re.compile(r'^\s*(\w+)\["([^\n]*)"\](:::entry)?\s*$')
EDGE_LINE = re.compile(r"^\s*(\w+) --> (\w+)\s*$")
SUB_LINE = re.compile(r'^\s*subgraph (\w+)\["([^\n]*)"\]\s*$')


def make_t0(files, edges=(), entry_points=(), quality="full", in_degree=None) -> Tier0:
    edges = set(edges)
    if in_degree is None:
        in_degree = {f: 0 for f in files}
        for _, dst in edges:
            in_degree[dst] = in_degree.get(dst, 0) + 1
    return Tier0(files=list(files), edges=edges, in_degree=in_degree, symbols={}, entry_points=list(entry_points),
                 languages={"Python": len(files)}, graph_quality=quality)


def parse(mermaid: str):
    """Structural parse of our own Mermaid output; fails on any line it does not understand."""
    lines = mermaid.split("\n")
    assert lines[0] == "flowchart LR"
    nodes, edges, subs = {}, [], {}
    open_subs = 0
    for line in lines[1:]:
        s = line.strip()
        if s == "end":
            assert open_subs == 1, "`end` without an open subgraph"
            open_subs = 0
            continue
        if not s or s.startswith("classDef "):
            continue
        for rx in (SUB_LINE, NODE_LINE, EDGE_LINE):
            m = rx.match(line)
            if m:
                break
        else:
            pytest.fail(f"unparseable line: {line!r}")
        if rx is SUB_LINE:
            assert open_subs == 0, "nested or unclosed subgraph"
            open_subs = 1
            subs[m.group(1)] = m.group(2)
        elif rx is NODE_LINE:
            nodes[m.group(1)] = m.group(2)
        else:
            edges.append((m.group(1), m.group(2)))
    assert open_subs == 0, "subgraph without `end`"
    return nodes, edges, subs


def test_edges_only_from_import_graph():
    files = ["a.py", "b.py", "c.py", "pkg/d.py"]
    edges = {("a.py", "b.py"), ("b.py", "pkg/d.py"), ("c.py", "b.py")}
    g = visualise.build_graph(make_t0(files, edges))
    by_id = {n["id"]: n["path"] for n in g["nodes"]}
    out = {(by_id[e["from"]], by_id[e["to"]]) for e in g["edges"]}
    assert out == edges
    g2 = visualise.build_graph(make_t0(files, edges), top_n=1)
    by_id = {n["id"]: n["path"] for n in g2["nodes"]}
    assert {(by_id[e["from"]], by_id[e["to"]]) for e in g2["edges"]} <= edges


def test_top_n_cap_and_truncated_flag():
    files = [f"src/f{i:03d}.py" for i in range(300)]
    edges = {(files[i], files[i + 1]) for i in range(299)}
    t0 = make_t0(files, edges, entry_points=["src/f000.py", "src/f001.py"])
    g = visualise.build_graph(t0)
    paths = {n["path"] for n in g["nodes"]}
    assert len(g["nodes"]) == visualise.DIAGRAM_TOP_N
    assert {"src/f000.py", "src/f001.py"} <= paths
    assert g["truncated"] is True
    assert all(isinstance(n["entry"], bool) for n in g["nodes"])
    ids = {n["id"] for n in g["nodes"]}
    assert all(e["from"] in ids and e["to"] in ids for e in g["edges"])
    out = visualise.diagrams(t0)
    assert any("Showing the" in n and "out of 300" in n for n in out["notes"])


def test_many_entry_points_do_not_blow_the_cap():
    entries = [f"e{i:03d}.py" for i in range(80)]
    others = [f"o{i:03d}.py" for i in range(220)]
    in_degree = {p: i for i, p in enumerate(entries)}
    in_degree.update({p: 100 + i for i, p in enumerate(others)})
    t0 = make_t0(entries + others, entry_points=entries, in_degree=in_degree)
    g = visualise.build_graph(t0)
    paths = {n["path"] for n in g["nodes"]}
    assert len(g["nodes"]) == 25
    assert g["truncated"] is True
    assert set(entries[:5]) <= paths                       # 5 entry points are prioritised (out-degree ties, so by path)
    assert sum(p in set(entries) for p in paths) == 5      # remaining 20 slots go to the most connected files
    assert set(others[-20:]) <= paths
    assert all(n["entry"] == (n["path"] in set(entries)) for n in g["nodes"])


def test_few_entry_points_all_kept_when_small():
    files = [f"f{i}.py" for i in range(10)]
    g = visualise.build_graph(make_t0(files, entry_points=files[:3]))
    assert len(g["nodes"]) == 10
    assert g["truncated"] is False
    assert sorted(n["path"] for n in g["nodes"] if n["entry"]) == files[:3]


def test_entry_cap_limited_by_top_n():
    files = [f"f{i}.py" for i in range(10)]
    g = visualise.build_graph(make_t0(files, entry_points=files[:4]), top_n=3)
    assert len(g["nodes"]) == 3
    assert g["truncated"] is True


def test_not_truncated_and_grouping_and_order():
    files = ["z.py", "pkg/a.py", "app/b.py"]
    g = visualise.build_graph(make_t0(files, {("z.py", "pkg/a.py")}))
    assert g["truncated"] is False
    assert [n["path"] for n in g["nodes"]] == ["app/b.py", "pkg/a.py", "z.py"]
    assert [n["id"] for n in g["nodes"]] == ["n0", "n1", "n2"]
    assert {n["path"]: n["group"] for n in g["nodes"]} == {"app/b.py": "app", "pkg/a.py": "pkg", "z.py": "(root)"}


def test_tie_break_by_path():
    files = ["b.py", "a.py", "c.py"]
    g = visualise.build_graph(make_t0(files), top_n=2)
    assert [n["path"] for n in g["nodes"]] == ["a.py", "b.py"]


WEIRD = ['a "b".py', "x[1].py", "ünï.py", "end", "graph", "d/<i>&\\z.py", "sp ace/f.py"]


def test_mermaid_ids_sanitised():
    files = WEIRD + ["main.py"]
    edges = {("main.py", f) for f in WEIRD}
    t0 = make_t0(files, edges, entry_points=["main.py"])
    g = visualise.build_graph(t0)
    for n in g["nodes"]:
        assert ID_RE.match(n["id"])
    for kind in ("modules", "imports", "entry_flow"):
        text = visualise.to_mermaid(g, kind)
        nodes, medges, subs = parse(text)
        for i in list(nodes) + list(subs):
            assert ID_RE.match(i)
        for a, b in medges:
            assert a in nodes and b in nodes
        by_id = {n["id"]: n["path"] for n in g["nodes"]}
        path_edges = {(by_id[a], by_id[b]) for a, b in medges}
        assert len(path_edges) == len(medges)
        assert path_edges <= edges
        if kind in ("modules", "imports"):
            assert path_edges == edges
        else:
            assert path_edges
        if kind == "modules":
            assert len(subs) == len({n["group"] for n in g["nodes"]})
        for label in list(nodes.values()) + list(subs.values()):
            assert '"' not in label
            assert not re.search(r"[<>&\\]", label)
        if kind == "imports":
            assert 'a #quot;b#quot;.py' in nodes.values()
            assert "ünï.py" in nodes.values()
            assert "x[1].py" in nodes.values()
            assert "end" in nodes.values()
            assert len(nodes) == len(files)
            assert len(medges) == len(edges)


def test_label_control_chars_removed():
    g = visualise.build_graph(make_t0(["a\nb\x00c.py"]))
    nodes, _, _ = parse(visualise.to_mermaid(g, "imports"))
    assert list(nodes.values()) == ["abc.py"]


def test_modules_has_subgraphs_and_entry_style():
    files = ["main.py", "pkg/a.py", "pkg/b.py"]
    g = visualise.build_graph(make_t0(files, {("main.py", "pkg/a.py")}, entry_points=["main.py"]))
    text = visualise.to_mermaid(g, "modules")
    _, _, subs = parse(text)
    assert sorted(subs.values()) == ["(root)", "pkg"]
    assert "classDef entry" in text and ":::entry" in text
    assert "subgraph" not in visualise.to_mermaid(g, "imports")


def test_entry_flow_depth_two():
    files = ["main.py", "a.py", "b.py", "c.py", "other.py"]
    edges = {("main.py", "a.py"), ("a.py", "b.py"), ("b.py", "c.py"), ("other.py", "c.py")}
    g = visualise.build_graph(make_t0(files, edges, entry_points=["main.py"]))
    nodes, medges, _ = parse(visualise.to_mermaid(g, "entry_flow"))
    assert sorted(nodes.values()) == ["a.py", "b.py", "main.py"]
    assert len(medges) == 2


def test_entry_flow_fallback_without_entry_points():
    files = ["a.py", "b.py", "hub.py", "z.py"]
    edges = {("a.py", "hub.py"), ("b.py", "hub.py"), ("hub.py", "z.py")}
    g = visualise.build_graph(make_t0(files, edges))
    nodes, _, _ = parse(visualise.to_mermaid(g, "entry_flow"))
    assert sorted(nodes.values()) == ["hub.py", "z.py"]


def test_empty_repo():
    out = visualise.diagrams(make_t0([], quality="tree_only"))
    assert out["graph"] == {"nodes": [], "edges": [], "quality": "tree_only", "truncated": False}
    assert out["mermaid"] == {"modules": "flowchart LR", "imports": "flowchart LR", "entry_flow": "flowchart LR"}
    assert "There are no source files to draw yet." in out["notes"]


def test_tree_only_note():
    files = ["main.py", "pkg/a.py"]
    out = visualise.diagrams(make_t0(files, quality="tree_only"))
    assert "I could not read the imports in this repository's languages, so this diagram shows the folder layout only." \
        in out["notes"]
    _, medges, subs = parse(out["mermaid"]["modules"])
    assert medges == []
    assert sorted(subs.values()) == ["(root)", "pkg"]
    assert out["graph"]["quality"] == "tree_only"


def test_partial_note_and_json_serialisable():
    out = visualise.diagrams(make_t0(["a.py", "b.py"], {("a.py", "b.py")}, quality="partial"))
    assert "Some languages here were read with less depth, so a few connections may be missing." in out["notes"]
    assert json.loads(json.dumps(out)) == out
    text = " ".join(out["notes"]).lower()
    assert not any(w in text for w in ("plagiarism", "copied", "original"))


def test_full_quality_has_no_notes():
    assert visualise.diagrams(make_t0(["a.py"]))["notes"] == []


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        visualise.to_mermaid(visualise.build_graph(make_t0(["a.py"])), "bogus")


def test_entry_ranked_by_out_degree_not_in_degree():
    files = ["real_entry.py", "hub_entry.py", "a.py", "b.py", "c.py", "d.py", "x.py", "y.py", "z.py"]
    edges = {("real_entry.py", f) for f in ("a.py", "b.py", "c.py", "d.py")} | {(f, "hub_entry.py") for f in ("x.py", "y.py", "z.py")}
    t0 = make_t0(files, edges, entry_points=["real_entry.py", "hub_entry.py"])
    assert t0.in_degree["hub_entry.py"] == 3 and t0.in_degree["real_entry.py"] == 0
    g = visualise.build_graph(t0, top_n=1)
    assert [n["path"] for n in g["nodes"]] == ["real_entry.py"]


def test_entry_out_degree_ties_by_path():
    t0 = make_t0(["b.py", "a.py"], entry_points=["b.py", "a.py"])
    g = visualise.build_graph(t0, top_n=1)
    assert [n["path"] for n in g["nodes"]] == ["a.py"]


def test_label_escapes_backtick_and_hash():
    g = visualise.build_graph(make_t0(["`weird`.py", "a#lt;b.py"]))
    nodes, _, _ = parse(visualise.to_mermaid(g, "imports"))
    assert sorted(nodes.values()) == ["#96;weird#96;.py", "a#35;lt;b.py"]
