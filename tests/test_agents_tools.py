"""Evidence log and the budgeted read-only tools of the PR agent."""

import pytest

from conftest import make_repo, run
from gh_analyzer.agents import gitread
from gh_analyzer.agents.evidence import EvidenceLog
from gh_analyzer.agents.pr_tools import MAX_LIST, MAX_READS, READ_CHARS, SCHEMAS, PRContext, ToolBox
from gh_analyzer.agents.safe import WITHHELD
from gh_analyzer.repomap import Tier0

KEY = "sk-" + "Kj3nX9pQ2vLm" + "7RtB5wYc8HdF1sGa6ZeU"


def t0(files=(), edges=(), quality="full"):
    return Tier0(files=list(files), edges=set(edges), in_degree={}, symbols={}, entry_points=[],
                 languages={}, graph_quality=quality)


def build(tmp_path, base_files, head_files, name="repo", tier=None):
    """base commit on main, feature branch with head_files (None deletes a file)."""
    root = make_repo(tmp_path / name, [{"files": base_files, "msg": "base"}])
    run(root, "checkout", "-q", "-b", "feature")
    make_repo_commit(root, head_files)
    ctx = PRContext(root, "main", "feature", tier or t0(list(base_files)))
    return root, ToolBox(ctx, EvidenceLog())


def make_repo_commit(root, files):
    for rel, text in files.items():
        f = root / rel
        if text is None:
            f.unlink()
        elif isinstance(text, bytes):
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(text)
        else:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text, encoding="utf-8", newline="\n")
    run(root, "add", "-A")
    run(root, "commit", "-q", "-m", "feature work")


# --- evidence log ---------------------------------------------------------------------

def test_evidence_log_ids_get_lookup_and_copies():
    log = EvidenceLog()
    assert len(log) == 0 and log.get("E1") is None
    a = log.add("read_hunk", {"path": "a.py", "index": 0}, "one")
    b = log.add("callers_of", {"path": "a.py"}, "two")
    assert (a, b) == ("E1", "E2") and len(log) == 2
    assert log.get("E1") == {"tool": "read_hunk", "args": {"path": "a.py", "index": 0}, "result": "one"}
    assert log.lookup_call("read_hunk", {"index": 0, "path": "a.py"}) == "E1"   # key order does not matter
    assert log.lookup_call("read_hunk", {"path": "a.py", "index": 1}) is None
    log.get("E1")["result"] = "tampered"
    log.items()["E2"]["args"]["path"] = "x"
    assert log.get("E1")["result"] == "one" and log.get("E2")["args"] == {"path": "a.py"}


# --- list_changed_files ---------------------------------------------------------------

def test_list_changed_files_truncates(tmp_path):
    files = {f"src/f{i:03d}.py": f"v = {i}\n" for i in range(200)}
    _, box = build(tmp_path, {"README.md": "hi\n"}, files)
    eid, text = box.call("list_changed_files", {})
    assert eid == "E1"
    assert "200" in text.splitlines()[0]
    assert sum(1 for ln in text.splitlines() if ln.startswith("added src/")) == MAX_LIST
    assert "... and 140 more" in text
    assert box.reads == 0


def test_list_changed_files_rows_for_each_status(tmp_path):
    base = {"keep.py": "a\nb\n", "gone.py": "x\n", "old_name.py": "".join(f"line {i}\n" for i in range(30)),
            "pic.bin": b"\x00\x01\x02".decode("latin-1")}
    root = make_repo(tmp_path / "r", [{"files": {k: v for k, v in base.items() if k != "pic.bin"}}])
    (root / "pic.bin").write_bytes(b"\x00\x01\x02")
    run(root, "add", "-A")
    run(root, "commit", "-q", "-m", "bin")
    run(root, "checkout", "-q", "-b", "feature")
    (root / "keep.py").write_text("a\nb\nc\n", encoding="utf-8", newline="\n")
    (root / "gone.py").unlink()
    (root / "pic.bin").write_bytes(b"\x00\x09\x09\x09")
    run(root, "mv", "old_name.py", "new_name.py")
    run(root, "add", "-A")
    run(root, "commit", "-q", "-m", "work")
    box = ToolBox(PRContext(root, "main", "feature", t0()), EvidenceLog())
    _, text = box.call("list_changed_files", {})
    assert "modified keep.py (+1/-0)" in text
    assert "deleted gone.py (+0/-1)" in text
    assert "renamed old_name.py -> new_name.py" in text
    assert any(ln.startswith("binary pic.bin") and "(+" not in ln for ln in text.splitlines())


