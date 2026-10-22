"""Stage 0 of M03: get from "the student" to "a repository we can read" (FR-3.1).

Two ways in: GitHub OAuth with no scopes (which GitHub treats as read-only access to public data) or a pasted public URL.
Everything that touches the network goes through one `transport` function, so the rate-limit and error behaviour is
testable without GitHub.

Named errors, never a stack trace:
  EC-3.6   private repo       -> 404 looks the same as "does not exist"; we say both and offer the zip / skip path
  EC-3.14  gist / user / org  -> from snapshot.parse_target
  EC-3.15  rate limit         -> wait and retry with backoff; the student sees "GitHub is busy, I'll keep trying"
  EC-3.17  200 repos          -> rank, return the top 8; the rest are searchable by name
"""

from __future__ import annotations

import json
import secrets as _secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Callable

from . import selection, snapshot

API = "https://api.github.com"
AUTHORIZE = "https://github.com/login/oauth/authorize"
TOKEN = "https://github.com/login/oauth/access_token"
BUSY = "GitHub is busy, I'll keep trying."
PRIVATE_NOTE = "Private repositories are not visible with this connection. Paste a link or upload a zip to include one."
MAX_WAIT_S = 60.0
LOC_PER_KB = 20      # a crude estimate used ONLY to order the picker; the real count comes from the snapshot


class IntakeError(Exception):
    """A named, user-presentable failure. `code` is stable; `offer` says what the student can do next."""

    def __init__(self, message: str, code: str = "intake", offer: list[str] | None = None):
        super().__init__(message)
        self.code, self.offer = code, offer or []


class RateLimited(IntakeError):
    def __init__(self, retry_after: float):
        super().__init__(BUSY, "rate_limited", ["wait", "paste_link"])
        self.retry_after = retry_after


Transport = Callable[[str, str, dict, dict | None], tuple[int, dict, object]]


