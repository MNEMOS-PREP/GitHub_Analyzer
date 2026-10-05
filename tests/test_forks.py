"""Fork awareness: a student who added a little on top of a big project is not credited with, or blamed for, the rest."""

import subprocess

from conftest import make_repo, run

from gh_analyzer import contribution, forks, pipeline

OTHER = ("Maintainer", "maint@example.com")
ME = ("Student", "student@example.com")


def big_upstream(tmp_path):
    body = "".join(f"def f{i}():\n    return {i + 100}\n\n" for i in range(150))
    commits = [{"author": OTHER, "date": f"2025-0{m}-10T10:00:00", "msg": f"work {m}", "files": {f"core/m{m}.py": body}}
               for m in range(1, 6)]
    return make_repo(tmp_path / "upstream", commits)


def make_fork(tmp_path, student_commits=3):
    up = big_upstream(tmp_path)
    fork = tmp_path / "fork"
    subprocess.run(["git", "clone", "-q", str(up), str(fork)], check=True, capture_output=True)
    run(fork, "remote", "rename", "origin", "upstream")
    for i in range(student_commits):
        env = {"GIT_AUTHOR_NAME": ME[0], "GIT_AUTHOR_EMAIL": ME[1], "GIT_AUTHOR_DATE": f"2026-02-0{i + 1}T10:00:00+00:00",
               "GIT_COMMITTER_NAME": ME[0], "GIT_COMMITTER_EMAIL": ME[1], "GIT_COMMITTER_DATE": f"2026-02-0{i + 1}T10:00:00+00:00"}
        (fork / "mine").mkdir(exist_ok=True)
        (fork / "mine" / f"feature{i}.py").write_text("".join(f"def g{j}():\n    return {j + 7}\n" for j in range(20)), encoding="utf-8")
        run(fork, "add", "-A", env=env)
        run(fork, "commit", "-q", "-m", f"my change {i}", env=env)
    return fork


def test_parse_github():
    assert forks.parse_github("https://github.com/octo/hello.git") == "octo/hello"
    assert forks.parse_github("git@github.com:octo/hello.git") == "octo/hello"
    assert forks.parse_github("/some/local/path") is None


def test_the_share_is_of_what_was_added_on_top_not_of_the_whole_fork(tmp_path):
    fork = make_fork(tmp_path)
    r = pipeline.analyse_repo(str(fork), me=[ME[1]], fork_of="octo/upstream")["student"]
    assert r["fork"]["is_fork"] and r["fork"]["parent"] == "octo/upstream"
    assert r["fork"]["upstream_commits"] == 5
    assert "fork of octo/upstream" in r["fork"]["statement"] and "3 commits" in r["fork"]["statement"]
    lo, hi = r["contribution"]["range"]
    assert lo >= 0.9, (lo, hi)                      # everything on top of the fork is theirs; upstream is out of the count


def test_without_fork_awareness_the_same_repo_would_show_a_tiny_share(tmp_path):
    fork = make_fork(tmp_path)
    run(fork, "remote", "remove", "upstream")        # no parent known: the whole history is the project
    r = pipeline.analyse_repo(str(fork), me=[ME[1]])["student"]
    assert r["fork"] is None
    assert r["contribution"]["range"][1] < 0.2


def test_a_fork_with_two_commits_on_top_still_gets_an_answer(tmp_path):
    fork = make_fork(tmp_path, student_commits=2)
    r = pipeline.analyse_repo(str(fork), me=[ME[1]], fork_of="octo/upstream")["student"]
    assert r["contribution"]["history_usable"] and r["contribution"]["range"] is not None


def test_a_fork_with_nothing_added_is_reported_not_guessed(tmp_path):
    fork = make_fork(tmp_path, student_commits=0)
    r = pipeline.analyse_repo(str(fork), me=[ME[1]], fork_of="octo/upstream")["student"]
    assert r["contribution"]["range"] is None and "nothing has been added" in r["contribution"]["statement"].lower()


def test_questions_are_never_about_the_upstream_code(tmp_path):
    fork = make_fork(tmp_path)
    r = pipeline.analyse_repo(str(fork), me=[ME[1]], fork_of="octo/upstream")["student"]
    assert all(not d["path"].startswith("core/") for d in r["decisions"])


def test_an_unreachable_parent_falls_back_to_the_whole_repo_with_a_note(tmp_path):
    fork = make_fork(tmp_path)
    run(fork, "remote", "remove", "upstream")
    info = forks.detect(fork, "fork", "local", fork_of="octo/upstream")
    assert info.is_fork and not info.usable and any("could not read its history" in n for n in info.notes)


def test_a_local_repository_is_never_modified_by_fork_detection(tmp_path):
    fork = make_fork(tmp_path)
    before = run(fork, "count-objects", "-v") + run(fork, "for-each-ref")
    forks.detect(fork, "fork", "local", fork_of="octo/upstream")
    assert before == run(fork, "count-objects", "-v") + run(fork, "for-each-ref")


def test_github_api_parent_is_read_and_failures_are_silent(monkeypatch):
    import io
    import json

    monkeypatch.setattr(forks.urllib.request, "urlopen",
                        lambda *a, **k: io.StringIO(json.dumps({"fork": True, "parent": {"full_name": "up/stream"}})))
    assert forks._api_parent("me/repo") == "up/stream"
    monkeypatch.setattr(forks.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    assert forks._api_parent("me/repo") is None