def test_list_changed_files_no_changes(tmp_path):
    root = make_repo(tmp_path / "r", [{"files": {"a.py": "x\n"}}])
    run(root, "branch", "feature")
    box = ToolBox(PRContext(root, "main", "feature", t0()), EvidenceLog())
    eid, text = box.call("list_changed_files", {})
    assert eid == "E1" and "0" in text.splitlines()[0]


# --- read_hunk ------------------------------------------------------------------------

def test_read_hunk_capped_and_redacted(tmp_path):
    body = f'API_KEY = "{KEY}"\n' + "".join(f"value_{i} = {i}\n" for i in range(6000))
    assert len(body) > 50_000
    _, box = build(tmp_path, {"app.py": "x = 1\n"}, {"app.py": "x = 1\n" + body})
    eid, text = box.call("read_hunk", {"path": "app.py", "index": 0})
    assert eid == "E1"
    assert len(text) <= READ_CHARS
    assert KEY not in text and "Kj3nX9pQ2vLm" not in text
    assert "@@" in text
    assert KEY not in box.log.get(eid)["result"]


def test_read_hunk_index_zero_based_and_out_of_range(tmp_path):
    base = "".join(f"line {i}\n" for i in range(100))
    head = base.replace("line 2\n", "line two\n").replace("line 90\n", "line ninety\n")
    _, box = build(tmp_path, {"m.py": base}, {"m.py": head})
    _, first = box.call("read_hunk", {"path": "m.py", "index": 0})
    _, second = box.call("read_hunk", {"path": "m.py", "index": "1"})
    assert "line two" in first and "line ninety" in second
    eid, msg = box.call("read_hunk", {"path": "m.py", "index": 2})
    assert "2 hunk" in msg
    assert box.call("read_hunk", {"path": "m.py", "index": -1})[1].count("2 hunk") == 1


def test_read_hunk_float_and_bad_ints(tmp_path):
    _, box = build(tmp_path, {"m.py": "a\n"}, {"m.py": "a\nb\n"})
    assert box.call("read_hunk", {"path": "m.py", "index": 0.0})[0] == "E1"
    for bad in ("x", 1.5, None, [1], True, {"a": 1}, "1e3", float("nan")):
        eid, msg = box.call("read_hunk", {"path": "m.py", "index": bad})
        assert eid == "" and msg
    assert box.reads == 1


def test_read_hunk_binary_and_unchanged(tmp_path):
    root = make_repo(tmp_path / "r", [{"files": {"a.py": "x\n"}}])
    (root / "pic.bin").write_bytes(b"\x00\x01")
    run(root, "add", "-A")
    run(root, "commit", "-q", "-m", "bin")
    run(root, "checkout", "-q", "-b", "feature")
    (root / "pic.bin").write_bytes(b"\x00\x02\x03")
    run(root, "add", "-A")
    run(root, "commit", "-q", "-m", "bin2")
    box = ToolBox(PRContext(root, "main", "feature", t0()), EvidenceLog())
    _, msg = box.call("read_hunk", {"path": "pic.bin", "index": 0})
    assert "binary" in msg and "\x00" not in msg
    _, msg = box.call("read_hunk", {"path": "a.py", "index": 0})
    assert "not" in msg and "change" in msg


# --- read_file_range ------------------------------------------------------------------

def test_read_file_range_reads_head_version_via_git(tmp_path):
    root, box = build(tmp_path, {"a.py": "one\ntwo\nthree\n"}, {"a.py": "one\nTWO\nthree\n"})
    (root / "a.py").write_text("WORKING TREE EDIT\n", encoding="utf-8")   # uncommitted: must not be read
    eid, text = box.call("read_file_range", {"path": "a.py", "start": 1, "end": 3})
    assert eid == "E1"
    assert "  1: one" in text
    assert ": TWO" in text and "WORKING TREE" not in text
    # an unchanged file in the head tree can be read too
    root2, box2 = build(tmp_path, {"ctx.py": "keep\n", "b.py": "b\n"}, {"b.py": "c\n"}, name="r2")
    assert ": keep" in box2.call("read_file_range", {"path": "ctx.py", "start": 1, "end": 1})[1]


@pytest.mark.parametrize("bad", ["../secret.txt", "a/../../x", "/etc/passwd", "C:/x.py", "a\\b.py", "",
                                 "a\0b", "a\nb", "-rf", "--exec=x", "a/..", None, 5])