def default_transport(method: str, url: str, headers: dict, params: dict | None = None) -> tuple[int, dict, object]:
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method=method, headers={"Accept": "application/vnd.github+json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read().decode("utf-8", "replace")
            return r.status, {k.lower(): v for k, v in r.headers.items()}, json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8", "replace") or "null")
        except ValueError:
            body = None
        return e.code, {k.lower(): v for k, v in e.headers.items()}, body
    except (urllib.error.URLError, TimeoutError) as e:
        raise IntakeError("I could not reach GitHub. Paste a link to your repository instead.", "unreachable", ["paste_link"]) from e


def new_state() -> str:
    return _secrets.token_urlsafe(24)


def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    """No `scope` parameter on purpose: GitHub then grants read-only access to public information, which is all we ask for."""
    return AUTHORIZE + "?" + urllib.parse.urlencode({"client_id": client_id, "redirect_uri": redirect_uri, "state": state,
                                                     "allow_signup": "false"})


class GitHubClient:
    def __init__(self, token: str | None = None, transport: Transport | None = None, sleep=time.sleep,
                 max_retries: int = 4, clock=time.time):
        self.token, self.transport, self.sleep, self.max_retries, self.clock = token, transport or default_transport, sleep, max_retries, clock
        self.waited = 0.0
        self.on_wait: Callable[[str], None] | None = None

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _get(self, path: str, params: dict | None = None) -> tuple[int, dict, object]:
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            status, headers, body = self.transport("GET", API + path, self._headers(), params)
            limited = status == 429 or (status == 403 and (headers.get("x-ratelimit-remaining") == "0" or "retry-after" in headers))
            if not limited:
                return status, headers, body
            wait = self._wait_for(headers, delay)
            if attempt == self.max_retries or wait > MAX_WAIT_S:
                raise RateLimited(wait)
            if self.on_wait:
                self.on_wait(BUSY)
            self.sleep(wait)
            self.waited += wait
            delay = min(delay * 2, MAX_WAIT_S)
        raise RateLimited(delay)

    def _wait_for(self, headers: dict, fallback: float) -> float:
        if "retry-after" in headers:
            try:
                return float(headers["retry-after"])
            except ValueError:
                pass
        reset = headers.get("x-ratelimit-reset")
        if reset and reset.isdigit():
            return max(0.0, float(reset) - self.clock()) + 1.0
        return fallback

    def exchange_code(self, client_id: str, client_secret: str, code: str) -> str:
        status, _, body = self.transport("POST", TOKEN, {"Accept": "application/json"},
                                         {"client_id": client_id, "client_secret": client_secret, "code": code})
        token = (body or {}).get("access_token") if isinstance(body, dict) else None
        if status != 200 or not token:
            raise IntakeError("GitHub did not accept that sign-in. Try again, or paste a link to your repository.", "oauth_failed", ["paste_link"])
        self.token = token
        return token

    def list_repos(self, resume_titles: list[str] | None = None, top: int = 8) -> dict:
        """The student's public repositories, ranked. Forks are listed (and marked); private repos are never requested."""
        status, _, body = self._get("/user/repos", {"visibility": "public", "affiliation": "owner", "per_page": 100, "sort": "pushed"})
        if status == 401:
            raise IntakeError("GitHub sign-in expired. Connect again, or paste a link.", "unauthorised", ["connect", "paste_link"])
        if status != 200 or not isinstance(body, list):
            raise IntakeError("Could not read your repositories from GitHub. Paste a link instead.", "list_failed", ["paste_link"])
        public = [r for r in body if not r.get("private")]
        metas = [to_ranking_meta(r) for r in public]
        ranked = selection.rank_repos(metas, resume_titles, top=top)
        return {"repos": [{**{k: v for k, v in r.items() if k != "repo"}, "full_name": r["repo"]["full_name"], "is_fork": r["repo"]["is_fork"]}
                          for r in ranked],
                "total_public": len(public), "shown": len(ranked),
                "note": PRIVATE_NOTE}

    def search_repos(self, query: str, resume_titles: list[str] | None = None) -> list[dict]:
        """EC-3.17: the search box for the repos beyond the top eight."""
        everything = self.list_repos(resume_titles, top=10_000)["repos"]
        q = query.strip().lower()
        return [r for r in everything if q in r["name"].lower() or q in r["full_name"].lower()]

    def check_repo(self, owner: str, name: str) -> dict:
        """Does this repository exist and can we read it? Distinguishes the cases we can distinguish (EC-3.6, EC-3.14)."""
        status, _, body = self._get(f"/repos/{owner}/{name}")
        if status == 200 and isinstance(body, dict):
            if body.get("private"):
                raise _private(owner, name)
            return body
        if status in (404, 403, 401):
            raise _private(owner, name)
        raise IntakeError(f"GitHub returned an unexpected answer ({status}) for {owner}/{name}. Try again in a moment.", "github_error", ["retry"])

    def head_sha(self, owner: str, name: str, branch: str | None = None) -> str:
        meta = self.check_repo(owner, name)
        status, _, body = self._get(f"/repos/{owner}/{name}/commits/{branch or meta.get('default_branch', 'HEAD')}")
        if status == 409:
            raise snapshot.EmptyRepo("Nothing to read here yet: the repository has no commits.")
        if status != 200 or not isinstance(body, dict) or "sha" not in body:
            raise IntakeError("Could not read the latest commit of that repository.", "github_error", ["retry"])
        return body["sha"]


def _private(owner: str, name: str) -> IntakeError:
    return IntakeError(f"I cannot read {owner}/{name}. It is private, or the name is wrong. I only ask for access to public repositories.",
                       "private_or_missing", ["upload_zip", "continue_without_repo", "paste_link"])


def to_ranking_meta(r: dict) -> dict:
    created, pushed = _ts(r.get("created_at")), _ts(r.get("pushed_at"))
    span = max(0, (pushed - created).days) if created and pushed else 0
    return {"name": r.get("name", ""), "full_name": r.get("full_name", r.get("name", "")), "is_fork": bool(r.get("fork")),
            "loc": int(r.get("size", 0)) * LOC_PER_KB, "span_days": span,
            "has_readme": bool(r.get("description")), "has_tests": False}


def _ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def resolve(url: str, client: GitHubClient | None = None) -> dict:
    """Turn what the student pasted into {owner, name, repo, sha?}. Network only when a client is given."""
    kind, ref = snapshot.parse_target(url)       # raises SnapshotError with a named message for gists / users / orgs / junk
    if kind == "local":
        return {"kind": "local", "repo": ref.name, "target": str(ref)}
    owner, name = str(ref).split("/", 1)
    out = {"kind": "github", "owner": owner, "name": name, "repo": str(ref), "target": str(ref)}
    if client is not None:
        meta = client.check_repo(owner, name)
        out.update(default_branch=meta.get("default_branch"), is_fork=bool(meta.get("fork")), sha=client.head_sha(owner, name, meta.get("default_branch")))
    return out

