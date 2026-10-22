"""The service layer behind the API: one record per analysed repository, owned by one candidate.

What it adds on top of the pipeline:
  * the snapshot is KEPT, pinned to one SHA, until the student deletes the repo (FR-3.2, EC-3.7, EC-3.8). Identity
    confirmation and the explain gate re-read it instead of cloning again.
  * results are cached per (candidate, repo, SHA), so the same commit is analysed once (spec section 11).
  * every failure ends in the no-repo path, never a dead end (spec section 10, FR-3.18).
  * the `private` half of a result never leaves through anything but `security_notices`, and only for the owner.

It has no web framework in it. api.py is a thin shell over this class.
"""

from __future__ import annotations

import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import contribution, explain_gate, identity, intake, inventory, no_repo, pipeline, snapshot

MAX_CORRECTION_CHARS = 2000
FALLBACK_MESSAGE = "I could not read this repository, so we will do a project round instead. It is just as useful."


class NotFound(KeyError):
    pass


@dataclass
class Record:
    id: str
    candidate_id: str
    target: str
    repo: str
    status: str = "queued"               # queued | running | ready | empty | failed
    pinned_sha: str | None = None
    result: dict | None = None
    error: dict | None = None
    events: list[dict] = field(default_factory=list)
    snap: snapshot.Snapshot | None = None
    storage_uri: str | None = None
    cached: bool = False
    candidates: list[dict] = field(default_factory=list)
    identities: list[str] = field(default_factory=list)
    understanding: dict = field(default_factory=lambda: {"confirmed": False, "accepted_without_correction": None, "correction": None})
    gate_sessions: dict[str, dict] = field(default_factory=dict)
    claims: list[dict] = field(default_factory=list)
    resume_titles: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    fork_of: str | None = None
    created: float = field(default_factory=time.time)