def test_read_file_range_refuses_bad_paths(tmp_path, bad):
    _, box = build(tmp_path, {"a.py": "x\n"}, {"a.py": "y\n"})
    eid, msg = box.call("read_file_range", {"path": bad, "start": 1, "end": 2})
    assert eid == "" and "not allowed" in msg
    assert box.reads == 0 and len(box.log) == 0


def test_read_file_range_clamps_and_caps(tmp_path):
    text = "".join(f"l{i}\n" for i in range(1, 501))
    _, box = build(tmp_path, {"big.py": "x\n"}, {"big.py": text})
    _, out = box.call("read_file_range", {"path": "big.py", "start": 1, "end": 500})
    shown = [ln for ln in out.splitlines() if ": l" in ln]
    assert len(shown) <= 200 and ": l200" in out and ": l201" not in out
    _, out = box.call("read_file_range", {"path": "big.py", "start": -5, "end": "3"})
    assert ": l1" in out and ": l3" in out and ": l4" not in out
    _, out = box.call("read_file_range", {"path": "big.py", "start": 498, "end": 9999})
    assert ": l500" in out
    _, out = box.call("read_file_range", {"path": "big.py", "start": 900, "end": 950})
    assert "500" in out and ": l" not in out
    _, out = box.call("read_file_range", {"path": "big.py", "start": 10, "end": 5})
    assert "end" in out and ": l" not in out


def test_read_file_range_edge_files(tmp_path):
    _, box = build(tmp_path, {"a.py": "x\n"},
                   {"empty.py": "", "no_nl.py": "first\nlast", "naïve file.py": "héllo\n",
                    "wide.py": "z" * 20000 + "\n"})
    _, out = box.call("read_file_range", {"path": "empty.py", "start": 1, "end": 5})
    assert "empty" in out
    _, out = box.call("read_file_range", {"path": "no_nl.py", "start": 1, "end": 5})
    assert ": first" in out and ": last" in out
    _, out = box.call("read_file_range", {"path": "naïve file.py", "start": 1, "end": 1})
    assert "héllo" in out
    _, out = box.call("read_file_range", {"path": "wide.py", "start": 1, "end": 1})
    assert len(out) <= READ_CHARS


def test_read_file_range_redacts_before_numbering(tmp_path):
    src = f'x = 1\nTOKEN = "{KEY}"\ny = 2\n'
    _, box = build(tmp_path, {"a.py": "x\n"}, {"conf.py": src})
    _, out = box.call("read_file_range", {"path": "conf.py", "start": 1, "end": 3})
    assert KEY not in out and "Kj3nX9pQ2vLm" not in out
    assert ": y = 2" in out


def test_secret_file_withheld(tmp_path):
    _, box = build(tmp_path, {"a.py": "x\n"}, {".env": f"TOKEN={KEY}\n", "certs/server.pem": "abc\n"})
    eid, text = box.call("read_file_range", {"path": ".env", "start": 1, "end": 5})
    assert eid == "E1" and text == WITHHELD and box.log.get("E1")["result"] == WITHHELD
    assert box.call("read_file_range", {"path": "certs/server.pem", "start": 1, "end": 5})[1] == WITHHELD
    assert box.call("read_hunk", {"path": ".env", "index": 0})[1] == WITHHELD


def test_binary_and_deleted_file_messages(tmp_path):
    root = make_repo(tmp_path / "r", [{"files": {"gone.py": "x\n", "a.py": "y\n"}}])
    run(root, "checkout", "-q", "-b", "feature")
    (root / "gone.py").unlink()
    (root / "blob.dat").write_bytes(b"\x00\x01\x02binary")
    run(root, "add", "-A")
    run(root, "commit", "-q", "-m", "work")
    box = ToolBox(PRContext(root, "main", "feature", t0()), EvidenceLog())
    _, msg = box.call("read_file_range", {"path": "gone.py", "start": 1, "end": 3})
    assert "does not exist at head" in msg and "gone.py" in msg
    _, msg = box.call("read_file_range", {"path": "blob.dat", "start": 1, "end": 3})
    assert "binary" in msg and "\x00" not in msg
    _, msg = box.call("read_file_range", {"path": "no/such.py", "start": 1, "end": 3})
    assert "does not exist at head" in msg


