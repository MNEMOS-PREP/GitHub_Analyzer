"""The PR agent CLI, and the isolation rules: the core never imports the agents, the agents never import repo-agent."""

from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from agent_fakes import FakeClient, tool_call
from conftest import make_repo, run
from gh_analyzer.agents.__main__ import main

PROJECT = Path(__file__).resolve().parent.parent
AGENTS = PROJECT / "gh_analyzer" / "agents"

IDENT = {"GIT_AUTHOR_NAME": "Student", "GIT_AUTHOR_EMAIL": "student@example.com",
         "GIT_COMMITTER_NAME": "Student", "GIT_COMMITTER_EMAIL": "student@example.com"}
BASE = {"app.py": "def add(a, b):\n    return a + b\n", "README.md": "# Calc\nA small calculator.\n"}
HEAD = {"app.py": "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"}
TITLE = "Add a sub function to the calculator"
SUPPORTED = {"role": "assistant", "content": '{"supported": true, "reason": "the hunk shows it"}'}


def feature_repo(tmp_path: Path) -> Path:
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


def args(root: Path, *extra: str) -> list[str]:
    return ["pr", str(root), "--base", "main", "--head", "feature", *extra]


def test_core_never_imports_agents():
    code = ("import gh_analyzer.pipeline, gh_analyzer.service, gh_analyzer.api; import sys; "
            "assert not [m for m in sys.modules if m.startswith('gh_analyzer.agents')]")
    r = subprocess.run([sys.executable, "-c", code], cwd=PROJECT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


GLUE = "agents_app.py"
_AGENT_IMPORT = re.compile(r"^\s*(?:from\s+(?:gh_analyzer)?\.*agents\b|import\s+gh_analyzer\.agents\b"
                           r"|from\s+gh_analyzer\s+import\s+[^\n]*\bagents\b)", re.MULTILINE)
_GLUE_IMPORT = re.compile(r"\bagents_app\b")


def core_sources() -> list[Path]:
    """Every gh_analyzer module except the agents package and the single glue module."""
    pkg = PROJECT / "gh_analyzer"
    return [p for p in sorted(pkg.rglob("*.py")) if AGENTS not in p.parents and p.name != GLUE]


def test_core_sources_never_import_agents():
    sources = core_sources()
    assert {p.name for p in sources} >= {"api.py", "service.py", "pipeline.py"}
    for p in sources:
        assert not _AGENT_IMPORT.search(p.read_text(encoding="utf-8")), p.name


def test_agents_app_is_the_single_glue_module():
    pkg = PROJECT / "gh_analyzer"
    glue = [p.name for p in sorted(pkg.glob("*.py")) if _AGENT_IMPORT.search(p.read_text(encoding="utf-8"))]
    assert glue == [GLUE]


def test_core_does_not_import_agents_app():
    for name in ("api.py", "service.py", "pipeline.py"):
        assert not _GLUE_IMPORT.search((PROJECT / "gh_analyzer" / name).read_text(encoding="utf-8")), name
    code = ("import gh_analyzer.pipeline, gh_analyzer.service, gh_analyzer.api; import sys; "
            "assert 'gh_analyzer.agents_app' not in sys.modules")
    r = subprocess.run([sys.executable, "-c", code], cwd=PROJECT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_every_core_module_imports_without_agents():
    mods = [".".join(p.relative_to(PROJECT).with_suffix("").parts) for p in core_sources()
            if p.name != "__main__.py"]
    code = (f"import importlib, sys; [importlib.import_module(m) for m in {mods!r}]; "
            "assert not [m for m in sys.modules if m.startswith('gh_analyzer.agents')]")
    r = subprocess.run([sys.executable, "-c", code], cwd=PROJECT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_agents_do_not_import_repo_agent():
    sources = sorted(AGENTS.glob("*.py"))
    assert sources
    for p in sources:
        text = p.read_text(encoding="utf-8").lower()
        assert "ghagent" not in text, p.name
        assert "repo_agent" not in text and "repo-agent" not in text, p.name


def test_cli_runs_with_fake_client(tmp_path, capsys):
    root = feature_repo(tmp_path)
    client = FakeClient(script())
    assert main(args(root), client=client) == 0
    out = capsys.readouterr().out
    assert TITLE in out
    assert "Add a unit test for sub" in out
    assert "A test shows the new function works." in out
    assert "E2" in out
    assert "app.py" in out
    assert "Tokens used" in out
    assert len(client.calls) == 3


def test_cli_no_verify_skips_verifier(tmp_path, capsys):
    root = feature_repo(tmp_path)
    client = FakeClient(script()[:2])
    assert main(args(root, "--no-verify"), client=client) == 0
    assert TITLE in capsys.readouterr().out
    assert len(client.calls) == 2


def test_cli_no_changes_exits_0_without_model(tmp_path, capsys):
    root = feature_repo(tmp_path)
    client = FakeClient([])
    assert main(["pr", str(root), "--base", "feature", "--head", "feature"], client=client) == 0
    assert "no changes" in capsys.readouterr().out.lower()
    assert client.calls == []


def test_cli_incomplete_exits_1(tmp_path, capsys):
    root = feature_repo(tmp_path)
    never = {"role": "assistant", "content": "thinking..."}
    client = FakeClient([never] * 20)
    assert main(args(root, "--no-verify"), client=client) == 1
    assert "could not finish" in capsys.readouterr().out.lower()


def test_cli_missing_key_exits_2(tmp_path, capsys, monkeypatch):
    root = feature_repo(tmp_path)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert main(args(root)) == 2
    captured = capsys.readouterr()
    lines = [ln for ln in (captured.out + captured.err).splitlines() if ln.strip()]
    assert len(lines) == 1
    assert "LLM_API_KEY" in lines[0]


def test_cli_unreadable_repo_exits_2(tmp_path, capsys):
    client = FakeClient([])
    missing = tmp_path / "not-a-repo"
    missing.mkdir()
    assert main(["pr", str(missing), "--base", "main", "--head", "feature"], client=client) == 2
    captured = capsys.readouterr()
    lines = [ln for ln in (captured.out + captured.err).splitlines() if ln.strip()]
    assert len(lines) == 1
    assert client.calls == []


def test_cli_json_output_has_no_evidence_bodies(tmp_path, capsys):
    root = feature_repo(tmp_path)
    out = tmp_path / "report.json"
    assert main(args(root, "--json", str(out)), client=FakeClient(script())) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["suggested_title"] == TITLE
    assert set(report["evidence"]) == {"E1", "E2"}
    for entry in report["evidence"].values():
        assert set(entry) == {"tool", "args"}
    assert report["evidence"]["E2"] == {"tool": "read_hunk", "args": {"path": "app.py", "index": 0}}
    text = out.read_text(encoding="utf-8")
    assert "return a - b" not in text          # the hunk body (E2's result) is never stored
    assert "@@" not in text


def test_cli_reports_tier1_tokens_alongside_run_tokens(tmp_path, capsys):
    root = feature_repo(tmp_path)
    out = tmp_path / "report.json"
    assert main(args(root, "--json", str(out)), client=FakeClient(script())) == 0
    tokens = json.loads(out.read_text(encoding="utf-8"))["tokens"]
    assert {"prompt", "completion", "total", "tier1"} <= set(tokens)
    assert isinstance(tokens["tier1"], int) and tokens["tier1"] > 0
    assert tokens["total"] == tokens["prompt"] + tokens["completion"]     # run tokens are unchanged
    printed = capsys.readouterr().out
    assert "Tokens used" in printed and f"project overview {tokens['tier1']}" in printed


@pytest.mark.parametrize("where", ["missing_parent", "is_a_directory"])
def test_cli_unwritable_json_still_prints_report_and_exits_2(tmp_path, capsys, where):
    root = feature_repo(tmp_path)
    target = tmp_path / "no-such-dir" / "r.json" if where == "missing_parent" else tmp_path
    assert main(args(root, "--json", str(target)), client=FakeClient(script())) == 2
    captured = capsys.readouterr()
    assert TITLE in captured.out and "Tokens used" in captured.out
    errors = [ln for ln in captured.err.splitlines() if ln.startswith("Could not write")]
    assert len(errors) == 1 and errors[0].startswith(f"Could not write {target}: ")
    assert "Traceback" not in captured.err


def test_cli_error_lines_survive_a_cp1252_stream(tmp_path, monkeypatch):
    out_buf, err_buf = io.BytesIO(), io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(out_buf, encoding="cp1252"))
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(err_buf, encoding="cp1252"))
    folder = tmp_path / "proj_日本"
    folder.mkdir()
    code = main(["pr", str(folder), "--base", "main", "--head", "feature"], client=FakeClient([]))
    sys.stdout.flush()
    sys.stderr.flush()
    assert code == 2
    assert b"Could not read that repository" in out_buf.getvalue()


def _state(root: Path) -> tuple[dict, str]:
    files = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file():
            files[rel] = "" if rel.startswith(".git/") else hashlib.sha256(p.read_bytes()).hexdigest()
    status = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True).stdout
    return files, status


def test_cli_never_modifies_the_repo(tmp_path, capsys):
    root = feature_repo(tmp_path)
    (root / "notes.txt").write_text("uncommitted work\n", encoding="utf-8")   # the student's own pending change
    subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True)  # settle the index first
    before = _state(root)
    assert main(args(root, "--json", str(tmp_path / "r.json")), client=FakeClient(script())) == 0
    assert _state(root) == before
    assert run(root, "rev-parse", "--abbrev-ref", "HEAD").strip() == "feature"


def test_render_shows_notes():
    from gh_analyzer.agents.__main__ import render
    note = "Some items could not be double-checked within the token budget."
    out = render({"status": "ok", "summary": "s", "verified": True, "notes": [note], "tokens": {}})
    assert f"Note: {note}" in out
    assert "Note:" not in render({"status": "ok", "summary": "s", "verified": True, "notes": [], "tokens": {}})
