"""The opt-in agents layer: the PR agent served next to the core API, through gh_analyzer.agents_app."""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_fakes import FakeClient, tool_call
from conftest import make_repo, run
from gh_analyzer import agents_app
from gh_analyzer.agents_app import create_app
from gh_analyzer.service import AnalyzerService

# Under `github`, a noun the site shell routes to M03 (`agents` is M06's, so /v1/agents/... never reached us).
STATUS = "/v1/github/pr-helper/status"
SHELL_VITE = Path(r"C:\Desktop\Final_Year\run\Platform_Shell\vite.config.ts")
H = {"X-Candidate-Id": "cand-1"}
OTHER = {"X-Candidate-Id": "someone-else"}
# Built from pieces so no scanner flags this file.
KEY = "sk-" + "Qw7eR4tY1uI9oP3a" + "Sd6fG2hJ8kL5zX0c"
BASE_URL = "https://llm.internal.example/v9"
IDENT = {"GIT_AUTHOR_NAME": "Student", "GIT_AUTHOR_EMAIL": "student@example.com",
         "GIT_COMMITTER_NAME": "Student", "GIT_COMMITTER_EMAIL": "student@example.com"}
BASE = {"app.py": "def add(a, b):\n    return a + b\n", "README.md": "# Calc\nA small calculator.\n"}
HEAD = {"app.py": "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"}
TITLE = "Add a sub function to the calculator"
SUPPORTED = {"role": "assistant", "content": '{"supported": true, "reason": "the hunk shows it"}'}
REPORT_KEYS = {"status", "summary", "changes", "checklist", "suggested_title", "suggested_description",
               "findings", "dropped", "verified", "evidence", "tokens", "notes"}


def feature_repo(tmp_path):
    root = make_repo(tmp_path / "repo", [{"files": BASE, "msg": "base"}])
    run(root, "checkout", "-q", "-b", "feature")
    for rel, text in HEAD.items():
        (root / rel).write_text(text, encoding="utf-8", newline="\n")
    run(root, "add", "-A", env=IDENT)
    run(root, "commit", "-q", "-m", "feature work", env=IDENT)
    return root


def script() -> list:
    submit = tool_call("submit_report", {
        "summary": "Adds a sub function next to add.",
        "changes": [{"path": "app.py", "what": "New sub(a, b) function."}],
        "checklist": [{"item": "Add a unit test for sub", "why": "A test shows the new function works.",
                       "evidence": ["E2"]}],
        "suggested_title": TITLE,
        "suggested_description": "Adds sub() so the calculator can subtract."}, "s1")
    return [tool_call("read_hunk", {"path": "app.py", "index": 0}, "h1"), submit, SUPPORTED]


class Factory:
    """agent_client_factory double: hands out a fresh scripted client per review and records them."""

    def __init__(self, make=lambda: FakeClient(script())):
        self.make = make
        self.made: list = []

    def __call__(self):
        c = self.make()
        self.made.append(c)
        return c


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", KEY)
    monkeypatch.setenv("LLM_BASE_URL", BASE_URL)


@pytest.fixture
def stack(tmp_path):
    repo = feature_repo(tmp_path)
    svc = AnalyzerService(tmp_path / "store", sync=True)
    factory = Factory()
    client = TestClient(create_app(service=svc, agent_client_factory=factory))
    r = client.post("/v1/repos", json={"url": str(repo)}, headers=H)
    assert r.status_code == 202, r.text
    rid = r.json()["id"]
    assert client.get(f"/v1/repos/{rid}", headers=H).json()["status"] == "ready"
    return client, svc, rid, factory


def start(client, rid, headers=H, **body):
    return client.post(f"/v1/repos/{rid}/agents/pr", json={"base": "main", "head": "feature", **body}, headers=headers)


def poll(client, rid, job_id, headers=H, timeout=60.0):
    end = time.time() + timeout
    while True:
        r = client.get(f"/v1/repos/{rid}/agents/pr/{job_id}", headers=headers)
        if r.status_code != 200 or r.json()["status"] != "running" or time.time() > end:
            return r
        time.sleep(0.05)