def test_read_file_range_directory_and_submodule_are_plain(tmp_path):
    _, box = build(tmp_path, {"pkg/m.py": "x\n"}, {"pkg/m.py": "y\n"})
    eid, msg = box.call("read_file_range", {"path": "pkg", "start": 1, "end": 3})
    assert eid == "" and msg == "Could not read pkg."


# --- callers_of -----------------------------------------------------------------------

def test_callers_of_uses_tier0_edges(tmp_path):
    files = ["lib.py", "a.py", "b.py", "z.py"]
    edges = {("z.py", "lib.py"), ("a.py", "lib.py"), ("b.py", "a.py")}
    _, box = build(tmp_path, {"lib.py": "x\n"}, {"lib.py": "y\n"}, tier=t0(files, edges))
    eid, text = box.call("callers_of", {"path": "lib.py"})
    assert eid == "E1" and text.splitlines().index("a.py") < text.splitlines().index("z.py")
    assert "b.py" not in text
    assert box.call("callers_of", {"path": "b.py"})[1] == "No other indexed file imports b.py."
    assert box.call("callers_of", {"path": "z.py"})[1] == "No other indexed file imports z.py."
    assert box.call("callers_of", {"path": "nope.py"})[1] == "No index entry for nope.py."
    assert box.reads == 0


def test_callers_of_capped_and_tree_only(tmp_path):
    files = ["lib.py"] + [f"c{i:03d}.py" for i in range(100)]
    edges = {(f, "lib.py") for f in files[1:]}
    _, box = build(tmp_path, {"lib.py": "x\n"}, {"lib.py": "y\n"}, tier=t0(files, edges))
    _, text = box.call("callers_of", {"path": "lib.py"})
    assert sum(1 for ln in text.splitlines() if ln.startswith("c0")) == MAX_LIST
    assert "... and 40 more" in text
    _, box2 = build(tmp_path, {"lib.py": "x\n"}, {"lib.py": "y\n"}, name="r2", tier=t0(["lib.py"], (), "tree_only"))
    assert "not available" in box2.call("callers_of", {"path": "lib.py"})[1]
    assert box2.call("callers_of", {"path": "../x"})[1] == "That path is not allowed."


# --- budget, repeats, errors ----------------------------------------------------------

def test_budget_exhausted_message(tmp_path):
    base = {f"f{i}.py": "a\n" for i in range(8)}
    _, box = build(tmp_path, base, {f"f{i}.py": "a\nb\n" for i in range(8)})
    for i in range(MAX_READS):
        eid, _ = box.call("read_hunk", {"path": f"f{i}.py", "index": 0})
        assert eid == f"E{i + 1}"
    n = len(box.log)
    assert box.reads == MAX_READS
    eid, msg = box.call("read_hunk", {"path": "f6.py", "index": 0})
    assert (eid, msg) == ("", "Read budget used up; submit your report.")
    assert box.call("read_file_range", {"path": "f7.py", "start": 1, "end": 2}) == ("", msg)
    assert len(box.log) == n
    # repeats and the non-read tools still work
    assert box.call("read_hunk", {"path": "f0.py", "index": 0})[0] == "E1"
    assert box.call("list_changed_files", {})[0] == f"E{n + 1}"
    assert box.call("callers_of", {"path": "f0.py"})[0] == f"E{n + 2}"


def test_repeat_call_reuses_evidence_id(tmp_path):
    _, box = build(tmp_path, {"a.py": "a\n"}, {"a.py": "a\nb\n"})
    e1, t1 = box.call("read_hunk", {"path": "a.py", "index": 0})
    e2, t2 = box.call("read_hunk", {"path": "a.py", "index": "0"})
    assert e1 == e2 == "E1" and t1 == t2 and box.reads == 1 and len(box.log) == 1
    assert box.call("list_changed_files", {})[0] == box.call("list_changed_files", {})[0] == "E2"


def test_invalid_args_do_not_use_budget(tmp_path):
    _, box = build(tmp_path, {"a.py": "a\n"}, {"a.py": "a\nb\n"})
    for _ in range(10):
        assert box.call("read_file_range", {"path": "a.py", "start": "x", "end": 1})[0] == ""
    assert box.reads == 0 and len(box.log) == 0


def test_unknown_tool_and_bad_args_do_not_raise(tmp_path):
    _, box = build(tmp_path, {"a.py": "a\n"}, {"a.py": "a\nb\n"})
    for name, args in [("rm_rf", {}), ("read_hunk", {}), ("read_hunk", {"path": "a.py"}),
                       ("read_file_range", {"path": "a.py", "start": 1}), ("callers_of", None),
                       ("read_hunk", "not a dict"), (None, {}), ("list_changed_files", [1])]:
        eid, msg = box.call(name, args)
        assert eid == "" and isinstance(msg, str) and msg
    assert len(box.log) == 0 and box.reads == 0


