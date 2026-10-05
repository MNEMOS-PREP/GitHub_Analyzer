import json
import os

import pytest

from gh_analyzer import inventory, snapshot
from gh_analyzer.snapshot import EmptyRepo, SnapshotError

from conftest import lines, make_repo, run


# ── what the user gave us ─────────────────────────────────────────────────

def test_parse_target_accepts_local_folder_owner_name_and_links(tmp_path):
    assert snapshot.parse_target(str(tmp_path))[0] == "local"
    assert snapshot.parse_target("psf/requests") == ("github", "psf/requests")
    assert snapshot.parse_target("https://github.com/psf/requests.git") == ("github", "psf/requests")
    assert snapshot.parse_target("github.com/psf/requests/") == ("github", "psf/requests")


@pytest.mark.parametrize("bad,word", [
    ("https://gist.github.com/someone/abc123", "gist"),
    ("https://github.com/psf", "organisation"),
    ("not a repo at all", "could not read"),
])
def test_things_that_are_not_a_repository_get_a_named_error(bad, word):
    with pytest.raises(SnapshotError) as e:
        snapshot.parse_target(bad)
    assert word in str(e.value).lower()


def test_a_folder_that_is_not_a_git_repository_is_refused(tmp_path):
    with pytest.raises(SnapshotError, match="not a git repository"):
        snapshot.snapshot(str(tmp_path))


def test_an_empty_repository_is_reported_not_crashed(tmp_path):
    run(tmp_path, "init", "-q")
    with pytest.raises(EmptyRepo, match="no commits"):
        snapshot.snapshot(str(tmp_path))


# ── safety ────────────────────────────────────────────────────────────────

def test_a_link_that_points_outside_the_repository_is_refused(tmp_repo, tmp_path):
    repo = tmp_repo([{"files": {"a.py": "x = 1\n"}}])
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    try:
        os.symlink(outside, repo / "evil_link")
    except (OSError, NotImplementedError):
        pytest.skip("this machine cannot create symlinks")
    with pytest.raises(SnapshotError, match="points outside"):
        snapshot.check_safety(repo)


def test_too_many_files_is_refused(tmp_repo, monkeypatch):
    repo = tmp_repo([{"files": {f"f{i}.txt": "x" for i in range(8)}}])
    monkeypatch.setattr(snapshot, "MAX_FILES", 5)
    with pytest.raises(SnapshotError, match="files"):
        snapshot.check_safety(repo)


def test_excluded_directories_are_not_counted_toward_the_file_cap(tmp_repo, monkeypatch):
    repo = tmp_repo([{"files": {**{f"node_modules/p/f{i}.js": "x" for i in range(30)}, "a.py": "x = 1\n"}}])
    monkeypatch.setattr(snapshot, "MAX_FILES", 5)
    files, _ = snapshot.check_safety(repo)
    assert files == 1


def test_analysing_never_runs_code_in_the_repository(tmp_repo, tmp_path):
    marker = tmp_path / "EXECUTED.txt"
    evil = f"open(r'{marker}', 'w').write('boom')\n"
    repo = tmp_repo([{"files": {"setup.py": evil, "conftest.py": evil, "sitecustomize.py": evil,
                                "pkg/__init__.py": evil, "app.py": evil + "lr = 0.01\n",
                                "package.json": json.dumps({"scripts": {"postinstall": f"echo boom > {marker}"}})}}])
    from gh_analyzer import pipeline
    pipeline.analyse_repo(str(repo), ["student@example.com"])
    assert not marker.exists()


# ── history export ────────────────────────────────────────────────────────

def test_rename_paths_from_numstat_are_normalised():
    assert snapshot._norm_path("src/{old => new}/mod.py") == "src/new/mod.py"
    assert snapshot._norm_path("old.py => new.py") == "new.py"
    assert snapshot._norm_path("{ => sub}/a.py") == "sub/a.py"


def test_export_log_reads_authors_files_and_merges(tmp_repo):
    repo = tmp_repo([
        {"author": ("Asha", "asha@x.com"), "files": {"a.py": lines(5)}, "date": "2026-01-01T09:00:00"},
        {"author": ("Ben", "BEN@x.com"), "files": {"b.py": lines(3)}, "date": "2026-01-02T09:00:00", "msg": "Merge pull request #1"},
    ])
    log = snapshot.export_log(repo)
    assert [c.name for c in log] == ["Ben", "Asha"]
    assert log[0].email == "ben@x.com"                       # lower-cased
    assert log[1].files == [(5, 0, "a.py")]
    assert not any(c.boundary for c in log)


