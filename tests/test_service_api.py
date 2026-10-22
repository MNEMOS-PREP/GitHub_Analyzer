"""The service and HTTP layer end to end: intake -> snapshot -> analysis -> confirmation -> gate, over the real routes."""

import json

import pytest
from fastapi.testclient import TestClient

from gh_analyzer import guard, intake, snapshot
from gh_analyzer.api import create_app
from gh_analyzer.service import AnalyzerService
from test_pipeline_safety import A, B, build, KEY

H = {"X-Candidate-Id": "cand-1"}


@pytest.fixture
def stack(tmp_path, tmp_repo):
    repo = build(tmp_repo)
    svc = AnalyzerService(tmp_path / "store", sync=True)
    return TestClient(create_app(svc)), svc, repo


def submit(client, repo, **extra):
    r = client.post("/v1/repos", json={"url": str(repo), **extra}, headers=H)
    assert r.status_code == 202, r.text
    return r.json()["id"]


def test_submit_returns_202_and_the_read_is_a_student_safe_payload(stack):
    client, svc, repo = stack
    rid = submit(client, repo)
    r = client.get(f"/v1/repos/{rid}", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready" and len(body["pinned_sha"]) == 12
    assert body["inventory"]["analysed_files"] >= 3 and "private" not in body
    assert guard.problems(body) == []


def test_every_route_needs_a_candidate_and_a_repo_belongs_to_its_owner(stack):
    client, svc, repo = stack
    rid = submit(client, repo)
    assert client.get(f"/v1/repos/{rid}").status_code == 401
    assert client.get(f"/v1/repos/{rid}", headers={"X-Candidate-Id": "someone-else"}).status_code == 404
    assert client.get(f"/v1/repos/{rid}/security-notices", headers={"X-Candidate-Id": "someone-else"}).status_code == 404
    assert client.delete(f"/v1/repos/{rid}", headers={"X-Candidate-Id": "someone-else"}).status_code == 404


def test_identities_must_be_confirmed_before_a_share_exists_FR_3_6(stack):
    client, svc, repo = stack
    rid = submit(client, repo)
    before = client.get(f"/v1/repos/{rid}", headers=H).json()["contribution"]
    assert before["range"] is None and before["identity_candidates"]
    opts = client.get(f"/v1/repos/{rid}/identities", headers=H).json()
    assert {c["email"] for c in opts["candidates"]} == {"asha@example.com", "ben@example.com"}
    bad = client.post(f"/v1/repos/{rid}/identities/confirm", json={"emails": ["nobody@nowhere.dev"]}, headers=H)
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "identity"
    empty = client.post(f"/v1/repos/{rid}/identities/confirm", json={"emails": []}, headers=H)
    assert empty.status_code == 400
    ok = client.post(f"/v1/repos/{rid}/identities/confirm", json={"emails": ["ASHA@example.com"]}, headers=H)
    assert ok.status_code == 200
    lo, hi = ok.json()["contribution"]["range"]
    assert 0 < lo < hi <= 1
    assert ok.json()["identities_confirmed"] == ["asha@example.com"]


def test_identity_suggestions_come_from_hints_but_nothing_is_chosen_for_the_student(stack):
    client, svc, repo = stack
    rid = submit(client, repo, hints=["Asha"])
    opts = client.get(f"/v1/repos/{rid}/identities", headers=H).json()
    assert opts["suggested"] == ["asha@example.com"] and opts["confirmed"] == []


def test_confirming_re_reads_the_pinned_snapshot_instead_of_analysing_from_scratch(stack):
    client, svc, repo = stack
    rid = submit(client, repo)
    sha = client.get(f"/v1/repos/{rid}", headers=H).json()["pinned_sha"]
    client.post(f"/v1/repos/{rid}/identities/confirm", json={"emails": ["asha@example.com"]}, headers=H)
    assert client.get(f"/v1/repos/{rid}", headers=H).json()["pinned_sha"] == sha        # EC-3.8


def test_the_same_commit_is_analysed_once_per_candidate(tmp_path, tmp_repo):
    repo = build(tmp_repo)
    svc = AnalyzerService(tmp_path / "store", sync=True)
    sha = snapshot.git(repo, "rev-parse", "HEAD").strip()

    class Fixed(intake.GitHubClient):
        def check_repo(self, owner, name): return {"default_branch": "main"}
        def head_sha(self, owner, name, branch=None): return sha

    fake = Fixed(transport=lambda *a, **k: (500, {}, None))
    svc._snapshotter = lambda target, workdir: snapshot.snapshot(str(repo))      # stands in for the clone
    first = svc.submit("c", "owner/proj", client=fake)
    second = svc.submit("c", "owner/proj", client=fake)
    other = svc.submit("d", "owner/proj", client=fake)
    assert first.id == second.id and second.cached and svc.analyses_run == 2        # one for c, one for d
    assert other.id != first.id


def test_questions_decisions_and_depth_filter(stack):
    client, svc, repo = stack
    rid = submit(client, repo)
    d = client.get(f"/v1/repos/{rid}/decisions", headers=H).json()["decisions"]
    assert len(d) >= 5 and all(x["path"] and x["line_start"] >= 1 for x in d)
    q = client.get(f"/v1/repos/{rid}/questions?depth=2", headers=H).json()["questions"]
    assert q and {x["depth"] for x in q} == {2}
    assert client.get(f"/v1/repos/{rid}/questions?depth=9", headers=H).status_code == 422


def test_understanding_confirmation_records_acceptance_for_AC_3_7(stack):
    client, svc, repo = stack
    rid = submit(client, repo)
    assert client.post(f"/v1/repos/{rid}/understanding/confirm", json={}, headers=H).json()["accepted_without_correction"] is True
    r = client.post(f"/v1/repos/{rid}/understanding/confirm", json={"corrected_summary": "It is a Flask app that trains a model."}, headers=H)
    assert r.json()["accepted_without_correction"] is False and "Flask" in r.json()["correction"]
    assert svc.metrics()["understanding_confirmed"] == 1


def test_explain_gate_needs_identity_then_picks_a_function_the_student_wrote_FR_3_12(stack):
    client, svc, repo = stack
    rid = submit(client, repo)
    first = client.post(f"/v1/repos/{rid}/explain-gate", headers=H).json()
    assert first["available"] is False and "Confirm which commits" in first["reason"]
    client.post(f"/v1/repos/{rid}/identities/confirm", json={"emails": ["asha@example.com"]}, headers=H)
    g = client.post(f"/v1/repos/{rid}/explain-gate", headers=H).json()
    assert g["available"] and g["path"] == "train.py" and g["function"] == "run"
    assert g["seconds"] == 90 and g["scored"] is False and g["coaching_only"] is True
    assert "speak" in g["input_modes"] and "type" in g["input_modes"]          # typing is offered (accessibility)
    assert "train_test_split" in g["code"] and g["citation"] == "train.py:6"
    assert client.post(f"/v1/repos/{rid}/explain-gate/rating", json={"session_id": g["session_id"], "useful": True}, headers=H).status_code == 200
    assert svc.metrics()["gate_useful"] == 1


def test_explain_gate_does_not_pick_code_someone_else_wrote(tmp_path, tmp_repo):
    repo = build(tmp_repo)
    svc = AnalyzerService(tmp_path / "store", sync=True)
    client = TestClient(create_app(svc))
    rid = submit(client, repo)
    client.post(f"/v1/repos/{rid}/identities/confirm", json={"emails": ["ben@example.com"]}, headers=H)
    g = client.post(f"/v1/repos/{rid}/explain-gate", headers=H).json()
    assert g["available"] is False and "skip this step" in g["reason"]


def test_secrets_reach_only_the_owner_via_security_notices_never_the_read(stack):
    client, svc, repo = stack
    rid = submit(client, repo)
    everything = json.dumps([client.get(f"/v1/repos/{rid}", headers=H).json(),
                             client.get(f"/v1/repos/{rid}/decisions", headers=H).json(),
                             client.get(f"/v1/repos/{rid}/questions", headers=H).json()])
    assert KEY not in everything and "sk-proj" not in everything
    notes = client.get(f"/v1/repos/{rid}/security-notices", headers=H).json()["notices"]
    assert notes and KEY not in json.dumps(notes)                              # a notice, not the value


def test_delete_removes_the_record_and_the_stored_snapshot(tmp_path, tmp_repo):
    repo = build(tmp_repo)
    svc = AnalyzerService(tmp_path / "store", sync=True)
    client = TestClient(create_app(svc))
    rid = submit(client, repo)
    assert client.delete(f"/v1/repos/{rid}", headers=H).status_code == 204
    assert client.get(f"/v1/repos/{rid}", headers=H).status_code == 404
    assert repo.exists()                                                       # a local folder is never deleted


def test_a_gist_a_user_page_and_junk_get_named_errors_EC_3_14(stack):
    client, svc, repo = stack
    for url, word in [("https://gist.github.com/x/abc", "gist"), ("https://github.com/someone", "user or organisation"), ("???", "could not read")]:
        r = client.post("/v1/repos", json={"url": url}, headers=H)
        assert r.status_code == 400 and word in r.json()["error"]["message"]
        assert "continue_without_repo" in r.json()["error"]["offer"]
    assert client.post("/v1/repos", json={}, headers=H).status_code == 422


def test_an_empty_repository_ends_in_the_no_repo_path_with_a_project_round_EC_3_1(tmp_path, tmp_repo):
    empty = tmp_path / "empty"
    empty.mkdir()
    snapshot.git(empty, "init", "-q", "-b", "main", check=False)
    svc = AnalyzerService(tmp_path / "store", sync=True)
    client = TestClient(create_app(svc))
    rid = submit(client, empty)
    body = client.get(f"/v1/repos/{rid}", headers=H).json()
    assert body["status"] == "empty" and body["no_repo_path"]["affects_score"] is False
    assert len(body["project_round"]["questions"]) >= 4


def test_when_everything_fails_the_student_gets_the_no_repo_path_not_an_error_FR_3_18(tmp_path):
    def boom(target, workdir):
        raise RuntimeError("disk on fire")

    svc = AnalyzerService(tmp_path / "store", sync=True, snapshotter=boom)
    client = TestClient(create_app(svc))
    rid = submit(client, "owner/name")
    body = client.get(f"/v1/repos/{rid}", headers=H).json()
    assert body["status"] == "failed" and "project round" in body["message"]
    assert body["project_round"]["questions"] and "disk on fire" not in json.dumps(body)       # no internals


def test_progress_is_streamed_as_server_sent_events(stack):
    client, svc, repo = stack
    rid = submit(client, repo)
    text = client.get(f"/v1/repos/{rid}/stream", headers=H).text
    stages = [d["stage"] for d in (json.loads(l[6:]) for l in text.splitlines() if l.startswith("data: ")) if "stage" in d]
    assert stages[0] == "started" and "contribution" in stages and stages[-1] == "ready"
    assert "event: done" in text


def test_the_api_fails_closed_if_a_payload_would_carry_a_forbidden_key():
    with pytest.raises(guard.LeakError):
        guard.assert_clean({"student": {"similarity": 0.9}})
    with pytest.raises(guard.LeakError):
        guard.assert_clean({"questions": [{"question": "Is this original work?"}]})
    guard.assert_clean({"decisions": [{"snippet": "original = load()"}]})          # the student's own code is not policed for words
