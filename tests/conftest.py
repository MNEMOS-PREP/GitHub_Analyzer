"""Test helpers: build small real git repositories with chosen authors, dates and files."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def run(cwd, *args, env=None):
    e = dict(os.environ)
    e.update(env or {})
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=e)
    assert r.returncode == 0, r.stderr
    return r.stdout


def make_repo(path: Path, commits: list[dict]) -> Path:
    """commits: dicts with author=(name, email), date='2026-01-05T10:00:00', files={rel: text|None}, msg=str."""
    path.mkdir(parents=True, exist_ok=True)
    run(path, "init", "-q", "-b", "main")
    for c in commits:
        for rel, text in c.get("files", {}).items():
            f = path / rel
            if text is None:
                f.unlink()
                continue
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text, encoding="utf-8")
        name, email = c.get("author", ("Student", "student@example.com"))
        date = c.get("date", "2026-01-05T10:00:00") + "+00:00"
        env = {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_AUTHOR_DATE": date,
               "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email, "GIT_COMMITTER_DATE": date}
        run(path, "add", "-A", env=env)
        run(path, "commit", "-q", "--allow-empty", "-m", c.get("msg", "add feature"), env=env)
    return path


def lines(n: int, prefix: str = "x") -> str:
    return "".join(f"{prefix}{i} = {i + 10}\n" for i in range(n))


@pytest.fixture
def tmp_repo(tmp_path):
    def _make(commits, name="repo"):
        return make_repo(tmp_path / name, commits)
    return _make