def test_shallow_clone_marks_the_boundary_commit(tmp_repo, tmp_path):
    src = tmp_repo([{"files": {"a.py": lines(10)}, "date": f"2026-01-0{i}T09:00:00"} if i == 1
                    else {"files": {f"f{i}.py": lines(2)}, "date": f"2026-01-0{i}T09:00:00"} for i in range(1, 6)])
    clone = tmp_path / "shallow"
    run(tmp_path, "clone", "-q", "--depth", "3", src.as_uri(), str(clone))
    snap = snapshot.snapshot(str(clone))
    assert snap.shallow and snap.truncated
    log = snapshot.export_log(clone)
    assert len(log) == 3 and sum(c.boundary for c in log) == 1


# ── inventory ─────────────────────────────────────────────────────────────

def test_inventory_excludes_the_world_and_says_why(tmp_repo):
    big = "x = 1\n" * 100_000                               # over 500 KB
    repo = tmp_repo([{"files": {
        "app.py": lines(40),
        "node_modules/lib/index.js": lines(1000),
        "dist/bundle.js": lines(500),
        "static/app.min.js": "var a=1;\n" * 50,
        "api_pb2.py": lines(60),
        "gen.py": "# Code generated by tool. DO NOT EDIT.\n" + lines(70),
        "huge.py": big,
        "package-lock.json": "{}\n",
        "README.md": "# Hi\n",
    }}])
    inv = inventory.scan(repo)
    assert inv.analysed == ["app.py"]
    assert inv.loc_total == 40 and inv.languages == {"Python": 40}
    reasons = " ".join(inv.exclusion_reasons)
    for expected in ("node_modules", "dist", "*.min.js", "_pb2.py", "generated header", "500 KB"):
        assert expected in reasons, expected


def test_binary_files_with_a_code_extension_are_skipped(tmp_path):
    (tmp_path / "x.py").write_bytes(b"abc\x00\x01\x02" * 100)
    inv = inventory.scan(tmp_path)
    assert inv.analysed == [] and "binary" in inv.exclusion_reasons


def test_inventory_finds_tests_ci_docker_readme_licence_and_stack(tmp_repo):
    repo = tmp_repo([{"files": {
        "app/main.py": lines(20), "tests/test_main.py": lines(7),
        ".github/workflows/ci.yml": "on: push\n", "Dockerfile": "FROM python\n",
        "README.md": "# Demo\n\n## Install\n\n```\npip install -r requirements.txt\n```\n" + "word " * 200,
        "LICENSE": "MIT License\n\nCopyright", "requirements.txt": "flask==3.0\nnumpy>=1.2  # math\n-r other.txt\n",
        "package.json": json.dumps({"dependencies": {"react": "^18"}, "devDependencies": {"vite": "^5"}}),
    }}])
    inv = inventory.scan(repo)
    assert inv.has_tests and inv.test_loc == 7 and inv.has_ci and inv.has_dockerfile and inv.has_readme
    assert inv.licence == "MIT" and inv.readme_quality >= 0.9
    assert {"flask", "numpy", "react", "vite"} <= set(inv.dependencies)
    assert {"flask", "numpy", "react", "vite"} <= set(inv.frameworks)


def test_notebook_lines_count_code_cells_only(tmp_path):
    nb = {"cells": [{"cell_type": "code", "source": ["a = 1\n", "b = 2\n", "\n"], "outputs": [{"text": "x" * 5000}]},
                    {"cell_type": "markdown", "source": ["# title\n", "text\n"]}]}
    (tmp_path / "a.ipynb").write_text(json.dumps(nb))
    inv = inventory.scan(tmp_path)
    assert inv.languages == {"Jupyter": 2}


def test_path_rules_are_usable_on_paths_from_git_log():
    assert inventory.path_excluded("web/node_modules/x/y.js")
    assert inventory.path_excluded("a/b/package-lock.json")
    assert inventory.path_excluded("src/model.py") is None
    assert inventory.is_test_path("tests/x.py") and inventory.is_test_path("src/foo.test.js") and not inventory.is_test_path("src/latest.py")
