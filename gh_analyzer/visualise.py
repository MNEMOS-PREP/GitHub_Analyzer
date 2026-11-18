"""Diagram graph and Mermaid text built from the Tier 0 index.

Model-free. Every edge comes from `Tier0.edges` (the static import graph); nothing is inferred, so a missing edge is
possible but an invented one is not. Output of `diagrams` is plain JSON-serialisable data.
"""

from __future__ import annotations

import unicodedata

from .repomap import Tier0

DIAGRAM_TOP_N = 25
MAX_ENTRY_POINTS = 5
ROOT_GROUP = "(root)"
ENTRY_FLOW_DEPTH = 2
KINDS = ("modules", "imports", "entry_flow")

NOTE_EMPTY = "There are no source files to draw yet."
NOTE_TREE_ONLY = "I could not read the imports in this repository's languages, so this diagram shows the folder layout only."
NOTE_PARTIAL = "Some languages here were read with less depth, so a few connections may be missing."

_ESCAPES = {"#": "#35;", '"': "#quot;", "<": "#lt;", ">": "#gt;", "&": "#amp;", "\\": "#92;", "`": "#96;"}


def _group(path: str) -> str:
    return path.split("/", 1)[0] if "/" in path else ROOT_GROUP


def build_graph(t0: Tier0, top_n: int = DIAGRAM_TOP_N) -> dict:
    """At most `top_n` nodes: up to MAX_ENTRY_POINTS entry points first, then the most connected files; edges only between kept nodes."""
    files = set(t0.files)

    def rank(p: str) -> tuple[int, str]:
        return (-t0.in_degree.get(p, 0), p)

    out_degree: dict[str, int] = {}
    for a, _ in t0.edges:
        out_degree[a] = out_degree.get(a, 0) + 1                # edges is a set: distinct imported files per importer

    def entry_rank(p: str) -> tuple[int, str]:                  # real entry points import a lot and are imported by little
        return (-out_degree.get(p, 0), p)

    top_n = max(top_n, 0)
    entries = set(t0.entry_points)
    chosen = sorted(files & entries, key=entry_rank)[:min(MAX_ENTRY_POINTS, top_n)]
    kept = set(chosen)
    kept |= set(sorted(files - kept, key=rank)[:top_n - len(kept)])
    paths = sorted(kept)
    ids = {p: f"n{i}" for i, p in enumerate(paths)}
    nodes = [{"id": ids[p], "path": p, "group": _group(p), "in_degree": int(t0.in_degree.get(p, 0)),
              "entry": p in entries} for p in paths]
    edges = sorted(({"from": ids[a], "to": ids[b]} for a, b in t0.edges if a in ids and b in ids),
                   key=lambda e: (e["from"], e["to"]))
    return {"nodes": nodes, "edges": edges, "quality": t0.graph_quality, "truncated": len(kept) < len(files)}


def _label(text: str) -> str:
    text = "".join(c for c in text if unicodedata.category(c) not in ("Cc", "Zl", "Zp"))
    return "".join(_ESCAPES.get(c, c) for c in text)


def _node_line(n: dict, indent: str) -> str:
    return f'{indent}{n["id"]}["{_label(n["path"])}"]' + (":::entry" if n["entry"] else "")


def _entry_flow(graph: dict) -> tuple[list[dict], list[dict]]:
    nodes = graph["nodes"]
    if not nodes:
        return [], []
    starts = [n["id"] for n in nodes if n["entry"]] or [min(nodes, key=lambda n: (-n["in_degree"], n["path"]))["id"]]
    out: dict[str, list[str]] = {}
    for e in graph["edges"]:
        out.setdefault(e["from"], []).append(e["to"])
    seen, frontier = set(starts), set(starts)
    for _ in range(ENTRY_FLOW_DEPTH):
        frontier = {b for a in frontier for b in out.get(a, ())} - seen
        seen |= frontier
    return ([n for n in nodes if n["id"] in seen],
            [e for e in graph["edges"] if e["from"] in seen and e["to"] in seen])


def to_mermaid(graph: dict, kind: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"unknown diagram kind: {kind!r}")
    nodes, edges = (_entry_flow(graph) if kind == "entry_flow" else (graph["nodes"], graph["edges"]))
    lines = ["flowchart LR"]
    if nodes:
        if kind == "modules":
            groups: dict[str, list[dict]] = {}
            for n in nodes:
                groups.setdefault(n["group"], []).append(n)
            for k, name in enumerate(sorted(groups)):
                lines.append(f'  subgraph g{k}["{_label(name)}"]')
                lines += [_node_line(n, "    ") for n in groups[name]]
                lines.append("  end")
        else:
            lines += [_node_line(n, "  ") for n in nodes]
        lines += [f'  {e["from"]} --> {e["to"]}' for e in edges]
        if any(n["entry"] for n in nodes):
            lines.append("  classDef entry fill:#fde68a,stroke:#b45309,stroke-width:2px")
    return "\n".join(lines)


def diagrams(t0: Tier0) -> dict:
    graph = build_graph(t0)
    notes: list[str] = []
    if not t0.files:
        notes.append(NOTE_EMPTY)
    elif t0.graph_quality == "tree_only":
        notes.append(NOTE_TREE_ONLY)
    elif t0.graph_quality == "partial":
        notes.append(NOTE_PARTIAL)
    if graph["truncated"]:
        notes.append(f"Showing the {len(graph['nodes'])} most connected files out of {len(t0.files)}.")
    return {"graph": graph, "mermaid": {k: to_mermaid(graph, k) for k in KINDS}, "notes": notes}