def test_git_failure_becomes_message(tmp_path):
    root, box = build(tmp_path, {"a.py": "a\n"}, {"a.py": "a\nb\n"})
    box.ctx.root = tmp_path / "missing_dir"
    eid, msg = box.call("list_changed_files", {})
    assert eid == "" and isinstance(msg, str) and msg and "Traceback" not in msg


def test_toolbox_rejects_bad_refs(tmp_path):
    root = make_repo(tmp_path / "r", [{"files": {"a.py": "x\n"}}])
    with pytest.raises(ValueError):
        ToolBox(PRContext(root, "--output=x", "main", t0()), EvidenceLog())
    with pytest.raises(ValueError):
        ToolBox(PRContext(root, "main", "a..b", t0()), EvidenceLog())


def test_schemas_describe_four_tools_and_budgets():
    names = {s["function"]["name"] for s in SCHEMAS}
    assert names == {"list_changed_files", "read_hunk", "read_file_range", "callers_of"}
    assert all(s["type"] == "function" and s["function"]["parameters"]["type"] == "object" for s in SCHEMAS)
    blob = " ".join(s["function"]["description"] for s in SCHEMAS)
    assert f"{MAX_READS} reads" in blob and str(READ_CHARS) in blob
    assert gitread.valid_ref("main")


# --- fix round 1: listing cap, path aliases, size guard, collapsed redaction ----------

def _check_listing(text, total, row_prefix):
    lines = text.splitlines()
    assert len(text) <= READ_CHARS
    assert lines[-1].startswith("... and ") and lines[-1].endswith(" more")
    n = int(lines[-1].split()[2])
    rows = [ln for ln in lines if ln.startswith(row_prefix)]
    assert len(rows) + n == total and n > 0
    assert all(ln.endswith(".py") or ln.endswith(")") for ln in rows)    # no row cut mid-way
    assert "[truncated]" not in text


def test_list_changed_files_long_paths_keep_more_row(tmp_path):
    files = {f"{'d' * 60}/f{i:03d}_{'n' * 6}.py": f"v = {i}\n" for i in range(315)}
    _, box = build(tmp_path, {"README.md": "hi\n"}, files)
    _, text = box.call("list_changed_files", {})
    assert text.splitlines()[0] == "Changed files: 315"
    _check_listing(text, 315, "added ")


def test_callers_of_long_paths_keep_more_row(tmp_path):
    files = ["lib.py"] + [f"{'p' * 70}/c{i:03d}.py" for i in range(200)]
    edges = {(f, "lib.py") for f in files[1:]}
    _, box = build(tmp_path, {"lib.py": "x\n"}, {"lib.py": "y\n"}, tier=t0(files, edges))
    _, text = box.call("callers_of", {"path": "lib.py"})
    _check_listing(text, 200, "p" * 70)


@pytest.mark.parametrize("bad", ["./a.py", "a/./b.py", "a/.", "x" * 301, "a/" + "y" * 5000])
def test_aliases_and_overlong_paths_refused(tmp_path, bad):
    _, box = build(tmp_path, {"a.py": "x\n"}, {"a.py": "y\n"})
    for name, args in [("read_file_range", {"path": bad, "start": 1, "end": 2}),
                       ("read_hunk", {"path": bad, "index": 0}), ("callers_of", {"path": bad})]:
        assert box.call(name, args) == ("", "That path is not allowed.")
    assert box.reads == 0 and len(box.log) == 0


def test_read_file_range_too_large_not_logged(tmp_path):
    big = "a = 1\n" * 350_000                      # about 2.1 MB
    _, box = build(tmp_path, {"a.py": "x\n"}, {"big.py": big, "ok.py": "fine\n"})
    assert box.call("read_file_range", {"path": "big.py", "start": 1, "end": 3}) == ("", "big.py is too large to show.")
    assert box.reads == 0 and len(box.log) == 0
    assert ": fine" in box.call("read_file_range", {"path": "ok.py", "start": 1, "end": 1})[1]


