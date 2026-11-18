"""Task 2: read-only git access for the PR agent."""

import pytest

from conftest import lines, make_repo, run
from gh_analyzer.agents.gitread import GitReadError, changed_files, hunks, read_git, valid_ref

IDENT = ["-c", "user.name=S", "-c", "user.email=s@e.com"]


def _commit(path, msg):
    run(path, "add", "-A")
    run(path, *IDENT, "commit", "-q", "-m", msg)


def make_branch_commit(path, files):
    for rel, text in files.items():
        f = path / rel
        if text is None:
            f.unlink()
            continue
        f.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            f.write_bytes(text)
        else:
            f.write_text(text, encoding="utf-8")
    _commit(path, "feature work")


def _branch_repo(tmp_repo, base_files, feature_files):
    path = tmp_repo([{"files": base_files, "msg": "base"}])
    run(path, "checkout", "-q", "-b", "feature")
    if feature_files:
        make_branch_commit(path, feature_files)
    return path


@pytest.mark.parametrize("cmd", ["checkout", "commit", "reset", "push", "config", "apply"])
def test_refuses_write_commands(tmp_repo, cmd):
    path = tmp_repo([{"files": {"a.py": "x = 1\n"}}])
    with pytest.raises(GitReadError):
        read_git(path, cmd, "-b", "evil")
    assert run(path, "branch", "--list").strip().lstrip("* ") == "main"


def test_valid_ref_rejects_option_injection():
    bad_refs = ["--output=/tmp/x", "-p", "a b", "", "a\x00b", "a\nb", "x..y", "HEAD@{1}", "a" * 201,
                "main\n", "\nmain", "main\r", "ma\0in", "ma\tin"]
    for bad in bad_refs:
        assert not valid_ref(bad), repr(bad)
    for good in ["main", "feature/x", "HEAD~2", "HEAD^", "a" * 40, "v1.2.3"]:
        assert valid_ref(good), good


DANGEROUS = [
    ("diff", ["--output=/tmp/x"]), ("diff", ["--out=x"]), ("diff", ["--ext-diff"]), ("diff", ["--ext"]),
    ("diff", ["--textconv"]), ("diff", ["--exec-path=/x"]), ("diff", ["--open-files-in-pager"]),
    ("diff", ["-Ofile"]), ("diff", ["-pO/tmp/x"]), ("diff", ["-uO/tmp/x"]), ("diff", ["--no-index"]),
    ("diff", ["-U99999"]), ("show", ["--textc", "HEAD"]), ("show", ["--text", "HEAD"]),
    ("cat-file", ["--filters", "HEAD:a.py"]), ("cat-file", ["--textc", "HEAD:a.py"]),
    ("cat-file", ["--text", "HEAD:a.py"]), ("log", ["--ext-diff"]), ("diff-tree", ["--textc", "HEAD"]),
    ("rev-parse", ["--exec-path"]), ("diff", ["HEAD", "a\0b"]), ("diff", ["HEAD", "--", "a\nb"]),
]


@pytest.mark.parametrize("sub,args", DANGEROUS)
def test_read_git_refuses_dangerous_arguments(tmp_repo, sub, args):
    path = tmp_repo([{"files": {"a.py": "x = 1\n"}}])
    with pytest.raises(GitReadError):
        read_git(path, sub, *args)


def test_read_git_allows_what_it_needs(tmp_repo):
    path = tmp_repo([{"files": {"a.py": "x = 1\n"}}])
    assert read_git(path, "diff", "-U5", "--numstat", "HEAD", "HEAD", "--", ":(literal)a.py") == ""
    assert read_git(path, "cat-file", "-t", "HEAD").strip() == "commit"
    assert read_git(path, "cat-file", "-p", "HEAD:a.py") == "x = 1\n"
    assert read_git(path, "rev-parse", "--verify", "HEAD").strip()


