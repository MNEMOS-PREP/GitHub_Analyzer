"""The visualise stage in the pipeline, service, API and CLI: diagrams are added, and can never change or fail an analysis."""

import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gh_analyzer import pipeline, repomap, snapshot, visualise
from gh_analyzer.api import create_app
from gh_analyzer.service import AnalyzerService

H = {"X-Candidate-Id": "cand-1"}
NOTE = "Diagrams were not available for this repository."
FALLBACK = {"graph": {"nodes": [], "edges": [], "quality": "tree_only", "truncated": False},
            "mermaid": {"modules": "flowchart LR", "imports": "flowchart LR", "entry_flow": "flowchart LR"},
            "notes": [NOTE], "repo_map_tokens": 0}


def body(n: int, extra: str = "") -> str:
    return extra + "".join(f"def f{n}_{i}(x):\n    return x + {i}\n\n" for i in range(12))


def commits():
    return [{"files": {"app/main.py": "from app import util\nfrom app import models\n\n" + body(1),
                       "app/util.py": "from app import models\n\n" + body(2),
                       "app/models.py": body(3)}}]


@pytest.fixture
def repo(tmp_repo):
    return tmp_repo(commits())


def run(repo, **kw):
    return pipeline.analyse_repo(str(repo), **kw)


def test_result_has_diagrams(repo):
    r = run(repo)
    d = r["student"]["diagrams"]
    assert d["graph"]["edges"]
    assert 0 < d["repo_map_tokens"] <= 3000
    assert r["private"]["audit"]["context"] == {"tier1_tokens": d["repo_map_tokens"], "cache_hit": False}
    assert "visualise" in r["private"]["audit"]["timings_s"]


def _boom(*a, **k):
    raise RuntimeError("boom")


def test_visualise_failure_does_not_fail_analysis(repo, monkeypatch):
    monkeypatch.setattr(visualise, "diagrams", _boom)
    r = run(repo)
    assert r["student"]["status"] == "ok"
    assert r["student"]["diagrams"] == FALLBACK
    assert NOTE in r["student"]["notes"]
    assert r["private"]["audit"]["context"] == {"tier1_tokens": 0, "cache_hit": False}


def _strip(d: dict, drop: set[str]) -> dict:
    return {k: v for k, v in d.items() if k not in drop}


def test_scoring_outputs_unchanged(repo, monkeypatch):
    normal = run(repo)
    monkeypatch.setattr(visualise, "diagrams", _boom)
    broken = run(repo)
    assert _strip(normal["student"], {"diagrams", "notes"}) == _strip(broken["student"], {"diagrams", "notes"})
    def private(r):
        audit = _strip(r["private"]["audit"], {"context", "timings_s"})
        return {**_strip(r["private"], {"audit"}), "audit": audit}
    assert private(normal) == private(broken)


def test_cache_second_run_hits(repo, tmp_path, monkeypatch):
    calls = []
    real = repomap.build_tier0
    monkeypatch.setattr(repomap, "build_tier0", lambda *a, **k: calls.append(1) or real(*a, **k))
    cache = tmp_path / "cache"
    snap = snapshot.snapshot(str(repo), str(tmp_path / "work"))
    try:
        first = pipeline.analyse_snapshot(snap, cache_dir=cache)
        second = pipeline.analyse_snapshot(snap, cache_dir=cache)
    finally:
        snap.cleanup()
    assert first["private"]["audit"]["context"]["cache_hit"] is False
    assert second["private"]["audit"]["context"]["cache_hit"] is True
    assert len(calls) == 1
    assert second["student"]["diagrams"] == first["student"]["diagrams"]


def test_cache_not_written_into_repo_when_no_cache_dir(repo):
    def files():
        return {p.relative_to(repo).as_posix() for p in repo.rglob("*") if ".git" not in p.relative_to(repo).parts}
    before = files()
    run(repo)
    assert files() == before


def test_endpoint_diagrams(tmp_path, repo):
    svc = AnalyzerService(tmp_path / "store", sync=True)
    client = TestClient(create_app(svc))
    rid = client.post("/v1/repos", json={"url": str(repo)}, headers=H).json()["id"]
    r = client.get(f"/v1/repos/{rid}/diagrams", headers=H)
    assert r.status_code == 200
    assert r.json()["diagrams"]["graph"]["edges"]
    assert (tmp_path / "store" / ".context_cache").is_dir()
    assert client.get(f"/v1/repos/{rid}/diagrams").status_code == 401
    assert client.get(f"/v1/repos/{rid}/diagrams", headers={"X-Candidate-Id": "someone-else"}).status_code == 404


