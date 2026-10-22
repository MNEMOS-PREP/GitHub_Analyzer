"""Stage 2 of M03: get a repository onto disk safely and export its history.

The rule that matters most (FR-3.3): nothing inside the repository is ever
executed. We only run `git` itself (clone, log, blame, rev-parse) and read files
as text. No install, no build, no test run, no import of repository code.

Caps (see docs/M03_ALGORITHMS.md for where each number comes from):
  clone timeout 120 s, then one retry at depth 50 · at most 20,000 files ·
  a symlink that resolves outside the repository root is refused.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .inventory import EXCLUDE_DIRS

MAX_FILES = 20_000
SIZE_CAP_BYTES = 500 * 1024 * 1024
CLONE_TIMEOUT_S = 120
CLONE_DEPTH = 500
RETRY_DEPTH = 50
GIT_TIMEOUT_S = 90
MAX_COMMITS = 5000

_REPO_RE = re.compile(r"^(?:https?://)?(?:www\.)?(?:github\.com/)?(?P<owner>[A-Za-z0-9_.-]+)/(?P<name>[A-Za-z0-9_.-]+?)(?:\.git)?/?$")


class SnapshotError(Exception):
    """A named, user-presentable failure."""


class EmptyRepo(SnapshotError):
    pass


@dataclass
class Snapshot:
    root: Path
    sha: str
    default_branch: str
    source: str                       # "local" or "github"
    repo: str                         # owner/name, or the folder name for a local path
    shallow: bool
    total_files: int
    size_bytes: int
    truncated: bool                   # hit a cap, or the history is shallow
    notes: list[str] = field(default_factory=list)
    _tmp: str | None = None

    def cleanup(self) -> None:
        if self._tmp:
            import shutil

            shutil.rmtree(self._tmp, ignore_errors=True)


@dataclass
class Commit:
    sha: str
    name: str
    email: str
    date: datetime
    subject: str
    parents: int
    files: list[tuple[int, int, str]]   # (additions, deletions, path)
    boundary: bool = False              # oldest commit of a shallow clone: its diff is the whole tree


def _env() -> dict:
    env = dict(os.environ)
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1"})
    return env


def git(root: Path, *args: str, timeout: int = GIT_TIMEOUT_S, check: bool = True) -> str:
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, env=_env())
    except subprocess.TimeoutExpired:
        raise SnapshotError(f"git {args[0]} took longer than {timeout}s")
    if check and r.returncode != 0:
        raise SnapshotError(f"git {args[0]} failed: {r.stderr.strip()[:200]}")
    return r.stdout


def parse_target(target: str) -> tuple[str, str | Path]:
    """Decide what the user gave us. Named errors for things that are not a repository (EC-3.14)."""
    t = target.strip()
    p = Path(t)
    if p.is_dir():
        return "local", p.resolve()
    low = t.lower()
    if "gist.github.com" in low:
        raise SnapshotError("That is a gist, not a repository. Paste the link to a repository, like github.com/owner/name.")
    m = re.match(r"^(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/?$", t)
    if m:
        raise SnapshotError(f"'{m.group(1)}' is a user or organisation page. Paste a link to one repository, like github.com/{m.group(1)}/name.")
    m = _REPO_RE.match(t)
    if not m:
        raise SnapshotError("I could not read that as a GitHub repository. Use owner/name or a github.com link.")
    return "github", f"{m.group('owner')}/{m.group('name')}"


def check_safety(root: Path) -> tuple[int, int]:
    """Count files and bytes, and refuse anything that tries to leave the repository root.

    Returns (files, bytes). Raises SnapshotError for a symlink escape or too many files.
    """
    root_real = Path(os.path.realpath(root))
    files = size = 0
    stack = [root]
    while stack:
        cur = stack.pop()
        with os.scandir(cur) as it:
            for e in it:
                if e.name == ".git" and cur == root:
                    continue
                if e.is_symlink():
                    target = Path(os.path.realpath(e.path))
                    if root_real != target and root_real not in target.parents:
                        raise SnapshotError(f"Refused: '{Path(e.path).relative_to(root).as_posix()}' is a link that points outside the repository.")
                    continue
                if e.is_dir(follow_symlinks=False):
                    if e.name in EXCLUDE_DIRS:      # never read, so it cannot hurt us; do not count it
                        continue
                    stack.append(Path(e.path))
                else:
                    files += 1
                    try:
                        size += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
                    if files > MAX_FILES:
                        raise SnapshotError(f"Refused: more than {MAX_FILES:,} files. This looks like a dump, not a project.")
    return files, size


def _clone(repo: str, dest: Path, depth: int) -> None:
    cmd = ["git", "clone", "--depth", str(depth), "--single-branch", "--no-tags", "--no-recurse-submodules",
           f"https://github.com/{repo}.git", str(dest)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=CLONE_TIMEOUT_S, env=_env())
    if r.returncode != 0:
        msg = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "unknown error"
        raise SnapshotError(f"Could not clone {repo}: {msg[:160]}")


def snapshot(target: str, workdir: str | None = None) -> Snapshot:
    kind, ref = parse_target(target)
    notes: list[str] = []
    tmp = None
    if kind == "local":
        root = Path(ref)
        if not (root / ".git").exists():
            raise SnapshotError(f"'{root}' is a folder but not a git repository.")
        repo = root.name
    else:
        tmp = workdir or tempfile.mkdtemp(prefix="ghsnap_")
        root = Path(tmp) / "repo"
        repo = str(ref)
        try:
            _clone(repo, root, CLONE_DEPTH)
        except subprocess.TimeoutExpired:
            notes.append("Big repository: I read only the most recent history.")
            import shutil

            shutil.rmtree(root, ignore_errors=True)
            try:
                _clone(repo, root, RETRY_DEPTH)
            except subprocess.TimeoutExpired:
                raise SnapshotError("The clone timed out twice. Try again later or use a smaller repository.")
    try:
        files, size = check_safety(root)
        try:
            sha = git(root, "rev-parse", "HEAD").strip()
        except SnapshotError:
            raise EmptyRepo("Nothing to read here yet: the repository has no commits.")
        branch = git(root, "rev-parse", "--abbrev-ref", "HEAD", check=False).strip() or "unknown"
        shallow = git(root, "rev-parse", "--is-shallow-repository", check=False).strip() == "true"
        truncated = shallow or size > SIZE_CAP_BYTES
        if shallow:
            notes.append("I read only the recent history of this repository, so contribution figures cover that window.")
        if size > SIZE_CAP_BYTES:
            notes.append("This repository is large; I skipped the data files.")
        return Snapshot(root, sha, branch, kind, repo, shallow, files, size, truncated, notes, tmp)
    except Exception:
        if tmp:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)
        raise


_RENAME_BRACES = re.compile(r"\{[^{}]* => ([^{}]*)\}")


def _norm_path(p: str) -> str:
    p = _RENAME_BRACES.sub(r"\1", p)
    if " => " in p:
        p = p.split(" => ")[-1]
    return p.replace("//", "/").strip()


def _boundary_shas(root: Path) -> set[str]:
    raw = git(root, "rev-parse", "--git-path", "shallow", check=False).strip()
    if not raw:
        return set()
    path = Path(raw) if Path(raw).is_absolute() else root / raw
    try:
        return {ln.strip() for ln in path.read_text().splitlines() if ln.strip()}
    except OSError:
        return set()


def export_log(root: Path) -> list[Commit]:
    """Commits, newest first, with per-file additions and deletions. Merge commits carry no file lines."""
    fmt = "%x1e%H%x1f%an%x1f%ae%x1f%aI%x1f%P%x1f%s"
    out = git(root, "log", f"-n{MAX_COMMITS}", "--numstat", f"--format={fmt}", timeout=GIT_TIMEOUT_S * 2)
    boundary = _boundary_shas(root)
    commits: list[Commit] = []
    for chunk in out.split("\x1e"):
        if not chunk.strip():
            continue
        head, _, rest = chunk.partition("\n")
        parts = head.split("\x1f")
        if len(parts) < 6:
            continue
        sha, name, email, iso, parents, subject = parts[0], parts[1], parts[2], parts[3], parts[4], "\x1f".join(parts[5:])
        files = []
        for ln in rest.splitlines():
            cols = ln.split("\t")
            if len(cols) != 3:
                continue
            a = int(cols[0]) if cols[0].isdigit() else 0     # '-' means a binary file
            d = int(cols[1]) if cols[1].isdigit() else 0
            files.append((a, d, _norm_path(cols[2])))
        commits.append(Commit(sha, name, email.lower(), datetime.fromisoformat(iso), subject,
                              len(parents.split()), files, sha in boundary))
    return commits
