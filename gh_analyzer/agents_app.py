"""The opt-in agents layer: the core M03 app plus the PR agent's routes. Not part of the core.

Run it:   uvicorn --factory gh_analyzer.agents_app:create_app      (same env as gh_analyzer.api, plus
          LLM_API_KEY and the optional LLM_BASE_URL / LLM_MODEL for the PR agent)

This is the single glue module between the core (api.py, service.py, pipeline.py, ...) and gh_analyzer.agents.
The core never imports it, so the core still runs with no agent code loaded.

Rules:
  * the same ownership check (X-Candidate-Id; 401 when missing, 404 when not yours) and the same named-error
    format as api.py; every payload goes through api._ok (guard.assert_clean)
  * no response ever carries the model key, the endpoint URL, a traceback or raw exception text
  * reviews run in the background, at most MAX_CONCURRENT at once across the service, one per repository at a
    time, at most REVIEWS_PER_HOUR per repository per hour
  * jobs live in memory only: at most MAX_JOBS (oldest evicted), dropped when their repository is deleted
  * a review reads the service's own kept copy of the repository; a GitHub-sourced copy may have the two refs
    fetched into it, a local folder is only read
"""

from __future__ import annotations

import os
import re
import threading
import time
import uuid
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from . import api
from .agents.__main__ import _context, _fetch_refs
from .agents.gitread import valid_ref
from .agents.llm import OpenAICompatClient
from .agents.pr_agent import review_pr
from .service import AnalyzerService, NotFound, Record

REVIEWS_PER_HOUR = 5
WINDOW_S = 3600.0
MAX_CONCURRENT = 2
MAX_JOBS = 20

NOT_CONFIGURED = ("The pull request helper is not switched on here yet. "
                  "Ask whoever runs this site to set it up, then try again.")
UNAVAILABLE_REASON = "The pull request helper is not switched on here yet."
BAD_REF = "That branch or commit name is not valid. Use a name like main or feature/login."
NOT_READY = "Wait until this repository has been read, then try again."
BUSY = "A review of this repository is already running. Wait for it to finish."
LIMIT = "You have used the {n} reviews allowed for this repository this hour. Try again later."
FAILED = "The review could not be finished. Nothing in your repository was changed. Try again later."

_REPO_PATH = re.compile(r"^/v1/repos/([^/]+)/?$")


class PRReview(BaseModel):
    base: str
    head: str
    verify: bool = True


@dataclass
class Job:
    id: str
    repo_id: str
    candidate_id: str
    status: str = "running"            # running | done | failed
    report: dict | None = None
    error: dict | None = None
    created: float = field(default_factory=time.time)

    def view(self) -> dict:
        return {"status": self.status, "report": self.report, "error": self.error}


class PRJobs:
    """In-memory job book: capped, owner-scoped, dropped with the repository."""

    def __init__(self) -> None:
        self.jobs: OrderedDict[str, Job] = OrderedDict()
        self.starts: dict[str, deque] = {}
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=MAX_CONCURRENT, thread_name_prefix="pr-agent")

    def running_for(self, repo_id: str) -> bool:
        return any(j.repo_id == repo_id and j.status == "running" for j in self.jobs.values())

    def used(self, repo_id: str, now: float) -> int:
        q = self.starts.setdefault(repo_id, deque())
        while q and now - q[0] >= WINDOW_S:
            q.popleft()
        return len(q)

    def add(self, job: Job, now: float) -> None:
        self.jobs[job.id] = job
        self.starts.setdefault(job.repo_id, deque()).append(now)
        while len(self.jobs) > MAX_JOBS:
            finished = next((k for k, j in self.jobs.items() if j.status != "running"), None)
            self.jobs.pop(finished if finished is not None else next(iter(self.jobs)))

    def drop_repo(self, repo_id: str) -> None:
        with self.lock:
            for k in [k for k, j in self.jobs.items() if j.repo_id == repo_id]:
                del self.jobs[k]
            self.starts.pop(repo_id, None)


def _configured() -> bool:
    return bool(os.environ.get("LLM_API_KEY", "").strip())


def _failed() -> dict:
    return {"code": "agent_failed", "message": FAILED, "offer": []}