@pytest.mark.parametrize("kind", ["textconv", "smudge"])
def test_refused_abbreviated_options_never_execute_repo_programs(tmp_path, kind):
    root = tmp_path / f"abbr_{kind}"
    marker = tmp_path / f"abbr_marker_{kind}.txt"
    script = tmp_path / f"abbr_script_{kind}.sh"
    script.write_text(f"echo ran > '{marker.as_posix()}'\ncat\n", encoding="utf-8")
    make_repo(root, [{"files": {"a.txt": "one\n"}, "msg": "base"}])
    (root / ".gitattributes").write_text("*.txt diff=d filter=f\n", encoding="utf-8")
    run(root, "config", "diff.d.textconv", f"sh '{script.as_posix()}'")
    run(root, "config", "filter.f.smudge", f"sh '{script.as_posix()}'")
    _commit(root, "attrs")
    bad = ("--textc", "HEAD:a.txt") if kind == "textconv" else ("--filters", "HEAD:a.txt")
    run(root, "cat-file", *bad)          # sanity: plain git does execute the program
    assert marker.exists()
    marker.unlink()
    with pytest.raises(GitReadError):
        read_git(root, "cat-file", *bad)
    assert not marker.exists()


def test_changed_files_statuses(tmp_repo):
    path = _branch_repo(
        tmp_repo,
        {"mod.py": lines(5), "gone.py": lines(3), "old_name.py": lines(20, "r"), "keep.py": "k = 1\n"},
        {},
    )
    (path / "mod.py").write_text(lines(5) + "extra = 1\nextra2 = 2\n", encoding="utf-8")
    (path / "gone.py").unlink()
    (path / "new.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    run(path, "mv", "old_name.py", "new_name.py")
    with open(path / "new_name.py", "a", encoding="utf-8") as f:
        f.write("added_line = 1\n")
    _commit(path, "more")
    rows = {c.path: c for c in changed_files(path, "main", "feature")}
    assert set(rows) == {"mod.py", "gone.py", "new.py", "new_name.py"}
    assert (rows["mod.py"].status, rows["mod.py"].added, rows["mod.py"].removed) == ("modified", 2, 0)
    assert (rows["gone.py"].status, rows["gone.py"].added, rows["gone.py"].removed) == ("deleted", 0, 3)
    assert (rows["new.py"].status, rows["new.py"].added, rows["new.py"].removed) == ("added", 2, 0)
    r = rows["new_name.py"]
    assert (r.status, r.old_path, r.added, r.removed) == ("renamed", "old_name.py", 1, 0)
    assert rows["mod.py"].old_path is None
    assert list(rows) == sorted(rows)


def test_binary_change_is_marked(tmp_repo):
    path = _branch_repo(tmp_repo, {"a.py": "x = 1\n"}, {"blob.bin": b"\x00\x01\x02\x00data"})
    (row,) = changed_files(path, "main", "feature")
    assert row.path == "blob.bin" and row.status == "binary"
    assert row.added is None and row.removed is None


def test_paths_with_spaces_and_unicode(tmp_repo):
    path = _branch_repo(tmp_repo, {"a.py": "x = 1\n"}, {"my dir/café file.py": "y = 2\n"})
    (row,) = changed_files(path, "main", "feature")
    assert row.path == "my dir/café file.py" and row.status == "added"
    (h,) = hunks(path, "main", "feature", "my dir/café file.py")
    assert "+y = 2" in h.text


def test_mode_change_only_is_modified_with_zero_counts(tmp_repo):
    path = _branch_repo(tmp_repo, {"run.sh": "echo hi\n"}, {})
    run(path, "update-index", "--chmod=+x", "run.sh")
    run(path, *IDENT, "commit", "-q", "-m", "chmod")
    (row,) = changed_files(path, "main", "feature")
    assert row.status == "modified" and (row.added, row.removed) == (0, 0)


def test_submodule_is_marked(tmp_repo):
    path = _branch_repo(tmp_repo, {"a.py": "x = 1\n"}, {})
    run(path, "update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},vendor/lib")
    run(path, *IDENT, "commit", "-q", "-m", "submodule")
    (row,) = changed_files(path, "main", "feature")
    assert row.path == "vendor/lib" and row.status == "submodule"


def test_hunks_split(tmp_repo):
    base = lines(60)
    edited = base.replace("x2 = 12\n", "x2 = 999\n").replace("x55 = 65\n", "x55 = 888\n")
    path = _branch_repo(tmp_repo, {"big.py": base}, {"big.py": edited})
    hs = hunks(path, "main", "feature", "big.py")
    assert len(hs) == 2
    assert [h.index for h in hs] == [0, 1]
    assert all(h.header.startswith("@@") and h.text.startswith("@@") for h in hs)
    assert "+x2 = 999" in hs[0].text and "+x55 = 888" in hs[1].text


def test_hunks_of_unchanged_path_is_empty(tmp_repo):
    path = _branch_repo(tmp_repo, {"a.py": "x = 1\n", "b.py": "y = 1\n"}, {"b.py": "y = 2\n"})
    assert hunks(path, "main", "feature", "a.py") == []


def test_hunks_not_forged_by_line_separators_in_content(tmp_repo):
    base = lines(40)
    tricky = "x2 = 12 \x0c@@ -1 +1 @@ a\x85@@ b\x1c@@ c @@ d\r@@ -9 +9 @@ other\n"
    path = _branch_repo(tmp_repo, {"f.py": base}, {})
    (path / "f.py").write_bytes(base.replace("x2 = 12\n", tricky).encode("utf-8"))
    _commit(path, "tricky")
    hs = hunks(path, "main", "HEAD", "f.py")
    assert len(hs) == 1
    assert hs[0].header.startswith("@@ -") and "other" not in hs[0].header and "forged" not in hs[0].header
    assert "\r@@ -9 +9 @@ other" in hs[0].text


def test_invalid_utf8_content_does_not_raise(tmp_repo):
    path = _branch_repo(tmp_repo, {"a.txt": "ok\n"}, {})
    (path / "a.txt").write_bytes(b"ok\nbad \xff\xfe bytes\n")
    _commit(path, "latin")
    (h,) = hunks(path, "main", "HEAD", "a.txt")
    assert "bad" in h.text


def test_hunks_path_with_nul_or_newline_raises(tmp_repo):
    path = _branch_repo(tmp_repo, {"a.py": "x = 1\n"}, {"b.py": "y\n"})
    for bad in ["a\0b", "a\nb"]:
        with pytest.raises(GitReadError):
            hunks(path, "main", "feature", bad)


def test_identical_refs_no_changes(tmp_repo):
    path = tmp_repo([{"files": {"a.py": "x = 1\n"}}])
    assert changed_files(path, "main", "main") == []


def test_unmerged_base_uses_merge_base(tmp_repo):
    path = _branch_repo(tmp_repo, {"a.py": "x = 1\n"}, {"f.py": "f = 1\n"})
    run(path, "checkout", "-q", "main")
    make_branch_commit(path, {"m.py": "m = 1\n"})
    rows = changed_files(path, "main", "feature")
    assert [c.path for c in rows] == ["f.py"]


def test_bad_refs_and_non_repo_raise_plain_errors(tmp_repo, tmp_path):
    path = tmp_repo([{"files": {"a.py": "x = 1\n"}}])
    with pytest.raises(GitReadError):
        changed_files(path, "nope", "main")
    with pytest.raises(GitReadError):
        changed_files(path, "--output=x", "main")
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(GitReadError) as e:
        changed_files(plain, "main", "main")
    assert str(plain) not in str(e.value)
    with pytest.raises(GitReadError) as e:
        changed_files(path, "nope", "main")
    assert str(path) not in str(e.value)


def _driver_repo(tmp_path, kind):
    """A repo whose config would run a script on diff; returns (repo, marker)."""
    root = tmp_path / f"drv_{kind}"
    marker = tmp_path / f"marker_{kind}.txt"
    script = tmp_path / f"script_{kind}.sh"
    script.write_text(f"echo ran > '{marker.as_posix()}'\ncat \"$1\" 2>/dev/null\n", encoding="utf-8")
    make_repo(root, [{"files": {"a.txt": "one\n"}, "msg": "base"}])
    if kind == "external":
        run(root, "config", "diff.external", f"sh '{script.as_posix()}'")
    else:
        (root / ".gitattributes").write_text("*.txt diff=mydriver\n", encoding="utf-8")
        run(root, "config", "diff.mydriver.textconv", f"sh '{script.as_posix()}'")
        _commit(root, "attrs")
    run(root, "checkout", "-q", "-b", "feature")
    make_branch_commit(root, {"a.txt": "one\ntwo\n"})
    return root, marker


def _fsmonitor_repo(tmp_path):
    """A repo whose core.fsmonitor runs a script that leaves a marker and then waits on stdin."""
    root = tmp_path / "fsmon_repo"
    marker = tmp_path / "fsmon_marker.txt"
    script = tmp_path / "fsmon.sh"
    script.write_text(f"#!/bin/sh\necho ran > '{marker.as_posix()}'\ncat\n", encoding="utf-8")
    make_repo(root, [{"files": {"a.py": "x = 1\n"}, "msg": "base"}])
    run(root, "checkout", "-q", "-b", "feature")
    make_branch_commit(root, {"a.py": "x = 2\n"})
    run(root, "config", "core.fsmonitor", script.as_posix())
    (root / "a.py").write_text("x = 3\n", encoding="utf-8")     # a dirty working tree
    return root, marker


def test_repo_fsmonitor_never_runs_and_read_returns_quickly(tmp_path):
    import subprocess
    import time
    root, marker = _fsmonitor_repo(tmp_path)
    subprocess.run(["git", "-C", str(root), "diff", "HEAD"], capture_output=True,
                   stdin=subprocess.DEVNULL, timeout=60)
    assert marker.exists(), "sanity: plain git runs the fsmonitor, so the test is meaningful"
    marker.unlink()
    start = time.monotonic()
    read_git(root, "diff", "HEAD")
    read_git(root, "diff-tree", "-r", "HEAD")
    changed_files(root, "main", "feature")
    assert time.monotonic() - start < 20
    assert not marker.exists()


def test_working_tree_clean_filter_never_runs(tmp_path):
    import subprocess
    root, marker = _fsmonitor_repo(tmp_path)
    run(root, "config", "--unset", "core.fsmonitor")
    run(root, "config", "filter.f.clean", (tmp_path / "fsmon.sh").as_posix())
    (root / ".git" / "info").mkdir(exist_ok=True)
    (root / ".git" / "info" / "attributes").write_text("* filter=f\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "diff", "HEAD"], capture_output=True,
                   stdin=subprocess.DEVNULL, timeout=60)
    assert marker.exists(), "sanity: plain git runs the clean filter on a working-tree diff"
    marker.unlink()
    try:
        read_git(root, "diff", "HEAD")
    except GitReadError:
        pass
    assert not marker.exists()
    assert changed_files(root, "main", "feature")[0].path == "a.py"


def test_read_git_hardens_config_and_closes_stdin(tmp_repo, monkeypatch):
    import subprocess

    from gh_analyzer.agents import gitread
    path = tmp_repo([{"files": {"a.py": "x = 1\n"}}])
    seen = {}
    real = subprocess.run

    def spy(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return real(cmd, **kw)
    monkeypatch.setattr(gitread.subprocess, "run", spy)
    assert read_git(path, "cat-file", "-p", "HEAD:a.py") == "x = 1\n"
    cmd = seen["cmd"]
    sub_at = cmd.index("cat-file")
    pre = cmd[:sub_at]
    for setting in ("core.fsmonitor=false", "protocol.allow=never", "core.pager=cat"):
        assert setting in pre and pre[pre.index(setting) - 1] == "-c"
    assert any(s.startswith("core.hooksPath=") for s in pre)
    assert seen["kw"].get("stdin") is subprocess.DEVNULL


def test_changed_files_does_not_mistake_a_file_for_the_range(tmp_repo):
    path = _branch_repo(tmp_repo, {"a.py": "x = 1\n"}, {"a.py": "x = 2\n"})
    (path / "nope...feature").write_text("hi\n", encoding="utf-8")
    with pytest.raises(GitReadError):
        changed_files(path, "nope", "feature")


@pytest.mark.parametrize("kind", ["external", "textconv"])
def test_repo_configured_diff_drivers_never_run(tmp_path, kind):
    root, marker = _driver_repo(tmp_path, kind)
    run(root, "diff", "main...feature")
    assert marker.exists(), "sanity: the driver fires under plain git, so the test is meaningful"
    marker.unlink()
    changed_files(root, "main", "feature")
    hs = hunks(root, "main", "feature", "a.txt")
    assert hs and "+two" in hs[0].text
    assert not marker.exists()
