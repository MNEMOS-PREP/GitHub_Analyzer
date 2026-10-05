"""Fork awareness (FR-3.8): when a repository is a fork, analyse only what the student added on top of it.

Without this, a student who forked a 10,000-line project and wrote 300 lines is shown as the author of 3% of it, and the
other 97% is read as someone else's work they never touched. With it, commits that are reachable from the upstream
repository's tip are left out of the contribution count and out of the blame denominator, and the read-back says what the
student added to what.

How the parent is found, in order:
  1. the caller says so (`fork_of`);
  2. the GitHub API's `parent` field, for a repository given as owner/name (one unauthenticated request, 8 s timeout,
     silent on failure; GITHUB_TOKEN is used if set);
  3. a git remote named `upstream` that points at github.com.

How the fork point is found: for a repository we cloned ourselves, fetch the parent's recent history into it. For a
repository on the student's own disk we NEVER fetch: analysing a repository must not change it. We only use refs it
already has (`upstream/<branch>`). If no parent history is reachable, we say we could not tell where it forked and
analyse the whole repository, rather than guess.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .snapshot import git

API_TIMEOUT_S = 8
FETCH_DEPTH = 500
_GH_URL = re.compile(r"github\.com[:/]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
_PARENT_REFS = ("upstream/HEAD", "upstream/main", "upstream/master", "upstream/develop")


@dataclass
class ForkInfo:
    is_fork: bool = False
    parent: str | None = None              # owner/name
    how: str | None = None                 # "given", "github", "remote"
    base_sha: str | None = None            # the last commit both share
    upstream_shas: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.is_fork and self.base_sha is not None

    def to_dict(self) -> dict:
        return {"is_fork": self.is_fork, "parent": self.parent, "how": self.how,
                "base_sha": (self.base_sha or "")[:12] or None, "upstream_commits": len(self.upstream_shas)}


def parse_github(url: str) -> str | None:
    m = _GH_URL.search(url.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def _api_parent(repo: str) -> str | None:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "mnemos-gh-analyzer"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{repo}", headers=headers)
        with urllib.request.urlopen(req, timeout=API_TIMEOUT_S) as r:        # fixed https host
            data = json.load(r)
    except Exception:
        return None
    parent = data.get("parent") if data.get("fork") else None
    return parent.get("full_name") if isinstance(parent, dict) else None


def _remote_parent(root: Path) -> str | None:
    url = git(root, "remote", "get-url", "upstream", check=False).strip()
    return parse_github(url) if url else None


def _local_parent_tip(root: Path) -> str | None:
    for ref in _PARENT_REFS:
        sha = git(root, "rev-parse", "--verify", "--quiet", ref, check=False).strip()
        if sha:
            return sha
    return None


def _fetch_parent_tip(root: Path, parent: str) -> str | None:
    try:
        git(root, "fetch", "--depth", str(FETCH_DEPTH), "--no-tags", "--quiet", f"https://github.com/{parent}.git", "HEAD",
            timeout=120)
    except Exception:
        return None
    return git(root, "rev-parse", "--verify", "--quiet", "FETCH_HEAD", check=False).strip() or None


def detect(root: Path, repo: str, source: str, fork_of: str | None = None, allow_fetch: bool | None = None,
           use_api: bool = True) -> ForkInfo:
    """`source` is "github" (we cloned it into a temp folder) or "local" (the student's own disk)."""
    info = ForkInfo()
    parent, how = None, None
    if fork_of:
        parent, how = parse_github(fork_of) or fork_of, "given"
    if not parent and source == "github" and use_api:
        parent, how = _api_parent(repo), "github"
    if not parent:
        parent, how = _remote_parent(root), "remote"
    if not parent:
        return info
    info.is_fork, info.parent, info.how = True, parent, how
    fetch_ok = (source == "github") if allow_fetch is None else allow_fetch
    tip = _local_parent_tip(root)
    if not tip and fetch_ok:
        tip = _fetch_parent_tip(root, parent)
    if not tip:
        info.notes.append(f"This looks like a fork of {parent}, but I could not read its history, so I cannot tell what you "
                          "added. I analysed the whole repository.")
        return info
    base = git(root, "merge-base", "HEAD", tip, check=False).strip()
    if not base:
        info.notes.append(f"This looks like a fork of {parent}, but I could not find where it forked. "
                          "I analysed the whole repository.")
        return info
    info.base_sha = base
    info.upstream_shas = set(git(root, "rev-list", tip, check=False).split())
    info.notes.append(f"This is a fork of {parent}. I counted only what was added on top of it.")
    return info


def statement(info: ForkInfo, added_lines: int, own_commits: int) -> str:
    return (f"You added about {added_lines:,} lines in {own_commits} commit{'s' if own_commits != 1 else ''} "
            f"to a fork of {info.parent}.")