def create_app(service: AnalyzerService | None = None,
               agent_client_factory: Callable[[], object] | None = None, **kw) -> FastAPI:
    """The core app (gh_analyzer.api.create_app, unchanged) with the PR agent routes added."""
    svc = service or AnalyzerService(os.environ.get("GHA_STORAGE", "gha_storage"))
    app = api.create_app(service=svc, **kw)
    make_client = agent_client_factory or OpenAICompatClient
    book = PRJobs()
    app.state.pr_jobs = book

    def who(x_candidate_id: str | None) -> str:
        if not x_candidate_id:
            raise HTTPException(401, "missing X-Candidate-Id")
        return x_candidate_id

    def mine(repo_id: str, candidate: str) -> Record:
        try:
            return svc.get(repo_id, candidate)
        except NotFound:
            raise HTTPException(404, "not found")

    def still_there(job: Job) -> bool:
        try:
            svc.get(job.repo_id, job.candidate_id)
            return True
        except NotFound:
            return False

    def run(job: Job, rec: Record, base: str, head: str, verify: bool) -> None:
        try:
            snap = rec.snap
            if snap is None:
                raise RuntimeError("snapshot gone")
            _fetch_refs(snap, base, head)            # GitHub-sourced service copy only; a local folder is read as is
            t0, tier1, tier1_tokens = _context(snap.root)
            client = make_client()
            report = review_pr(snap.root, base, head, t0, tier1, client, verify=verify)
            report.setdefault("tokens", {})["tier1"] = int(tier1_tokens)
            report.setdefault("notes", [])
            with book.lock:
                job.report, job.status = report, "done"
        except Exception:  # noqa: BLE001 - a named error only; the raw text never leaves
            with book.lock:
                job.report, job.error, job.status = None, _failed(), "failed"
        if not still_there(job):                     # the repository was deleted while the review ran
            book.drop_repo(job.repo_id)

    @app.middleware("http")
    async def _drop_jobs_on_delete(request: Request, call_next):
        response = await call_next(request)
        if request.method == "DELETE" and response.status_code == 204:
            m = _REPO_PATH.match(request.url.path)
            if m:
                book.drop_repo(m.group(1))
        return response

    # Under `github`, not `agents`: the site shell routes /v1/<first noun> to one module and `agents` is M06's.
    # `pr-helper` is not one of api.py's /v1/github/{authorize,callback,repos}, and none of those take a parameter.
    @app.get("/v1/github/pr-helper/status")
    def agents_status(x_candidate_id: str | None = Header(None)):
        who(x_candidate_id)
        ready = _configured()
        return api._ok({"pr": {"available": ready, "reason": None if ready else UNAVAILABLE_REASON}})

    @app.post("/v1/repos/{repo_id}/agents/pr")
    def start_pr_review(repo_id: str, body: PRReview, x_candidate_id: str | None = Header(None)):
        candidate = who(x_candidate_id)
        rec = mine(repo_id, candidate)
        if not _configured():
            return api._err(503, "agent_not_configured", NOT_CONFIGURED, [])
        if not (valid_ref(body.base) and valid_ref(body.head)):
            return api._err(422, "invalid_ref", BAD_REF, [])
        if rec.status != "ready" or rec.snap is None:
            return api._err(422, "not_ready", NOT_READY, [])
        now = time.monotonic()
        with book.lock:
            if book.running_for(rec.id):
                return api._err(409, "busy", BUSY, [])
            if book.used(rec.id, now) >= REVIEWS_PER_HOUR:
                wait = int(WINDOW_S - (now - book.starts[rec.id][0])) + 1
                return api._err(429, "limit", LIMIT.format(n=REVIEWS_PER_HOUR), [],
                                headers={"Retry-After": str(max(wait, 1))})
            job = Job(id=str(uuid.uuid4()), repo_id=rec.id, candidate_id=candidate)
            book.add(job, now)
        book.pool.submit(run, job, rec, body.base, body.head, body.verify)
        return api._ok({"job_id": job.id, "status": "running"}, 202)

    @app.get("/v1/repos/{repo_id}/agents/pr/{job_id}")
    def get_pr_review(repo_id: str, job_id: str, x_candidate_id: str | None = Header(None)):
        candidate = who(x_candidate_id)
        rec = mine(repo_id, candidate)
        with book.lock:
            job = book.jobs.get(job_id)
            if job is None or job.repo_id != rec.id or job.candidate_id != candidate:
                raise HTTPException(404, "not found")
            view = job.view()
        return api._ok(view)

    return app
