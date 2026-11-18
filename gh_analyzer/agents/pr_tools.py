"""The four read-only tools the PR agent can call, with budgets and numbered evidence.

`ToolBox.call` never raises. It returns `(evidence_id, text)`; things that are not evidence (an unknown tool,
bad arguments, an exhausted budget, a git failure) come back as `("", message)` and are not logged.
Every text a model can see passes through `safe_text`; file content is redacted before line numbers are added,
and secret files are withheld whole. Reads come from the head commit through `git show`, never the working tree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..repomap import Tier0
from .evidence import EvidenceLog
from .gitread import GitReadError, changed_files, hunks, read_git, valid_ref
from .safe import TRUNCATED, WITHHELD, is_secret_file, safe_text

MAX_READS = 6
READ_CHARS = 4000
MAX_LIST = 60
MAX_RANGE_LINES = 200
MAX_PATH_LEN = 300
MAX_FILE_BYTES = 2_000_000
HUNK_SCAN_CHARS = 4 * READ_CHARS   # hunk text is cut (at a newline) to this before redaction

BUDGET_MSG = "Read budget used up; submit your report."
COLLAPSED_NOTE = "(note: a redacted block was collapsed; line numbers after it may not match the file)"
BAD_PATH_MSG = "That path is not allowed."

_READ_TOOLS = {"read_hunk", "read_file_range"}
_DRIVE_RE = re.compile(r"[A-Za-z]:")

SCHEMAS: list[dict] = [
    {"type": "function", "function": {
        "name": "list_changed_files",
        "description": ("List the files this branch changes, with status and added/removed line counts "
                        f"(at most {MAX_LIST} rows are shown). Does not use the read budget."),
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "read_hunk",
        "description": ("Read one diff hunk of a changed file. `index` starts at 0. "
                        f"Counts as a read: at most {MAX_READS} reads per review; "
                        f"each read shows at most {READ_CHARS} characters."),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Repository-relative path of a changed file."},
            "index": {"type": "integer", "description": "Hunk number, starting at 0."}},
            "required": ["path", "index"]}}},
    {"type": "function", "function": {
        "name": "read_file_range",
        "description": ("Read numbered lines of a file as it is on the branch head (1-based, inclusive). "
                        f"Shows at most {MAX_RANGE_LINES} lines. Counts as a read: at most {MAX_READS} reads per "
                        f"review; each read shows at most {READ_CHARS} characters. Line numbers after a "
                        "redacted block may differ from the file."),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Repository-relative path."},
            "start": {"type": "integer", "description": "First line, starting at 1."},
            "end": {"type": "integer", "description": "Last line, inclusive."}},
            "required": ["path", "start", "end"]}}},
    {"type": "function", "function": {
        "name": "callers_of",
        "description": (f"List the files that import the given file (at most {MAX_LIST} shown). "
                        "Does not use the read budget."),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Repository-relative path."}},
            "required": ["path"]}}},
]


@dataclass
class PRContext:
    root: Path
    base: str
    head: str
    t0: Tier0


class _Fail(Exception):
    """A plain message for the model; not evidence."""


def _path_ok(path: object) -> bool:
    if not isinstance(path, str) or not path or len(path) > MAX_PATH_LEN:
        return False
    if path.startswith(("/", "-")) or _DRIVE_RE.match(path):
        return False
    if any(c in path for c in ("\\", "\0", "\n", "\r")):
        return False
    return not {"..", "."} & set(path.split("/"))


def _int(value: object) -> int | None:
    """Accept 3, 3.0 and "3"; anything else is None."""
    if isinstance(value, bool):
        return None
    try:
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value) if value.is_integer() else None
        if isinstance(value, str):
            s = value.strip()
            if re.fullmatch(r"[+-]?\d+", s):
                return int(s)
    except (ValueError, OverflowError):
        return None
    return None


def _cap_lines(text: str, limit: int) -> str:
    """`text` cut to at most `limit` chars at a newline, so redaction never sees half a line."""
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit)
    return text[:cut + 1] if cut > 0 else text[:limit]


def _cap(text: str) -> str:
    if len(text) <= READ_CHARS:
        return text
    return text[:READ_CHARS - len(TRUNCATED)] + TRUNCATED


def _split(text: str) -> list[str]:
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _listing(header: list[str], rows: list[str]) -> str:
    """Header, as many whole rows as fit (at most MAX_LIST), then `... and N more` counting every row not shown."""
    rows = [safe_text(r) for r in rows]
    total = len(rows)
    out = list(header)
    used = sum(len(h) + 1 for h in header)
    if total <= MAX_LIST and used + sum(len(r) + 1 for r in rows) <= READ_CHARS:
        return "\n".join(out + rows)                   # everything fits: no "... and N more" row is needed
    reserve = len(f"... and {total} more") + 1
    shown = 0
    for r in rows[:MAX_LIST]:
        if used + len(r) + 1 + reserve > READ_CHARS:
            break
        out.append(r)
        used += len(r) + 1
        shown += 1
    if shown < total:
        out.append(f"... and {total - shown} more")
    return "\n".join(out)


class ToolBox:
    def __init__(self, ctx: PRContext, log: EvidenceLog) -> None:
        if not (valid_ref(ctx.base) and valid_ref(ctx.head)):
            raise ValueError("base and head must be valid branch or commit names")
        self.ctx = ctx
        self.log = log
        self.reads = 0
        self._changes = None

    # --- dispatch ---------------------------------------------------------------------

    def call(self, name: str, args: dict) -> tuple[str, str]:
        try:
            return self._call(name, args)
        except _Fail as e:
            return "", str(e)
        except GitReadError as e:
            return "", str(e)
        except Exception:
            return "", "That tool call could not be completed."

    def _call(self, name: str, args: dict) -> tuple[str, str]:
        handlers = {"list_changed_files": self._list, "read_hunk": self._hunk,
                    "read_file_range": self._range, "callers_of": self._callers}
        if not isinstance(name, str) or name not in handlers:
            return "", "Unknown tool. Use list_changed_files, read_hunk, read_file_range or callers_of."
        if not isinstance(args, dict):
            raise _Fail("Tool arguments must be a JSON object.")
        canon, run = handlers[name](args)           # validates arguments; nothing has run or been counted yet
        seen = self.log.lookup_call(name, canon)
        if seen is not None:
            return seen, self.log.get(seen)["result"]
        is_read = name in _READ_TOOLS
        if is_read and self.reads >= MAX_READS:
            return "", BUDGET_MSG
        text = run()                                # may raise: then nothing is logged or counted
        if is_read:
            self.reads += 1
        return self.log.add(name, canon, text), text

    # --- argument helpers -------------------------------------------------------------

    @staticmethod
    def _path(args: dict) -> str:
        path = args.get("path")
        if not _path_ok(path):
            raise _Fail(BAD_PATH_MSG)
        return path

    @staticmethod
    def _num(args: dict, key: str) -> int:
        n = _int(args.get(key))
        if n is None:
            raise _Fail(f"'{key}' must be a whole number.")
        return n

    def _changed(self):
        if self._changes is None:
            self._changes = changed_files(self.ctx.root, self.ctx.base, self.ctx.head)
        return self._changes

    # --- tools ------------------------------------------------------------------------

    def _list(self, args: dict):
        def run() -> str:
            changes = self._changed()
            rows = []
            for c in changes:
                counts = f" (+{c.added}/-{c.removed})" if c.added is not None and c.removed is not None else ""
                name = f"{c.old_path} -> {c.path}" if c.status == "renamed" and c.old_path else c.path
                rows.append(f"{c.status} {name}{counts}")
            return _listing([f"Changed files: {len(changes)}"], rows)
        return {}, run

    def _callers(self, args: dict):
        path = self._path(args)

        def run() -> str:
            t0 = self.ctx.t0
            if path not in t0.files:
                return f"No index entry for {path}."
            if t0.graph_quality == "tree_only":
                return "Import relationships are not available for this repository's languages."
            callers = sorted({src for src, dst in t0.edges if dst == path})
            if not callers:
                return f"No other indexed file imports {path}."
            return _listing([], callers)
        return {"path": path}, run

    def _hunk(self, args: dict):
        path = self._path(args)
        index = self._num(args, "index")

        def run() -> str:
            if is_secret_file(path):
                return WITHHELD
            change = next((c for c in self._changed() if c.path == path), None)
            if change is None:
                return f"{path} is not part of this change."
            if change.status == "binary":
                return f"{path} is a binary file; its content is not shown."
            if change.status == "submodule":
                return f"{path} is a submodule change; there is no text diff to show."
            if self._too_big(change):
                raise _Fail(f"{path} is too large to show.")
            found = hunks(self.ctx.root, self.ctx.base, self.ctx.head, path)
            if not found:
                return f"{path} has no text hunks in this change."
            if not 0 <= index < len(found):
                return f"{path} has {len(found)} hunk(s), numbered from 0; hunk {index} does not exist."
            h = found[index]
            head = f"{path} hunk {index} of {len(found)}\n"
            return head + safe_text(_cap_lines(h.text, HUNK_SCAN_CHARS), path=path, max_chars=READ_CHARS - len(head))
        return {"path": path, "index": index}, run

    def _too_big(self, change) -> bool:
        """True when either side of the change is a blob over MAX_FILE_BYTES (checked before any diff is read)."""
        root, head = self.ctx.root, self.ctx.head
        try:
            base = read_git(root, "merge-base", self.ctx.base, head).strip()
        except GitReadError:
            base = self.ctx.base
        for spec in (f"{head}:{change.path}", f"{base}:{change.old_path or change.path}"):
            try:
                read_git(root, "cat-file", "-e", spec)
            except GitReadError:
                continue                                # this side does not exist (added or deleted file)
            size = read_git(root, "cat-file", "-s", spec).strip()
            if not size.isdigit() or int(size) > MAX_FILE_BYTES:
                return True
        return False

    def _range(self, args: dict):
        path = self._path(args)
        start = self._num(args, "start")
        end = self._num(args, "end")

        def run() -> str:
            if is_secret_file(path):
                return WITHHELD
            spec = f"{self.ctx.head}:{path}"
            try:
                read_git(self.ctx.root, "cat-file", "-e", spec)
            except GitReadError:
                return f"{path} does not exist at head (it may have been deleted)."
            try:
                if read_git(self.ctx.root, "cat-file", "-t", spec).strip() != "blob":
                    raise _Fail(f"Could not read {path}.")
                size = read_git(self.ctx.root, "cat-file", "-s", spec).strip()
                if not size.isdigit():
                    raise _Fail(f"Could not read {path}.")
                if int(size) > MAX_FILE_BYTES:
                    raise _Fail(f"{path} is too large to show.")
                raw = read_git(self.ctx.root, "show", spec)
            except GitReadError:
                raise _Fail(f"Could not read {path}.") from None
            if "\0" in raw:
                return f"{path} looks like a binary file; its content is not shown."
            total = len(_split(raw))
            lines = _split(safe_text(raw, path=path))   # redact first so numbering never splits a secret
            if total == 0:
                return f"{path} is empty."
            lo = max(start, 1)
            if end < lo:
                return "The end line must not be before the start line."
            if lo > len(lines):
                if len(lines) != total:                # a redacted block was collapsed: count what is shown
                    return f"{path} has only {len(lines)} line(s) to show. {COLLAPSED_NOTE}"
                return f"{path} has only {total} line(s)."
            hi = min(end, len(lines), lo + MAX_RANGE_LINES - 1)
            width = len(str(hi))
            body = "\n".join(f"  {n:>{width}}: {lines[n - 1]}" for n in range(lo, hi + 1))
            note = f"\n{COLLAPSED_NOTE}" if len(lines) != total else ""
            return _cap(f"{path} lines {lo}-{hi} of {total}{note}\n{body}")
        return {"path": path, "start": start, "end": end}, run
