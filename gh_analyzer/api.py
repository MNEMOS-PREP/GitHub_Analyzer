"""The HTTP surface of M03 (spec section 7). A thin shell: every decision lives in service.py.

Run it:   uvicorn --factory gh_analyzer.api:create_app     (storage in $GHA_STORAGE or ./gha_storage;
          OAuth from $GITHUB_CLIENT_ID, $GITHUB_CLIENT_SECRET, $GITHUB_REDIRECT_URI, and optional)

Rules the shell enforces:
  * every route needs `X-Candidate-Id` (the platform's auth layer sets it); a repo is visible only to its owner
  * every payload goes through guard.assert_clean before it is sent (AC-3.1)
  * `private` data (secret notices) leaves through exactly one route, for the owner
  * errors are named, with a list of what the student can do next, and never a stack trace
"""

from __future__ import annotations

import os
from typing import Iterator

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import guard, identity, intake, snapshot
from .service import AnalyzerService, NotFound, Record


class NewRepo(BaseModel):
    url: str | None = None
    repo_id: str | None = Field(None, description="owner/name, as listed by /v1/github/repos")
    resume_claims: list[dict] = []
    resume_titles: list[str] = []
    hints: list[str] = []
    fork_of: str | None = None


class Identities(BaseModel):
    emails: list[str]


class Understanding(BaseModel):
    corrected_summary: str | None = None


class GateRating(BaseModel):
    session_id: str
    useful: bool


def _ok(payload, status: int = 200) -> JSONResponse:
    try:
        guard.assert_clean(payload)
    except guard.LeakError:
        # fail closed: nothing is sent, and the reason is not shown to the client
        raise HTTPException(500, "internal error")
    return JSONResponse(payload, status_code=status)


def _err(status: int, code: str, message: str, offer: list[str] | None = None, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, "offer": offer or []}}, status_code=status, headers=headers)


_STATUS = {"private_or_missing": 404, "rate_limited": 503, "unreachable": 502, "unauthorised": 401, "oauth_failed": 400}