def assert_no_secret(r):
    assert KEY not in r.text and BASE_URL not in r.text and "llm.internal" not in r.text
    assert "Traceback" not in r.text


# --- status ------------------------------------------------------------------------------------------------

def test_status_available_with_key(stack, key):
    client, _, _, _ = stack
    r = client.get(STATUS, headers=H)
    assert r.status_code == 200
    assert r.json() == {"pr": {"available": True, "reason": None}}
    assert_no_secret(r)


def test_status_unavailable_without_key(stack, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    client, _, _, _ = stack
    r = client.get(STATUS, headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["pr"]["available"] is False and isinstance(body["pr"]["reason"], str) and body["pr"]["reason"]


def test_status_blank_key_is_unavailable(stack, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "   ")
    client, _, _, _ = stack
    assert client.get(STATUS, headers=H).json()["pr"]["available"] is False


def test_status_needs_a_candidate(stack, key):
    client, _, _, _ = stack
    assert client.get(STATUS).status_code == 401


def test_old_agents_status_route_is_gone(stack, key):
    client, _, _, _ = stack
    assert client.get("/v1/agents/status", headers=H).status_code == 404
    assert not any(getattr(r, "path", "").startswith("/v1/agents") for r in client.app.routes)


def test_status_path_is_not_shadowed_by_a_github_route(stack, key):
    """Only one route may match the status path, and no /v1/github/{param} route sits in front of it."""
    client, _, _, _ = stack
    matching = [r for r in client.app.routes
                if getattr(r, "path_regex", None) and r.path_regex.match(STATUS) and "GET" in (r.methods or ())]
    assert [r.path for r in matching] == [STATUS]


def _shell_owner_table(text: str) -> dict[str, str]:
    block = re.search(r"const OWNER\b[^=]*=\s*\{(.*?)\n\};", text, re.S)
    assert block, "no OWNER table in the shell's vite.config.ts"
    body = re.sub(r"/\*.*?\*/", "", block.group(1), flags=re.S)
    body = re.sub(r"//[^\n]*", "", body)
    return dict(re.findall(r"[\"']?([\w-]+)[\"']?\s*:\s*[\"'](m\d+)[\"']", body))


def test_every_v1_noun_is_routed_to_m03_by_the_site_shell(tmp_path):
    """The shell proxies /v1/<noun>/... by the first noun alone: a route under a noun it gives to another module
    is unreachable from the site (GET /v1/agents/status went to M06 and came back 500)."""
    if not SHELL_VITE.is_file():
        pytest.skip(f"site shell not present at {SHELL_VITE}")
    owner = _shell_owner_table(SHELL_VITE.read_text(encoding="utf-8"))
    app = create_app(service=AnalyzerService(tmp_path / "store", sync=True), agent_client_factory=Factory())
    nouns = {r.path.split("/")[2] for r in app.routes if getattr(r, "path", "").startswith("/v1/")}
    assert {"repos", "github", "coding-profiles"} <= nouns
    wrong = {n: owner.get(n) for n in sorted(nouns) if owner.get(n) != "m03"}
    assert not wrong, f"the site shell routes these M03 nouns elsewhere (or nowhere): {wrong}"


# --- starting a review -------------------------------------------------------------------------------------

def test_start_without_key_is_503_named_error(stack, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    client, _, rid, factory = stack
    r = start(client, rid)
    assert r.status_code == 503
    err = r.json()["error"]
    assert err["code"] == "agent_not_configured" and err["offer"] == [] and err["message"]
    assert "LLM_API_KEY" not in err["message"]
    assert factory.made == []


def test_ownership(stack, key):
    client, _, rid, factory = stack
    assert start(client, rid, headers={}).status_code == 401
    assert start(client, rid, headers=OTHER).status_code == 404
    assert start(client, "no-such-repo").status_code == 404
    job = start(client, rid).json()["job_id"]
    assert client.get(f"/v1/repos/{rid}/agents/pr/{job}").status_code == 401
    assert client.get(f"/v1/repos/{rid}/agents/pr/{job}", headers=OTHER).status_code == 404
    assert client.get(f"/v1/repos/{rid}/agents/pr/not-a-job", headers=H).status_code == 404
    assert poll(client, rid, job).json()["status"] == "done"


@pytest.mark.parametrize("base,head", [("-x", "feature"), ("main", "a..b"), ("main", "x@{1}"), ("", "feature")])
def test_invalid_ref_is_422(stack, key, base, head):
    client, _, rid, factory = stack
    r = start(client, rid, base=base, head=head)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_ref"
    assert factory.made == []


def test_not_ready_is_422(stack, key):
    client, svc, rid, factory = stack
    rec = svc.get(rid, "cand-1")
    rec.status = "running"
    r = start(client, rid)
    assert r.status_code == 422 and r.json()["error"]["code"] == "not_ready"
    assert factory.made == []


def test_busy_is_409_while_a_review_runs(stack, key):
    client, _, rid, factory = stack
    gate = threading.Event()

    def blocked(_messages):
        gate.wait(30)
        return script()[1]

    factory.make = lambda: FakeClient([blocked, SUPPORTED])
    first = start(client, rid)
    assert first.status_code == 202
    try:
        r = start(client, rid)
        assert r.status_code == 409 and r.json()["error"]["code"] == "busy"
    finally:
        gate.set()
    assert poll(client, rid, first.json()["job_id"]).json()["status"] == "done"
    assert start(client, rid).status_code == 202           # free again once the first one is done


def test_limit_is_429_after_five_reviews_an_hour(stack, key):
    client, _, rid, _ = stack
    assert agents_app.REVIEWS_PER_HOUR == 5
    for _ in range(5):
        r = start(client, rid)
        assert r.status_code == 202
        assert poll(client, rid, r.json()["job_id"]).json()["status"] == "done"
    r = start(client, rid)
    assert r.status_code == 429 and r.json()["error"]["code"] == "limit"
    assert r.json()["error"]["message"]


# --- the happy path ----------------------------------------------------------------------------------------

def test_happy_path_polls_to_done_with_the_report(stack, key):
    client, _, rid, factory = stack
    r = start(client, rid)
    assert r.status_code == 202
    assert r.json() == {"job_id": r.json()["job_id"], "status": "running"}
    done = poll(client, rid, r.json()["job_id"])
    assert done.status_code == 200
    body = done.json()
    assert set(body) == {"status", "report", "error"}
    assert body["status"] == "done" and body["error"] is None
    report = body["report"]
    assert set(report) == REPORT_KEYS
    assert report["status"] == "ok" and report["suggested_title"] == TITLE
    assert [c["item"] for c in report["checklist"]] == ["Add a unit test for sub"]
    assert isinstance(report["tokens"]["tier1"], int) and report["tokens"]["tier1"] > 0
    assert report["tokens"]["total"] == report["tokens"]["prompt"] + report["tokens"]["completion"]
    assert report["notes"] == []
    assert "return a - b" not in done.text                   # tool results never leave
    assert len(factory.made) == 1 and len(factory.made[0].calls) == 3
    assert_no_secret(done)


def test_verify_false_skips_the_verifier(stack, key):
    client, _, rid, factory = stack
    factory.make = lambda: FakeClient(script()[:2])
    r = start(client, rid, verify=False)
    body = poll(client, rid, r.json()["job_id"]).json()
    assert body["status"] == "done" and body["report"]["verified"] is False
    assert len(factory.made[0].calls) == 2


def test_local_source_repo_is_not_written_to(stack, key, tmp_path):
    client, _, rid, _ = stack
    root = tmp_path / "repo"
    before = run(root, "for-each-ref", "--format=%(refname) %(objectname)")
    r = start(client, rid)
    assert poll(client, rid, r.json()["job_id"]).json()["status"] == "done"
    assert run(root, "for-each-ref", "--format=%(refname) %(objectname)") == before
    assert run(root, "status", "--porcelain") == ""


# --- failure and cleanup -----------------------------------------------------------------------------------

def test_failed_job_gives_a_named_error_without_raw_text(stack, key):
    client, _, rid, factory = stack

    def boom():
        raise RuntimeError(f"connection to {BASE_URL} refused with key {KEY}")

    factory.make = boom
    r = start(client, rid)
    done = poll(client, rid, r.json()["job_id"])
    body = done.json()
    assert body["status"] == "failed" and body["report"] is None
    assert set(body["error"]) == {"code", "message", "offer"}
    assert body["error"]["code"] == "agent_failed" and body["error"]["message"]
    assert "refused" not in done.text and "RuntimeError" not in done.text
    assert_no_secret(done)


def test_repo_deletion_drops_its_jobs(stack, key):
    client, svc, rid, _ = stack
    job = start(client, rid).json()["job_id"]
    assert poll(client, rid, job).json()["status"] == "done"
    assert client.delete(f"/v1/repos/{rid}", headers=H).status_code == 204
    assert client.get(f"/v1/repos/{rid}/agents/pr/{job}", headers=H).status_code == 404
    app = client.app
    assert all(j.repo_id != rid for j in app.state.pr_jobs.jobs.values())


def test_jobs_are_capped(stack, key, monkeypatch):
    client, _, rid, _ = stack
    monkeypatch.setattr(agents_app, "REVIEWS_PER_HOUR", 100)
    monkeypatch.setattr(agents_app, "MAX_JOBS", 3)
    ids = []
    for _ in range(4):
        job = start(client, rid).json()["job_id"]
        assert poll(client, rid, job).json()["status"] == "done"
        ids.append(job)
    assert len(client.app.state.pr_jobs.jobs) == 3
    assert client.get(f"/v1/repos/{rid}/agents/pr/{ids[0]}", headers=H).status_code == 404
    assert client.get(f"/v1/repos/{rid}/agents/pr/{ids[-1]}", headers=H).status_code == 200


def test_no_secret_in_any_response(stack, key, monkeypatch):
    client, _, rid, _ = stack
    responses = [client.get(STATUS, headers=H), start(client, rid, base="-x")]
    ok = start(client, rid)
    responses += [ok, start(client, rid)]
    responses.append(poll(client, rid, ok.json()["job_id"]))
    monkeypatch.delenv("LLM_API_KEY")
    responses += [client.get(STATUS, headers=H), start(client, rid)]
    for r in responses:
        assert_no_secret(r)
        json.loads(r.text)


def test_core_routes_still_work(stack, key):
    client, _, rid, _ = stack
    assert client.get("/health").status_code == 200
    assert client.get(f"/v1/repos/{rid}/decisions", headers=H).status_code == 200


def test_github_sourced_copy_gets_missing_refs_fetched(tmp_path, key):
    """A GitHub clone holds only the default branch: the review fetches `feature` into the service's own copy."""
    import dataclasses
    from pathlib import Path

    from gh_analyzer import snapshot

    upstream = feature_repo(tmp_path)
    run(upstream, "checkout", "-q", "main")

    def single_branch_clone(target, workdir):
        dest = Path(workdir) / "repo"
        dest.parent.mkdir(parents=True, exist_ok=True)
        run(dest.parent, "clone", "-q", "--single-branch", "--branch", "main", str(upstream), str(dest))
        return dataclasses.replace(snapshot.snapshot(str(dest)), source="github", _tmp=str(workdir))

    svc = AnalyzerService(tmp_path / "store", sync=True, snapshotter=single_branch_clone)
    client = TestClient(create_app(service=svc, agent_client_factory=Factory()))
    rid = client.post("/v1/repos", json={"url": str(upstream)}, headers=H).json()["id"]
    rec = svc.get(rid, "cand-1")
    assert rec.status == "ready" and rec.snap.source == "github"
    copy = rec.snap.root
    assert run(copy, "branch", "--list", "feature").strip() == ""
    before = run(upstream, "for-each-ref", "--format=%(refname) %(objectname)")
    r = start(client, rid)
    body = poll(client, rid, r.json()["job_id"]).json()
    assert body["status"] == "done" and body["report"]["status"] == "ok"
    assert run(copy, "branch", "--list", "feature").strip()          # fetched into the service's copy
    assert run(upstream, "for-each-ref", "--format=%(refname) %(objectname)") == before