def test_read_file_range_notes_collapsed_redaction(tmp_path):
    pem = ("-----BEGIN RSA PRIVATE KEY-----\n" + "".join(f"Mq{i}Zk8Vt3Wn5Lx7Rb2Hd4Jc6Fs9Gy1Pa0Ue\n" for i in range(6))
           + "-----END RSA PRIVATE KEY-----\n")
    src = "first = 1\n" + pem + "after = 2\n"        # 10 lines in the file
    _, box = build(tmp_path, {"a.py": "x\n"}, {"conf.py": src})
    _, out = box.call("read_file_range", {"path": "conf.py", "start": 1, "end": 20})
    assert out.splitlines()[0] == "conf.py lines 1-3 of 10"
    assert "(note: a redacted block was collapsed; line numbers after it may not match the file)" in out
    assert ": after = 2" in out and "Mq0Zk8Vt3" not in out and "PRIVATE KEY" not in out
    _, plain = box.call("read_file_range", {"path": "a.py", "start": 1, "end": 1})
    assert "note:" not in plain
    assert "redacted block" in " ".join(s["function"]["description"] for s in SCHEMAS)


# --- final fix wave: bounded read_hunk, listing reserve, post-redaction line count ------

def test_read_hunk_of_a_large_change_is_bounded(tmp_path, monkeypatch):
    import time
    import tracemalloc

    from gh_analyzer.agents import pr_tools
    big = "".join(f"line_{i:07d} = 'some ordinary text value here'\n" for i in range(30_000))   # ~1.4 MB
    assert 1_000_000 < len(big) < pr_tools.MAX_FILE_BYTES
    _, box = build(tmp_path, {"a.py": "x\n"}, {"a.py": "x\n" + big})
    seen = []
    real = pr_tools.safe_text

    def spy(text, *a, **kw):
        seen.append(len(text))
        return real(text, *a, **kw)
    monkeypatch.setattr(pr_tools, "safe_text", spy)
    tracemalloc.start()
    start = time.monotonic()
    try:
        eid, text = box.call("read_hunk", {"path": "a.py", "index": 0})
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert time.monotonic() - start < 20
    assert eid and len(text) <= READ_CHARS and "@@" in text
    assert seen and max(seen) <= 4 * READ_CHARS          # capped before redaction
    assert peak < 8 * len(big)


def test_read_hunk_refuses_oversized_blobs_before_diffing(tmp_path, monkeypatch):
    from gh_analyzer.agents import pr_tools
    big = "a = 1\n" * 350_000                              # about 2.1 MB
    gone = "b = 2\n" * 350_000                             # different content, so not paired as a rename
    _, box = build(tmp_path, {"a.py": "x\n", "gone.py": gone}, {"big.py": big, "gone.py": None})

    def no_diff(*a, **k):
        raise AssertionError("the diff of an oversized file must not be read")
    monkeypatch.setattr(pr_tools, "hunks", no_diff)
    assert box.call("read_hunk", {"path": "big.py", "index": 0}) == ("", "big.py is too large to show.")
    assert box.call("read_hunk", {"path": "gone.py", "index": 0}) == ("", "gone.py is too large to show.")
    assert box.reads == 0 and len(box.log) == 0


def test_listing_reserves_no_more_row_when_everything_fits(tmp_path):
    files = ["lib.py"] + [f"{'p' * 61}/c{i:03d}.py" for i in range(57)]    # 57 rows of 69 chars: 3989 chars
    edges = {(f, "lib.py") for f in files[1:]}
    _, box = build(tmp_path, {"lib.py": "x\n"}, {"lib.py": "y\n"}, tier=t0(files, edges))
    _, text = box.call("callers_of", {"path": "lib.py"})
    assert len(text) <= READ_CHARS
    assert "more" not in text
    assert len(text.splitlines()) == 57


def test_out_of_range_message_uses_lines_the_model_sees(tmp_path):
    pem = ("-----BEGIN RSA PRIVATE KEY-----\n" + "".join(f"Mq{i}Zk8Vt3Wn5Lx7Rb2Hd4Jc6Fs9Gy1Pa0Ue\n" for i in range(6))
           + "-----END RSA PRIVATE KEY-----\n")
    src = "first = 1\n" + pem + "after = 2\n"            # 10 lines in the file, 3 after redaction
    _, box = build(tmp_path, {"a.py": "x\n"}, {"conf.py": src})
    _, out = box.call("read_file_range", {"path": "conf.py", "start": 5, "end": 8})
    assert "10" not in out
    assert "3 line(s)" in out
    _, plain = box.call("read_file_range", {"path": "a.py", "start": 5, "end": 8})
    assert plain == "a.py has only 1 line(s)."