def create_app(service: AnalyzerService | None = None, client_id: str | None = None, client_secret: str | None = None,
               redirect_uri: str | None = None, client_factory=intake.GitHubClient) -> FastAPI:
    svc = service or AnalyzerService(os.environ.get("GHA_STORAGE", "gha_storage"))
    client_id = client_id or os.environ.get("GITHUB_CLIENT_ID")
    client_secret = client_secret or os.environ.get("GITHUB_CLIENT_SECRET")
    redirect_uri = redirect_uri or os.environ.get("GITHUB_REDIRECT_URI")
    app = FastAPI(title="M03 GitHub Analyzer", docs_url=None, redoc_url=None)
    states: dict[str, str] = {}              # oauth state -> candidate
    tokens: dict[str, str] = {}              # candidate -> token (memory only: not persisted, not logged)

    def who(x_candidate_id: str | None) -> str:
        if not x_candidate_id:
            raise HTTPException(401, "missing X-Candidate-Id")
        return x_candidate_id

    def mine(repo_id: str, candidate: str) -> Record:
        try:
            return svc.get(repo_id, candidate)
        except NotFound:
            raise HTTPException(404, "not found")

    def client_for(candidate: str) -> intake.GitHubClient:
        c = client_factory(tokens.get(candidate))
        return c

    @app.exception_handler(intake.IntakeError)
    async def _intake_error(_: Request, exc: intake.IntakeError):
        headers = {"Retry-After": str(int(exc.retry_after))} if isinstance(exc, intake.RateLimited) else None
        return _err(_STATUS.get(exc.code, 400), exc.code, str(exc), exc.offer, headers)

    @app.exception_handler(snapshot.SnapshotError)
    async def _snapshot_error(_: Request, exc: snapshot.SnapshotError):
        return _err(400, "not_a_repository", str(exc), ["paste_link", "continue_without_repo"])

    @app.exception_handler(identity.IdentityError)
    async def _identity_error(_: Request, exc: identity.IdentityError):
        return _err(400, "identity", str(exc))

    # ---- GitHub connection ------------------------------------------------------------------------------------

    @app.get("/v1/github/authorize")
    def authorize(x_candidate_id: str | None = Header(None)):
        candidate = who(x_candidate_id)
        if not (client_id and redirect_uri):
            return _err(503, "oauth_not_configured", "Connecting to GitHub is not set up here. Paste a link to your repository instead.", ["paste_link"])
        state = intake.new_state()
        states[state] = candidate
        return RedirectResponse(intake.authorize_url(client_id, redirect_uri, state), status_code=307)

    @app.get("/v1/github/callback")
    def callback(code: str, state: str, x_candidate_id: str | None = Header(None)):
        candidate = who(x_candidate_id)
        if states.pop(state, None) != candidate or not (client_id and client_secret):
            return _err(400, "oauth_state", "That sign-in link has expired. Start again, or paste a link to your repository.", ["connect", "paste_link"])
        c = client_factory(None)
        tokens[candidate] = c.exchange_code(client_id, client_secret, code)
        return _ok({"connected": True})

    @app.get("/v1/github/repos")
    def repos(x_candidate_id: str | None = Header(None), q: str | None = None, resume_title: list[str] = Query(default=[])):
        candidate = who(x_candidate_id)
        if candidate not in tokens:
            return _err(401, "not_connected", "Connect GitHub, or paste a link to your repository.", ["connect", "paste_link"])
        c = client_for(candidate)
        return _ok({"repos": c.search_repos(q, resume_title)} if q else c.list_repos(resume_title))

    # ---- repositories -----------------------------------------------------------------------------------------

    @app.post("/v1/repos")
    def create_repo(body: NewRepo, x_candidate_id: str | None = Header(None)):
        candidate = who(x_candidate_id)
        target = body.url or body.repo_id
        if not target:
            return _err(422, "missing_target", "Give me a repository link, or choose one from your list.", ["paste_link"])
        rec = svc.submit(candidate, target, body.resume_claims, body.resume_titles, body.hints, body.fork_of,
                         client=client_for(candidate) if candidate in tokens else None)
        return _ok({"id": rec.id, "status": rec.status, "cached": rec.cached}, 202)

    @app.get("/v1/repos/{repo_id}")
    def get_repo(repo_id: str, x_candidate_id: str | None = Header(None)):
        return _ok(svc.student_view(mine(repo_id, who(x_candidate_id))))

    @app.get("/v1/repos/{repo_id}/stream")
    def stream(repo_id: str, x_candidate_id: str | None = Header(None)):
        rec = mine(repo_id, who(x_candidate_id))

        def events() -> Iterator[str]:
            import json
            import time

            sent = 0
            while True:
                while sent < len(rec.events):
                    yield f"data: {json.dumps({'stage': rec.events[sent]['stage'], 'status': rec.status})}\n\n"
                    sent += 1
                if rec.status not in ("queued", "running"):
                    yield f"event: done\ndata: {json.dumps({'status': rec.status})}\n\n"
                    return
                time.sleep(0.1)

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/v1/repos/{repo_id}/identities")
    def identity_options(repo_id: str, x_candidate_id: str | None = Header(None)):
        return _ok(svc.identity_options(mine(repo_id, who(x_candidate_id))))

    @app.post("/v1/repos/{repo_id}/identities/confirm")
    def confirm_identities(repo_id: str, body: Identities, x_candidate_id: str | None = Header(None)):
        rec = svc.confirm_identities(mine(repo_id, who(x_candidate_id)), body.emails)
        return _ok(svc.student_view(rec))

    @app.post("/v1/repos/{repo_id}/understanding/confirm")
    def confirm_understanding(repo_id: str, body: Understanding, x_candidate_id: str | None = Header(None)):
        return _ok(svc.confirm_understanding(mine(repo_id, who(x_candidate_id)), body.corrected_summary))

    @app.get("/v1/repos/{repo_id}/decisions")
    def decisions(repo_id: str, x_candidate_id: str | None = Header(None)):
        return _ok({"decisions": svc.decisions(mine(repo_id, who(x_candidate_id)))})

    @app.get("/v1/repos/{repo_id}/questions")
    def questions(repo_id: str, depth: int | None = Query(None, ge=0, le=3), x_candidate_id: str | None = Header(None)):
        return _ok({"questions": svc.questions(mine(repo_id, who(x_candidate_id)), depth)})

    @app.post("/v1/repos/{repo_id}/explain-gate")
    def explain_gate_(repo_id: str, x_candidate_id: str | None = Header(None)):
        return _ok(svc.explain_gate(mine(repo_id, who(x_candidate_id))), 201)

    @app.post("/v1/repos/{repo_id}/explain-gate/rating")
    def rate_gate(repo_id: str, body: GateRating, x_candidate_id: str | None = Header(None)):
        try:
            svc.rate_gate(mine(repo_id, who(x_candidate_id)), body.session_id, body.useful)
        except NotFound:
            raise HTTPException(404, "not found")
        return _ok({"ok": True})

    @app.get("/v1/repos/{repo_id}/security-notices")
    def security_notices(repo_id: str, x_candidate_id: str | None = Header(None)):
        # private, self-only: the same ownership check as every route, and the only route that returns the private half
        return _ok({"notices": svc.security_notices(mine(repo_id, who(x_candidate_id)))})

    @app.delete("/v1/repos/{repo_id}", status_code=204)
    def delete_repo(repo_id: str, x_candidate_id: str | None = Header(None)):
        svc.delete(mine(repo_id, who(x_candidate_id)))

    return app