class AnalyzerService:
    def __init__(self, storage_dir: str | Path, client: intake.GitHubClient | None = None, workers: int = 2, sync: bool = False,
                 reference_corpus: str | None = None, snapshotter=snapshot.snapshot):
        self.storage = Path(storage_dir)
        self.storage.mkdir(parents=True, exist_ok=True)
        self.client = client
        self.reference_corpus = reference_corpus
        self._snapshotter = snapshotter
        self._records: dict[str, Record] = {}
        self._cache: dict[tuple[str, str, str], str] = {}
        self._lock = threading.RLock()
        self._pool = None if sync else ThreadPoolExecutor(max_workers=workers)
        self.analyses_run = 0          # for the cache test and for a metric

    # ---- lookup -----------------------------------------------------------------------------------------------

    def get(self, repo_id: str, candidate_id: str) -> Record:
        with self._lock:
            rec = self._records.get(repo_id)
        if rec is None or rec.candidate_id != candidate_id:      # someone else's repo looks exactly like a missing one
            raise NotFound(repo_id)
        return rec

    def _event(self, rec: Record, stage: str, **extra) -> None:
        with self._lock:
            rec.events.append({"stage": stage, "at": round(time.time() - rec.created, 3), **extra})

    # ---- submit and run ---------------------------------------------------------------------------------------

    def submit(self, candidate_id: str, url: str, resume_claims: list[dict] | None = None, resume_titles: list[str] | None = None,
               hints: list[str] | None = None, fork_of: str | None = None, client: intake.GitHubClient | None = None) -> Record:
        """Validate the target and queue the job. Raises intake/snapshot errors that are the student's to fix (a gist, a user page)."""
        info = intake.resolve(url, client or self.client)      # raises with a named message; network only if a client is set
        sha = info.get("sha")
        with self._lock:
            if sha:
                prior = self._cache.get((candidate_id, info["repo"], sha))
                if prior and prior in self._records and self._records[prior].status in ("ready", "empty"):
                    rec = self._records[prior]
                    rec.cached = True
                    self._event(rec, "cache_hit", sha=sha[:12])
                    return rec
            rec = Record(id=str(uuid.uuid4()), candidate_id=candidate_id, target=info["target"], repo=info["repo"],
                         claims=list(resume_claims or []), resume_titles=list(resume_titles or []), hints=list(hints or []), fork_of=fork_of)
            if sha:
                rec.pinned_sha = sha
            self._records[rec.id] = rec
        if self._pool is None:
            self._run(rec)
        else:
            self._pool.submit(self._run, rec)
        return rec

    def _run(self, rec: Record) -> None:
        rec.status = "running"
        self._event(rec, "started")
        workdir = self.storage / rec.id
        try:
            snap = self._snapshotter(rec.target, str(workdir))
        except snapshot.EmptyRepo as exc:
            self._finish_empty(rec, str(exc))
            return
        except (snapshot.SnapshotError, intake.IntakeError) as exc:
            self._finish_failed(rec, str(exc), code=getattr(exc, "code", "snapshot"), offer=getattr(exc, "offer", None))
            return
        except Exception as exc:                                   # spec section 10: everything fails -> no-repo path
            self._finish_failed(rec, FALLBACK_MESSAGE, code="unexpected", detail=repr(exc))
            return
        with self._lock:
            rec.snap, rec.storage_uri, rec.pinned_sha = snap, str(snap.root), snap.sha
        self._event(rec, "snapshot_done", sha=snap.sha[:12])
        try:
            result = pipeline.analyse_snapshot(snap, None, rec.resume_titles, rec.fork_of, self.reference_corpus, rec.claims,
                                               progress=lambda stage: self._event(rec, stage))
        except Exception as exc:
            self._finish_failed(rec, FALLBACK_MESSAGE, code="unexpected", detail=repr(exc))
            return
        with self._lock:
            self.analyses_run += 1
            rec.result = result
            rec.candidates = list(result["student"]["contribution"].get("identity_candidates", []))
            rec.status = "ready"
            self._cache[(rec.candidate_id, rec.repo, snap.sha)] = rec.id
        self._event(rec, "ready")

    def _finish_empty(self, rec: Record, message: str) -> None:
        with self._lock:
            rec.result, rec.status = pipeline.empty_result(message), "empty"
        self._event(rec, "empty")

    def _finish_failed(self, rec: Record, message: str, code: str, offer: list[str] | None = None, detail: str | None = None) -> None:
        with self._lock:
            rec.status = "failed"
            rec.error = {"code": code, "message": message, "offer": offer or ["continue_without_repo"], "detail": detail}
            rec.result = {"student": {"status": "failed", "message": message, "no_repo_path": pipeline.selection.NO_REPO,
                                      "project_round": no_repo.project_round([{"title": t} for t in rec.resume_titles])},
                          "private": {"error_detail": detail}}
        self._event(rec, "failed", code=code)
        shutil.rmtree(self.storage / rec.id, ignore_errors=True)

    def wait(self, repo_id: str, candidate_id: str, timeout: float = 120.0) -> Record:
        rec = self.get(repo_id, candidate_id)
        end = time.time() + timeout
        while rec.status in ("queued", "running") and time.time() < end:
            time.sleep(0.02)
        return rec

    # ---- views ------------------------------------------------------------------------------------------------

    def student_view(self, rec: Record) -> dict:
        base = {"id": rec.id, "status": rec.status, "repo": rec.repo, "pinned_sha": (rec.pinned_sha or "")[:12] or None,
                "cached": rec.cached, "progress": [e["stage"] for e in rec.events]}
        if rec.result:
            student = dict(rec.result["student"])
            student.pop("status", None)
            base.update(student)
            base["understanding_state"] = rec.understanding
            base["identities_confirmed"] = rec.identities
        if rec.status == "failed" and rec.error:
            base["offer"] = rec.error["offer"]
        return base

    def decisions(self, rec: Record) -> list[dict]:
        return (rec.result or {}).get("student", {}).get("decisions", [])

    def questions(self, rec: Record, depth: int | None = None) -> list[dict]:
        s = (rec.result or {}).get("student", {})
        qs = s.get("questions", []) + s.get("supplementary_questions", [])
        return [q for q in qs if depth is None or q["depth"] == depth]

    def security_notices(self, rec: Record) -> list[dict]:
        return (rec.result or {}).get("private", {}).get("secret_notices", [])

    # ---- confirmation steps -----------------------------------------------------------------------------------

    def identity_options(self, rec: Record) -> dict:
        return {"candidates": rec.candidates, "suggested": identity.suggest(rec.candidates, rec.hints), "confirmed": rec.identities}

    def confirm_identities(self, rec: Record, emails: list[str]) -> Record:
        """Fix which commits are the student's, then compute the share (FR-3.6). Re-reads the pinned snapshot; no clone."""
        if rec.snap is None or rec.status != "ready":
            raise identity.IdentityError("This repository has not been read yet.")
        confirmed = identity.confirm(rec.candidates, emails)
        result = pipeline.analyse_snapshot(rec.snap, confirmed, rec.resume_titles, rec.fork_of, self.reference_corpus, rec.claims,
                                           progress=lambda stage: self._event(rec, f"reanalysis:{stage}"))
        with self._lock:
            rec.identities, rec.result = confirmed, result
            rec.gate_sessions.clear()
        return rec

    def confirm_understanding(self, rec: Record, corrected_summary: str | None = None) -> dict:
        """AC-3.7: record whether the read-back was accepted as it stood. A correction is the student's own text and is kept as typed."""
        text = (corrected_summary or "").strip()[:MAX_CORRECTION_CHARS] or None
        rec.understanding = {"confirmed": True, "accepted_without_correction": text is None, "correction": text}
        return rec.understanding

    # ---- explain gate -----------------------------------------------------------------------------------------

    def explain_gate(self, rec: Record) -> dict:
        if rec.snap is None or rec.status != "ready":
            return {"available": False, "reason": "This repository has not been read yet.", "scored": False}
        blame = None
        if rec.identities:
            analysed = inventory.scan(rec.snap.root).analysed
            blame = contribution.blame_lines_by_file(rec.snap.root, analysed, set(rec.identities))
        session = explain_gate.build(rec.snap.root, blame)
        if session.get("available"):
            session["session_id"] = str(uuid.uuid4())
            rec.gate_sessions[session["session_id"]] = {"rating": None, "path": session["path"], "function": session["function"]}
        return session

    def rate_gate(self, rec: Record, session_id: str, useful: bool) -> None:
        """AC-3.9 is a rating, nothing more: it does not feed any score."""
        if session_id not in rec.gate_sessions:
            raise NotFound(session_id)
        rec.gate_sessions[session_id]["rating"] = "useful" if useful else "not_useful"

    # ---- delete -----------------------------------------------------------------------------------------------

    def delete(self, rec: Record) -> None:
        with self._lock:
            self._records.pop(rec.id, None)
            for k in [k for k, v in self._cache.items() if v == rec.id]:
                del self._cache[k]
        if rec.snap is not None:
            rec.snap.cleanup()
        shutil.rmtree(self.storage / rec.id, ignore_errors=True)
        rec.snap, rec.result = None, None

    def metrics(self) -> dict:
        """What AC-3.7 and AC-3.9 are computed from. Counts only; no student content."""
        with self._lock:
            recs = list(self._records.values())
        confirmed = [r for r in recs if r.understanding["confirmed"]]
        rated = [s["rating"] for r in recs for s in r.gate_sessions.values() if s["rating"]]
        timings = sorted(r.result["private"]["audit"]["timings_s"]["total"] for r in recs
                         if r.result and r.result.get("private", {}).get("audit", {}).get("timings_s"))
        return {"repos": len(recs), "understanding_confirmed": len(confirmed),
                "understanding_accepted_without_correction": sum(1 for r in confirmed if r.understanding["accepted_without_correction"]),
                "gate_rated": len(rated), "gate_useful": rated.count("useful"),
                "analysis_seconds_p95": timings[min(len(timings) - 1, int(0.95 * len(timings)))] if timings else None}