def test_cli_mermaid_flag_writes_files(repo, tmp_path):
    out = tmp_path / "diagrams_out"
    root = Path(__file__).resolve().parent.parent
    p = subprocess.run([sys.executable, "-m", "gh_analyzer", str(repo), "--mermaid", str(out)], cwd=root,
                       capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 0, p.stderr
    for name in ("modules.mmd", "imports.mmd", "entry_flow.mmd"):
        text = (out / name).read_text(encoding="utf-8")
        assert text.startswith("flowchart LR") and text.endswith("\n")
    assert (out / "graph.json").is_file()


def _cache_file(cache, sha):
    return Path(cache) / sha / "context-v1.json"


def _stage_cache(repo, tmp_path, prepare=None):
    cache = tmp_path / "cache"
    snap = snapshot.snapshot(str(repo), str(tmp_path / "work"))
    try:
        if prepare:
            prepare(cache, snap.sha)
        r = pipeline.analyse_snapshot(snap, cache_dir=cache)
    finally:
        snap.cleanup()
    return r, cache, snap.sha


@pytest.mark.parametrize("stale", ['{"diagrams": {"graph": 1}}', '{"old": true}', "not json{", '{"diagrams": {"graph": {}, "mermaid": {}, "notes": []}, "tier1": {"text": 5, "tokens": 1}}'])
def test_stale_cache_entry_is_rebuilt(repo, tmp_path, stale):
    def prepare(cache, sha):
        _cache_file(cache, sha).parent.mkdir(parents=True)
        _cache_file(cache, sha).write_text(stale, encoding="utf-8")
    r, cache, sha = _stage_cache(repo, tmp_path, prepare)
    d = r["student"]["diagrams"]
    assert d["graph"]["edges"] and d != FALLBACK
    assert r["private"]["audit"]["context"]["cache_hit"] is False
    import json
    v = json.loads(_cache_file(cache, sha).read_text(encoding="utf-8"))
    assert set(v["diagrams"]) == {"graph", "mermaid", "notes"} and isinstance(v["tier1"]["text"], str)


def test_cache_kind_is_versioned(repo, tmp_path):
    _, cache, sha = _stage_cache(repo, tmp_path)
    assert _cache_file(cache, sha).is_file() and not (cache / sha / "context.json").exists()


def test_readme_text_skips_non_text_files(tmp_path):
    (tmp_path / "readme_banner.png").write_bytes(b"PNG")
    (tmp_path / "README.md").write_text("# real readme", encoding="utf-8")
    assert pipeline._readme_text(tmp_path) == "# real readme"
    (tmp_path / "README.md").unlink()
    assert pipeline._readme_text(tmp_path) == ""
    (tmp_path / "README").write_text("plain", encoding="utf-8")
    assert pipeline._readme_text(tmp_path) == "plain"


def test_delete_removes_sha_cache_folder(tmp_path, repo):
    svc = AnalyzerService(tmp_path / "store", sync=True)
    rec = svc.submit("cand-1", str(repo))
    sha_dir = tmp_path / "store" / ".context_cache" / rec.pinned_sha
    assert sha_dir.is_dir()
    svc.delete(rec)
    assert not sha_dir.exists()


def test_delete_keeps_sha_cache_while_another_record_pins_it(tmp_path, repo):
    svc = AnalyzerService(tmp_path / "store", sync=True)
    a, b = svc.submit("cand-1", str(repo)), svc.submit("cand-2", str(repo))
    assert a.pinned_sha == b.pinned_sha
    sha_dir = tmp_path / "store" / ".context_cache" / a.pinned_sha
    svc.delete(a)
    assert sha_dir.is_dir()
    svc.delete(b)
    assert not sha_dir.exists()


def test_cli_mermaid_dir_is_a_file(repo, tmp_path):
    blocker = tmp_path / "afile"
    blocker.write_text("x", encoding="utf-8")
    root = Path(__file__).resolve().parent.parent
    p = subprocess.run([sys.executable, "-m", "gh_analyzer", str(repo), "--mermaid", str(blocker)], cwd=root,
                       capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 2
    assert f"Could not write diagrams to {blocker}:" in p.stdout
    assert "Traceback" not in p.stderr
    assert "What I understood" in p.stdout
