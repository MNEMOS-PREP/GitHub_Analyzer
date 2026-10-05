"""In-degree of each file in the import graph, for JavaScript, TypeScript, Java and Go.

`ranking.import_graph` already does this for Python with `ast`. These languages are matched with a short list of
import patterns instead: what matters for ranking is only "how many other files depend on this one", so a missed
dynamic import costs a little centrality, never a wrong citation. Only imports that resolve to a file inside the
analysed set count; a package from npm, the JDK or the Go standard library points at nothing and is ignored.
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path

JS_EXT = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")
_JS_IMPORT = re.compile(r"""(?:\bfrom\s+|\bimport\s*\(\s*|\brequire\s*\(\s*|^\s*import\s+)['"]([^'"]+)['"]""", re.M)
_JAVA_IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+?)(?:\.\*)?\s*;", re.M)
_JAVA_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.M)
_GO_IMPORT_BLOCK = re.compile(r"import\s*\(([^)]*)\)", re.S)
_GO_IMPORT_ONE = re.compile(r'^\s*import\s+(?:\w+\s+)?"([^"]+)"', re.M)
_GO_PATH = re.compile(r'"([^"]+)"')


def _read(root: Path, rel: str) -> str:
    try:
        return (Path(root) / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _resolve_js(files: set[str], importer: str, spec: str) -> str | None:
    if not spec.startswith("."):
        return None
    base = posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec))
    stem = posixpath.splitext(base)[0] if base.endswith(JS_EXT) else base
    for cand in (base, *(base + e for e in JS_EXT), *(stem + e for e in JS_EXT), *(base + "/index" + e for e in JS_EXT)):
        if cand in files and cand.endswith(JS_EXT):          # `import "./theme.css"` points at a file we do not graph
            return cand
    return None


def in_degree(root: Path, analysed: list[str]) -> dict[str, int]:
    files = set(analysed)
    js = [p for p in analysed if p.endswith(JS_EXT)]
    java = [p for p in analysed if p.endswith(".java")]
    go = [p for p in analysed if p.endswith(".go")]
    indeg = {p: 0 for p in js + java + go}

    for rel in js:
        targets = {t for spec in _JS_IMPORT.findall(_read(root, rel)) if (t := _resolve_js(files, rel, spec))}
        targets.discard(rel)
        for t in targets:
            indeg[t] += 1

    # Java: a fully qualified class name maps to the file whose package + file name spell it
    classes: dict[str, str] = {}
    for rel in java:
        pkg = _JAVA_PACKAGE.search(_read(root, rel))
        classes[(pkg.group(1) + "." if pkg else "") + Path(rel).stem] = rel
    for rel in java:
        src = _read(root, rel)
        targets: set[str] = set()
        for imp in _JAVA_IMPORT.findall(src):
            parts = imp.split(".")
            for i in range(len(parts), 0, -1):                 # `import static a.B.method` resolves to class a.B
                hit = classes.get(".".join(parts[:i]))
                if hit:
                    targets.add(hit)
                    break
        pkg = _JAVA_PACKAGE.search(src)
        if pkg:                                                # same-package classes need no import
            for name in re.findall(r"\b([A-Z]\w+)\b", src):
                hit = classes.get(pkg.group(1) + "." + name)
                if hit:
                    targets.add(hit)
        targets.discard(rel)
        for t in targets:
            indeg[t] += 1

    # Go: an import path names a directory; every file importing it depends on that package's files equally
    by_dir: dict[str, list[str]] = {}
    for rel in go:
        by_dir.setdefault(posixpath.dirname(rel), []).append(rel)
    for rel in go:
        src = _read(root, rel)
        paths = set(_GO_IMPORT_ONE.findall(src))
        for block in _GO_IMPORT_BLOCK.findall(src):
            paths.update(_GO_PATH.findall(block))
        hit_dirs: set[str] = set()
        for path in paths:
            for d in by_dir:
                if d and (path == d or path.endswith("/" + d)):
                    hit_dirs.add(d)
        for d in hit_dirs:
            if d != posixpath.dirname(rel):
                for t in by_dir[d]:
                    indeg[t] += 1
    return indeg
