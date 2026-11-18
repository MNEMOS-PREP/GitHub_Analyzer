"""Tier 1 repo map: a token-capped text summary of a Tier 0 index, plus a commit-SHA-keyed JSON cache.

Model-free and deterministic (sorted, no timestamps). The map is cut in a fixed order until it fits the budget: fewer
signatures per file, a shallower tree, a shorter README, then fewer files (lowest in-degree first). `tokens <= budget` always holds.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from typing import Callable

from .repomap import Tier0

TIER1_BUDGET = 3000
TOP_FILES = 10
SIGS_PER_FILE = 12
README_CHARS = 2000                  # README excerpt before any truncation
_CUT_SIGS, _CUT_DEPTH, _CUT_README = 6, 2, 600


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def _ranked(t0: Tier0) -> list[str]:
    """Files by in-degree, highest first; ties broken by path so the order is deterministic."""
    return sorted(t0.files, key=lambda f: (-t0.in_degree.get(f, 0), f))


def _tree(files: list[str], depth: int | None) -> list[str]:
    nodes: set[tuple[str, ...]] = set()
    for f in files:
        parts = tuple(f.split("/"))
        for i in range(len(parts) if depth is None else min(len(parts), depth)):
            nodes.add(parts[: i + 1])
    full = {tuple(f.split("/")) for f in files}
    return ["  " * (len(n) - 1) + n[-1] + ("" if n in full else "/") for n in sorted(nodes)]


def _render(t0: Tier0, readme: str, sigs: int, depth: int | None, readme_chars: int, n_files: int) -> str:
    kept = _ranked(t0)[:n_files]
    stack = ", ".join(f"{lang} ({lines} lines)" for lang, lines in sorted(t0.languages.items(), key=lambda kv: (-kv[1], kv[0])))
    lines = [f"Stack: {stack or 'unknown'}"]
    excerpt = readme.strip()[:readme_chars]
    if excerpt:
        lines += ["", "README:", excerpt]
    lines += ["", "Tree:", *_tree(sorted(kept), depth)]
    lines += ["", "Key files (most imported first):"]
    for f in kept[:TOP_FILES]:
        lines.append(f"{f} (imported by {t0.in_degree.get(f, 0)})")
        lines += [f"  {s.signature}" for s in t0.symbols.get(f, [])[:sigs]]
    return "\n".join(lines)


def build_tier1(t0: Tier0, readme: str, budget: int = TIER1_BUDGET) -> tuple[str, int]:
    """Returns `(map_text, tokens)` with `tokens <= budget`."""
    n = len(t0.files)
    for sigs, depth, chars in ((SIGS_PER_FILE, None, README_CHARS), (_CUT_SIGS, None, README_CHARS),
                               (_CUT_SIGS, _CUT_DEPTH, README_CHARS), (_CUT_SIGS, _CUT_DEPTH, _CUT_README)):
        text = _render(t0, readme, sigs, depth, chars, n)
        if estimate_tokens(text) <= budget:
            return text, estimate_tokens(text)

    def at(k: int) -> str:
        return _render(t0, readme, _CUT_SIGS, _CUT_DEPTH, _CUT_README, k)

    lo, hi = 1, n                                    # fewest files that keeps the top file; size only grows with k
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if estimate_tokens(at(mid)) <= budget:
            lo = mid
        else:
            hi = mid - 1
    text = at(min(lo, n))
    if estimate_tokens(text) > budget:               # budget too small even for one file: hard cut
        text = text[: max(budget, 0) * 4]
    return text, estimate_tokens(text)


def _safe(part: str, what: str) -> str:
    if not part or ".." in part or "/" in part or "\\" in part or os.sep in part or (os.altsep and os.altsep in part):
        raise ValueError(f"unsafe cache {what}: {part!r}")
    return part


def cached(cache_dir: Path, sha: str, kind: str, builder: Callable[[], dict],
           validate: Callable[[dict], bool] | None = None) -> tuple[dict, bool]:
    """JSON cache at `cache_dir/<sha>/<kind>.json`. Returns `(value, was_cache_hit)`; an unreadable or invalid file is a miss,
    and so is a hit that `validate` (when given) rejects; it is then rebuilt and overwritten. A miss returns the same
    JSON round-tripped value a later hit would, so hit and miss are identical."""
    path = Path(cache_dir) / _safe(sha, "sha") / f"{_safe(kind, 'kind')}.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict) and (validate is None or validate(value)):
            return value, True
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        pass
    value = builder()
    data = json.dumps(value, ensure_ascii=False)
    value = json.loads(data)
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
        os.replace(tmp, path)
        tmp = None
    except OSError:
        pass                                          # an unwritable cache only costs a rebuild next time
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return value, False
