"""Read-only git access for the PR agent.

Only an allowlist of read subcommands runs. Refs are validated, every option must be on a small exact-match
allowlist per subcommand (git accepts abbreviated options, so a denylist cannot be right), and
`--no-ext-diff --no-textconv` is added to every diff-producing command so a repository's own diff drivers
never execute (nothing in the repo runs).
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..snapshot import GIT_TIMEOUT_S, _env

ALLOWED = {"diff", "show", "log", "rev-parse", "merge-base", "cat-file", "diff-tree"}
_SAFE_DIFF = {"diff", "show", "log", "diff-tree"}
# Exact flags a caller may pass, per subcommand. Anything starting with "-" that is not listed (or matched by
# _FLAG_PATTERNS) is refused. Arguments after a bare "--" are positional and exempt.
_COMMON = {"--no-color", "-z", "-M", "--numstat", "--raw"}
_FLAGS: dict[str, set[str]] = {
    "diff": _COMMON,
    "show": _COMMON,
    "log": _COMMON | {"--oneline"},
    "diff-tree": _COMMON | {"-r", "--name-only", "--no-commit-id", "--root"},
    "rev-parse": {"--verify", "--short", "--abbrev-ref"},
    "merge-base": {"--is-ancestor"},
    "cat-file": {"-e", "-t", "-s", "-p"},
}
_FLAG_PATTERNS = {"diff": r"-U\d{1,3}", "show": r"-U\d{1,3}", "log": r"-n\d{1,5}"}
_REF_RE = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9._/@^~{}-]*")
_HEADER_RE = re.compile(r"^@@ ", re.MULTILINE)
MAX_REF_LEN = 200
# Placed before every subcommand so a repository's own config cannot start a program: the working tree is
# pointed at the null device (no working-tree file is ever read, so no clean filter runs; commit reads are
# unaffected), and there is no fsmonitor, no hook, no pager and no transport (no lazy fetch).
_HARDEN = [f"--work-tree={os.devnull}", "-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}", "-c", "protocol.allow=never",
           "-c", "core.pager=cat", "-c", "core.untrackedCache=false"]


class GitReadError(Exception):
    """A plain, user-presentable failure reading git."""


@dataclass
class Change:
    path: str                      # always the new path
    status: str                    # added, modified, deleted, renamed, binary, submodule
    added: int | None
    removed: int | None
    old_path: str | None = None    # only for renames


@dataclass
class Hunk:
    index: int
    header: str
    text: str


def valid_ref(ref: str) -> bool:
    if not isinstance(ref, str) or not ref or len(ref) > MAX_REF_LEN:
        return False
    if ".." in ref or "@{" in ref:
        return False
    return _REF_RE.fullmatch(ref) is not None


def _flag_ok(sub: str, a: str) -> bool:
    if a in _FLAGS[sub]:
        return True
    pat = _FLAG_PATTERNS.get(sub)
    return bool(pat and re.fullmatch(pat, a))


def read_git(root: Path, *args: str) -> str:
    if not args or args[0] not in ALLOWED:
        raise GitReadError("That git command is not allowed: the agent only reads.")
    sub, rest = args[0], list(args[1:])
    if any(c in a for a in rest for c in ("\0", "\n", "\r")):
        raise GitReadError("That git argument is not allowed.")
    for a in rest[: rest.index("--")] if "--" in rest else rest:
        if a.startswith("-") and not _flag_ok(sub, a):
            raise GitReadError("That git option is not allowed.")
    safe = ["--no-ext-diff", "--no-textconv"] if sub in _SAFE_DIFF else []
    # Run here rather than through snapshot.git: its text mode turns a lone CR in file content into a newline,
    # which could forge a diff line. We read bytes and decode with errors="replace". stdin is closed so a
    # program git starts can never wait on it.
    try:
        r = subprocess.run(["git", "-C", str(root), *_HARDEN, sub, *safe, *rest], capture_output=True,
                           stdin=subprocess.DEVNULL, timeout=GIT_TIMEOUT_S, env=_env())
    except (subprocess.TimeoutExpired, OSError):
        raise GitReadError(f"git {sub} could not read that: it timed out or git is unavailable.") from None
    if r.returncode != 0:
        raise GitReadError(f"git {sub} could not read that: check the branch names and that this is a git repository.")
    return r.stdout.decode("utf-8", errors="replace")


def _check_refs(*refs: str) -> None:
    for r in refs:
        if not valid_ref(r):
            raise GitReadError("That is not a valid branch or commit name.")


def _numstat(root: Path, spec: str) -> list[tuple[str, str, str, str | None]]:
    """(added, removed, path, old_path) per file, from `--numstat -z`."""
    toks = read_git(root, "diff", "--numstat", "-z", "-M", spec, "--").split("\0")   # "--": spec is a revision
    out, i = [], 0
    while i < len(toks):
        t = toks[i]
        i += 1
        if not t:
            continue
        cols = t.split("\t", 2)
        if len(cols) != 3:
            continue
        if cols[2] == "":                       # rename: the two paths follow as separate fields
            if i + 1 >= len(toks):
                break
            out.append((cols[0], cols[1], toks[i + 1], toks[i]))
            i += 2
        else:
            out.append((cols[0], cols[1], cols[2], None))
    return out


def _raw(root: Path, spec: str) -> dict[str, tuple[str, bool]]:
    """new path -> (status letter, touches a submodule), from `--raw -z`."""
    toks = read_git(root, "diff", "--raw", "-z", "-M", spec, "--").split("\0")
    out, i = {}, 0
    while i < len(toks):
        t = toks[i]
        i += 1
        if not t.startswith(":"):
            continue
        meta = t[1:].split()
        if len(meta) < 5:
            continue
        letter = meta[4][:1]
        n = 2 if letter == "R" else 1
        paths = toks[i:i + n]
        i += n
        if len(paths) == n:
            out[paths[-1]] = (letter, "160000" in meta[:2])
    return out


def changed_files(root: Path, base: str, head: str) -> list[Change]:
    """Files changed on `head` since it forked from `base` (three-dot merge-base semantics), sorted by path."""
    _check_refs(base, head)
    spec = f"{base}...{head}"
    raw = _raw(root, spec)
    changes = []
    for a, r, path, old in _numstat(root, spec):
        letter, sub = raw.get(path, ("M", False))
        if sub:
            status = "submodule"
        elif a == "-" and r == "-":
            status = "binary"
        else:
            status = {"A": "added", "D": "deleted", "R": "renamed"}.get(letter, "modified")
        counted = status != "binary"
        changes.append(Change(path, status, int(a) if counted and a.isdigit() else None,
                              int(r) if counted and r.isdigit() else None, old))
    return sorted(changes, key=lambda c: c.path)


def hunks(root: Path, base: str, head: str, path: str) -> list[Hunk]:
    """The diff hunks of one file, each starting at its `@@` header."""
    _check_refs(base, head)
    out = read_git(root, "diff", "--no-color", "-M", f"{base}...{head}", "--", f":(literal){path}")
    # Split on newline only: other line separators can occur inside file content and must not forge a header
    # (Python's re.M `^` matches only after "\n"). In git's own output a line starting with "@@ " is always a
    # real header (content lines start with a space, "+", "-" or a backslash). Hunks are slices of the output,
    # so no per-line list is built for a large diff.
    starts = [m.start() for m in _HEADER_RE.finditer(out)]
    result: list[Hunk] = []
    for i, s in enumerate(starts):
        text = out[s:starts[i + 1] if i + 1 < len(starts) else len(out)]
        nl = text.find("\n")
        result.append(Hunk(i, text[:nl] if nl >= 0 else text, text))
    return result
