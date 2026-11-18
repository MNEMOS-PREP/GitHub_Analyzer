"""Tier 0 repo index: file tree, top-level signatures, entry points, import edges and a graph-quality label.

Model-free and read-only: files are parsed as text (`ast`, or tree-sitter when installed), never executed. One bad file
(syntax error, unreadable, odd encoding) is skipped for symbols and edges; it never stops the build and never invents
an edge. Tiers 1 (text map) and the diagrams are derived from this index.
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import modgraph, ranking, ts_detect
from .inventory import CODE_EXT, EXCLUDE_DIRS

ENTRY_NAMES = {"__main__.py", "main.py", "app.py", "index.js", "server.py", "manage.py", "run.py", "wsgi.py", "asgi.py",
               "server.js", "app.js", "main.js", "index.ts", "main.ts", "index.tsx", "main.tsx", "server.ts", "main.go",
               "Main.java", "Application.java", "Program.cs"}
_JS_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs"}
_ALIAS_SPEC = re.compile(r"""(?:\bfrom\s+|\bimport\s*\(?\s*|\brequire\s*\(\s*)['"](?:@/|~/|#)""")
_TSCONFIG = re.compile(r"^[jt]sconfig[^/]*\.json$", re.I)
GRAPHED = {"Python", "JavaScript", "TypeScript", "Java", "Go"}      # edges exist for these (modgraph is regex-based)
IGNORED_LANGS = {"Jupyter", "HTML", "CSS", "SQL", "Shell"}           # not graphed, but no reason to say "partial"
_FUNC_NODES = {"function_declaration": "function"}
_CLASS_NODES = {"class_declaration": "class"}
_METHOD_NODES = {"method_definition": "method", "method_declaration": "method"}
_LEADING_ANNOTATIONS = re.compile(r"^(?:@\w+(?:\([^)]*\))?\s+)+")      # Java `@Override` before the declaration


@dataclass
class Symbol:
    name: str
    kind: str                  # "function" | "class" | "method"
    line: int
    signature: str


@dataclass
class Tier0:
    files: list[str]
    edges: set[tuple[str, str]]
    in_degree: dict[str, int]
    symbols: dict[str, list[Symbol]]
    entry_points: list[str]
    languages: dict[str, int]
    graph_quality: str         # "full" | "partial" | "tree_only"


def _read(root: Path, rel: str) -> str | None:
    try:
        return (Path(root) / rel).read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None


def _py_signature(node: ast.AST) -> str:
    if isinstance(node, ast.ClassDef):
        bases = [ast.unparse(b) for b in node.bases] + [f"{k.arg}={ast.unparse(k.value)}" for k in node.keywords if k.arg]
        return f"class {node.name}" + (f"({', '.join(bases)})" if bases else "")
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    ret = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    return f"{prefix} {node.name}({ast.unparse(node.args)}){ret}"


def _py_symbols(tree: ast.Module) -> list[Symbol]:
    out: list[Symbol] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(Symbol(node.name, "function", node.lineno, _py_signature(node)))
        elif isinstance(node, ast.ClassDef):
            out.append(Symbol(node.name, "class", node.lineno, _py_signature(node)))
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append(Symbol(sub.name, "method", sub.lineno, _py_signature(sub)))
    return out


def _is_main_guard(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare) and isinstance(node.test.left, ast.Name) \
                and node.test.left.id == "__name__" and len(node.test.comparators) == 1 \
                and isinstance(node.test.comparators[0], ast.Constant) and node.test.comparators[0].value == "__main__":
            return True
    return False


def _ts_symbols(src: str, lang: str) -> list[Symbol]:
    """Top-level functions, classes and their methods for JavaScript, TypeScript, Java and Go; first source line as signature."""
    try:
        tree = ts_detect.Parser(ts_detect._LANGS[lang]).parse(src.encode("utf-8"))
    except Exception:
        return []
    out: list[Symbol] = []

    def add(node, kind: str) -> None:
        name = node.child_by_field_name("name")
        if name is None:
            return
        body = node.child_by_field_name("body")
        if body is not None:                                   # declaration text up to the body, any line count
            head = node.text[: body.start_byte - node.start_byte].decode("utf-8", "replace")
        else:
            head = (ts_detect._txt(node).splitlines() or [""])[0]
        sig = _LEADING_ANNOTATIONS.sub("", " ".join(head.split())).rstrip("{ ").strip()
        sig = sig.replace("( ", "(").replace(" )", ")")           # a wrapped parameter list reads as one line
        out.append(Symbol(ts_detect._txt(name), kind, node.start_point[0] + 1, sig))

    def visit(node) -> None:
        for child in node.children:
            t = child.type
            if t in _FUNC_NODES:
                add(child, "function")
            elif t in _CLASS_NODES:
                add(child, "class")
                body = child.child_by_field_name("body")
                if body is not None:
                    visit(body)
            elif t in _METHOD_NODES:
                add(child, "method")
            elif t == "export_statement":
                visit(child)

    try:
        visit(tree.root_node)
    except Exception:                                          # keep what was found before a bad node
        pass
    return out


def _graph_quality(analysed: list[str]) -> str:
    langs = {CODE_EXT[Path(p).suffix.lower()] for p in analysed if Path(p).suffix.lower() in CODE_EXT} - IGNORED_LANGS
    hit = langs & GRAPHED
    if not hit:
        return "tree_only"
    return "full" if hit == langs else "partial"


def _uses_aliases(root: Path, analysed: list[str]) -> bool:
    """True when imports go through aliases the static graph cannot follow (`@/x`, `~/x`, `#x`, tsconfig/jsconfig `paths`)."""
    try:
        for rel in analysed:
            if Path(rel).suffix.lower() in _JS_EXT:
                src = _read(root, rel)
                if src and _ALIAS_SPEC.search(src):
                    return True
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
            for fn in filenames:
                if _TSCONFIG.match(fn):
                    try:
                        if '"paths"' in (Path(dirpath) / fn).read_text(encoding="utf-8", errors="replace"):
                            return True
                    except (OSError, ValueError):
                        pass
    except Exception:
        pass
    return False


def build_tier0(root: Path, analysed: list[str], languages: dict[str, int]) -> Tier0:
    """The structural index of the analysed files; `languages` is `Inventory.languages` (name -> line count)."""
    root = Path(root)
    files = list(analysed)
    edges = set(ranking.import_edges(root, files)) | set(modgraph.edges(root, files))
    in_degree = {f: 0 for f in files}
    for _, imported in edges:                       # edges is a set, so each importer counts once per file
        in_degree[imported] = in_degree.get(imported, 0) + 1

    symbols: dict[str, list[Symbol]] = {}
    entries: set[str] = set()
    for rel in files:
        name = Path(rel).name
        if name in ENTRY_NAMES:
            entries.add(rel)
        src = _read(root, rel)
        if src is None:
            continue
        if rel.endswith(".py"):
            try:
                tree = ast.parse(src)
            except (SyntaxError, ValueError, RecursionError):
                continue
            if _is_main_guard(tree):
                entries.add(rel)
            try:
                symbols[rel] = _py_symbols(tree)
            except Exception:                      # incl. RecursionError: skip this file's symbols only
                pass
        elif ts_detect.available() and Path(rel).suffix.lower() in ts_detect.EXT_LANG:
            try:
                found = _ts_symbols(src, ts_detect.EXT_LANG[Path(rel).suffix.lower()])
            except Exception:
                found = []
            if found:
                symbols[rel] = found
    quality = _graph_quality(files)
    if quality == "full" and _uses_aliases(root, files):
        quality = "partial"
    return Tier0(files, edges, in_degree, symbols, sorted(entries), dict(languages), quality)
