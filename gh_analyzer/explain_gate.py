"""The 'explain your own code' gate (FR-3.12): a 90-second explanation of one function from a high-contribution file.

Coaching only in Phase 1 (spec section 15, decision 4): the session carries `scored: False` and the payload has no
field a score could be read from. It is offered only when blame says the function is mostly the student's, because
asking someone to explain code they did not write would be exactly the gotcha the module is built to avoid.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from .questions import copy_violations
from .secrets import redact

SECONDS = 90
MIN_LINES, MAX_LINES = 5, 60
MIN_OWN_SHARE = 0.6          # of a function's blamed lines, how many must be the student's
MIN_FILE_SHARE = 0.5         # of a file's blamed lines, how many must be the student's, for it to count as a high-contribution file

PROMPT = ("Take {seconds} seconds to explain this function out loud, or type it if you prefer: what it does, "
          "why you wrote it this way, and one thing you would change.")
NO_GATE_NEEDS_IDENTITY = "Confirm which commits are yours first, then I can pick a function you wrote."
NO_GATE_NOTHING_FOUND = "I did not find a function of a good size that is mostly yours, so I will skip this step and ask design questions."

_JS_FN = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function\s+\w+\s*\(|(?:const|let)\s+\w+\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>\s*\{)")


def _python_functions(src: str) -> list[tuple[str, int, int]]:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    out = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and getattr(n, "end_lineno", None):
            out.append((n.name, n.lineno, n.end_lineno))
    return out


def _brace_functions(src: str) -> list[tuple[str, int, int]]:
    """JS/TS/Java/Go-ish: a declaration line, then the matching close brace. Reduced depth, and says so nowhere it must not."""
    lines = src.splitlines()
    out = []
    for i, ln in enumerate(lines):
        if not _JS_FN.match(ln):
            continue
        depth, j = 0, i
        while j < len(lines):
            depth += lines[j].count("{") - lines[j].count("}")
            if depth <= 0 and "{" in "".join(lines[i:j + 1]):
                break
            j += 1
        else:
            continue
        name = re.search(r"(?:function\s+|(?:const|let)\s+)(\w+)", ln)
        out.append((name.group(1) if name else "function", i + 1, j + 1))
    return out


def _functions(rel: str, src: str) -> list[tuple[str, int, int]]:
    if rel.endswith(".py"):
        return _python_functions(src)
    if rel.endswith((".js", ".jsx", ".ts", ".tsx")):
        return _brace_functions(src)
    return []


def pick(root: Path, blame: dict[str, dict[int, bool]]) -> dict | None:
    """The best candidate: highest share of the student's own lines, then the longest within the size window."""
    best = None
    for rel, lines in blame.items():
        if not lines:
            continue
        file_share = sum(lines.values()) / len(lines)
        if file_share < MIN_FILE_SHARE:
            continue
        try:
            src = (Path(root) / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name, a, b in _functions(rel, src):
            n = b - a + 1
            if not (MIN_LINES <= n <= MAX_LINES):
                continue
            blamed = [lines[i] for i in range(a, b + 1) if i in lines]
            if len(blamed) < max(3, n // 2):
                continue
            own = sum(blamed) / len(blamed)
            if own < MIN_OWN_SHARE:
                continue
            key = (round(own, 2), n)
            if best is None or key > best[0]:
                best = (key, rel, name, a, b, src, own)
    if not best:
        return None
    _, rel, name, a, b, src, own = best
    code = redact("\n".join(src.splitlines()[a - 1:b]))      # a committed secret is never shown, even in code the student wrote
    return {"path": rel, "function": name, "line_start": a, "line_end": b, "code": code, "citation": f"{rel}:{a}", "own_share": round(own, 2)}


def build(root: Path, blame: dict[str, dict[int, bool]] | None) -> dict:
    """The session payload, or an `available: False` payload with a kind reason (never an error the student sees as a fault)."""
    if blame is None:
        return {"available": False, "reason": NO_GATE_NEEDS_IDENTITY, "scored": False}
    picked = pick(root, blame)
    if not picked:
        return {"available": False, "reason": NO_GATE_NOTHING_FOUND, "scored": False}
    prompt = PROMPT.format(seconds=SECONDS)
    assert not copy_violations(prompt)
    return {
        "available": True, "scored": False, "coaching_only": True, "seconds": SECONDS, "prompt": prompt, **picked,
        # accessibility (section 8): the code is plain text, not an image; typing is offered; a read-aloud text is included
        "input_modes": ["speak", "type"],
        "read_aloud": f"Function {picked['function']}, in {picked['path']}, lines {picked['line_start']} to {picked['line_end']}. "
                      + picked["code"].replace("\n", ". "),
        "rating_prompt": "Was this useful? (yes / no)",
    }
