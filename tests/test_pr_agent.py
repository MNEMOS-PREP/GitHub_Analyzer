"""The PR agent loop: an evidence-cited, teach-first review driven by a scripted fake model."""

from __future__ import annotations

import json
import re
import time

import pytest

from agent_fakes import FakeClient, tool_call
from conftest import make_repo, run
from gh_analyzer.agents import pr_agent
from gh_analyzer.agents.llm import LLMError, Usage
from gh_analyzer.agents.pr_agent import (
    MAX_PROMPT_CHARS, MAX_STEPS, RUN_TOKEN_BUDGET, SUBMIT_SCHEMA, SYSTEM_PROMPT, review_pr,
)
from gh_analyzer.agents.pr_tools import MAX_LIST, READ_CHARS
from gh_analyzer.repomap import Tier0

# Built from pieces so no scanner flags this file; high entropy so redaction must catch it.
KEY = "sk-" + "Qw7eR4tY1uI9oP3a" + "Sd6fG2hJ8kL5zX0c"
FORCE = {"type": "function", "function": {"name": "submit_report"}}
STUB = "I could not finish this review (the model did not return a usable report). Nothing was changed."
KEYS = {"status", "summary", "changes", "checklist", "suggested_title", "suggested_description",
        "findings", "dropped", "verified", "evidence", "tokens", "notes"}
IDENT = {"GIT_AUTHOR_NAME": "Student", "GIT_AUTHOR_EMAIL": "student@example.com",
         "GIT_COMMITTER_NAME": "Student", "GIT_COMMITTER_EMAIL": "student@example.com"}

BASE = {"app.py": "def add(a, b):\n    return a + b\n"}
HEAD = {"app.py": "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"}

SUPPORTED = {"role": "assistant", "content": '{"supported": true, "reason": "the hunk shows it"}'}
UNSUPPORTED = {"role": "assistant", "content": '{"supported": false, "reason": "not shown"}'}


def t0(files=("app.py",)) -> Tier0:
    return Tier0(files=list(files), edges=set(), in_degree={}, symbols={}, entry_points=[],
                 languages={}, graph_quality="full")


def feature_repo(tmp_path, base=BASE, head=HEAD, name="repo"):
    """`main` holds `base`; branch `feature` adds one commit with `head` (None deletes a file)."""
    root = make_repo(tmp_path / name, [{"files": base, "msg": "base"}])
    run(root, "checkout", "-q", "-b", "feature")
    for rel, text in head.items():
        f = root / rel
        if text is None:
            f.unlink()
        else:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text, encoding="utf-8", newline="\n")
    run(root, "add", "-A", env=IDENT)
    run(root, "commit", "-q", "-m", "feature work", env=IDENT)
    return root


def item(text="Add a unit test for sub", why="A test shows the new function works.", evidence=("E2",)):
    return {"item": text, "why": why, "evidence": list(evidence)}


def submit(summary="Adds a sub function.", checklist=None, changes=None, title="Add sub",
           desc="Adds sub() next to add().", call_id="s1", **extra):
    args = {"summary": summary, "changes": changes if changes is not None else [],
            "checklist": checklist if checklist is not None else [],
            "suggested_title": title, "suggested_description": desc, **extra}
    return tool_call("submit_report", args, call_id)


def multi(*calls, content=None) -> dict:
    """One assistant message carrying several tool calls (each built with `tool_call`)."""
    return {"role": "assistant", "content": content,
            "tool_calls": [c["tool_calls"][0] for c in calls]}


HUNK = tool_call("read_hunk", {"path": "app.py", "index": 0}, "h1")


def review(root, client, tier1="Project: a small calculator.", **kw):
    return review_pr(root, "main", "feature", t0(), tier1, client, **kw)


def sent(client) -> str:
    return json.dumps([c["messages"] for c in client.calls])


def chars(messages) -> int:
    return sum(len(m["content"]) for m in messages if isinstance(m.get("content"), str))


def tool_messages(messages) -> list[dict]:
    return [m for m in messages if m["role"] == "tool"]


# --- the happy path -------------------------------------------------------------------

def test_happy_path(tmp_path):
    root = feature_repo(tmp_path)
    client = FakeClient([
        (tool_call("list_changed_files", {}, "c1"), Usage(10, 1)),
        (HUNK, Usage(20, 2)),
        (submit(checklist=[item()], changes=[{"path": "app.py", "what": "Adds sub"}]), Usage(30, 3)),
        (SUPPORTED, Usage(40, 4)),
    ])
    report = review(root, client)
    assert set(report) == KEYS
    assert report["status"] == "ok" and report["verified"] is True and report["dropped"] == 0
    assert report["summary"] == "Adds a sub function."
    assert report["changes"] == [{"path": "app.py", "what": "Adds sub"}]
    assert report["checklist"] == [item()]
    assert report["findings"] == [{"claim": "Add a unit test for sub A test shows the new function works.",
                                   "severity": "info", "evidence": ["E2"]}]
    assert report["suggested_title"] == "Add sub"
    assert report["suggested_description"] == "Adds sub() next to add()."
    assert report["tokens"] == {"prompt": 100, "completion": 10, "total": 110}
    for entry in report["checklist"]:
        assert all(eid in report["evidence"] for eid in entry["evidence"])
    assert report["evidence"]["E2"] == {"tool": "read_hunk", "args": {"path": "app.py", "index": 0}}
    first = client.calls[0]
    assert first["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert SUBMIT_SCHEMA in first["tools"] and first["max_tokens"] == 1500 and first["tool_choice"] is None
    assert "[E1]" in first["messages"][1]["content"] and "app.py" in first["messages"][1]["content"]


def test_plain_json_content_is_accepted_as_report(tmp_path):
    root = feature_repo(tmp_path)
    content = 'Here it is: {"summary": "Adds sub.", "checklist": []} trailing words'
    client = FakeClient([{"role": "assistant", "content": content}])
    report = review(root, client)
    assert report["status"] == "ok" and report["summary"] == "Adds sub."
    assert len(client.calls) == 1


# --- no changes, bad refs, git errors: no model call ----------------------------------

def test_no_changes_makes_no_llm_call(tmp_path):
    root = feature_repo(tmp_path)
    client = FakeClient([])
    report = review_pr(root, "main", "main", t0(), "x", client)
    assert report["status"] == "no_changes"
    assert report["summary"] == "There are no changes between those two points."
    assert client.calls == []
    run(root, "branch", "same", "main")
    report = review_pr(root, "main", "same", t0(), "x", client)
    assert report["status"] == "no_changes" and client.calls == []
    assert set(report) == KEYS and report["tokens"]["total"] == 0


def test_invalid_ref_rejected(tmp_path, monkeypatch):
    git_calls = []
    monkeypatch.setattr(pr_agent, "changed_files", lambda *a: git_calls.append(a) or [])
    client = FakeClient([])
    for base, head in (("main", "--output=x"), ("-p", "feature"), ("main", "a..b"), ("", "main")):
        report = review_pr(tmp_path, base, head, t0(), "x", client)
        assert report["status"] == "incomplete"
        assert report["summary"] == "That branch or commit name is not valid."
        assert set(report) == KEYS
    assert git_calls == [] and client.calls == []


@pytest.mark.parametrize("refs,summary", [
    (("main", "--upload-pack=x"), "That branch or commit name is not valid."),
    (("main", "feature"), "I could not read the changes between those two points."),
])
def test_no_model_call_for_invalid_ref_or_git_error(tmp_path, refs, summary):
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    client = FakeClient([])
    report = review_pr(not_a_repo, *refs, t0(), "x", client)
    assert report["status"] == "incomplete" and report["summary"] == summary
    assert client.calls == [] and report["evidence"] == {}


# --- secrets ----------------------------------------------------------------------------

def test_secret_in_diff_never_sent(tmp_path):
    head = {"app.py": HEAD["app.py"] + f'\nAPI_KEY = "{KEY}"\n'}
    root = feature_repo(tmp_path, head=head)
    client = FakeClient([HUNK, submit(checklist=[item()]), SUPPORTED])
    lines: list[str] = []
    report = review(root, client, log=lines.append)
    assert report["status"] == "ok"
    text = sent(client)
    assert "[REDACTED]" in text            # the hunk was sent, redacted
    assert KEY not in text
    assert KEY not in json.dumps(report)
    assert lines and all(KEY not in ln for ln in lines)


def test_secret_in_tier1_text_never_sent(tmp_path):
    root = feature_repo(tmp_path)
    client = FakeClient([submit()])
    report = review(root, client, tier1=f"Overview.\ndef connect(key=\"{KEY}\"):\n    pass\n")
    assert report["status"] == "ok"
    assert KEY not in sent(client) and KEY not in json.dumps(report)
    assert "Overview." in client.calls[0]["messages"][1]["content"]


def test_report_contains_no_tool_output(tmp_path):
    head = {"app.py": HEAD["app.py"] + f"marker_zeta_31 = 7\nAPI_KEY = \"{KEY}\"\n"}
    root = feature_repo(tmp_path, head=head)
    client = FakeClient([HUNK, submit(checklist=[item()]), SUPPORTED])
    report = review(root, client)
    dumped = json.dumps(report)
    assert "marker_zeta_31" in sent(client)          # the model did see it
    assert "marker_zeta_31" not in dumped and KEY not in dumped
    assert report["evidence"] == {
        "E1": {"tool": "list_changed_files", "args": {}},
        "E2": {"tool": "read_hunk", "args": {"path": "app.py", "index": 0}},
    }


# --- evidence and verification ------------------------------------------------------

def test_unsupported_finding_dropped_and_counted(tmp_path):
    root = feature_repo(tmp_path)
    client = FakeClient([HUNK, submit(checklist=[item()]), UNSUPPORTED])
    report = review(root, client)
    assert report["status"] == "ok" and report["dropped"] == 1
    assert report["findings"] == [] and report["checklist"] == []


def test_finding_without_evidence_dropped_without_verifier_call(tmp_path):
    root = feature_repo(tmp_path)
    checklist = [item(evidence=["E99"]), item(evidence=[]), {"item": "x", "why": "y"}]
    client = FakeClient([submit(checklist=checklist)])
    report = review(root, client)
    assert report["status"] == "ok" and report["dropped"] == 3
    assert report["checklist"] == [] and len(client.calls) == 1


def test_verify_false_keeps_items_with_valid_evidence(tmp_path):
    root = feature_repo(tmp_path)
    checklist = [item(evidence=["E1", "E1", "E7"]), item(text="Nothing cited", evidence=["E9"])]
    client = FakeClient([submit(checklist=checklist)])
    report = review(root, client, verify=False)
    assert report["verified"] is False and report["dropped"] == 1
    assert report["checklist"] == [item(evidence=["E1"])]
    assert report["findings"][0]["evidence"] == ["E1"]
    assert len(client.calls) == 1


def test_usage_includes_verifier_calls(tmp_path):
    root = feature_repo(tmp_path)
    client = FakeClient([
        (HUNK, Usage(1, 2)),
        (submit(checklist=[item(), item(text="Check the docstring")]), Usage(3, 4)),
        (SUPPORTED, Usage(1000, 2000)),
        (UNSUPPORTED, Usage(500, 700)),
    ])
    report = review(root, client)
    assert report["tokens"] == {"prompt": 1504, "completion": 2706, "total": 4210}
    assert len(report["checklist"]) == 1 and report["dropped"] == 1


# --- the loop -----------------------------------------------------------------------

def test_repeat_call_is_not_resent(tmp_path):
    root = feature_repo(tmp_path)
    client = FakeClient([HUNK, tool_call("read_hunk", {"index": 0, "path": "app.py"}, "h2"),
                         tool_call("list_changed_files", {}, "l1"), submit()])
    review(root, client)
    tools = tool_messages(client.calls[3]["messages"])
    assert tools[0]["content"].startswith("[E2] app.py hunk 0")
    assert tools[1] == {"role": "tool", "tool_call_id": "h2", "content": "Already called; see [E2] above."}
    assert tools[2]["content"] == "Already called; see [E1] above."
    assert json.dumps(client.calls[3]["messages"]).count("def sub(a, b)") == 1


def test_malformed_tool_args_and_non_json_end_the_run(tmp_path):
    root = feature_repo(tmp_path)
    script = [tool_call("read_hunk", "{not json", f"c{i}") if i % 2 == 0
              else {"role": "assistant", "content": "I think it looks fine."} for i in range(MAX_STEPS)]
    script.append({"role": "assistant", "content": "still no report"})
    client = FakeClient(script)
    report = review(root, client)
    assert report["status"] == "incomplete" and report["summary"] == STUB
    assert len(client.calls) == MAX_STEPS + 1
    assert client.calls[-1]["tool_choice"] == FORCE
    text = sent(client)
    assert "Your arguments were not valid JSON." in text and "Finish by calling submit_report." in text
    assert set(report) == KEYS and report["tokens"]["total"] == 150 * (MAX_STEPS + 1)


def test_never_submits_forced_conclusion(tmp_path):
    root = feature_repo(tmp_path)
    script = [tool_call("list_changed_files", {}, f"c{i}") for i in range(MAX_STEPS)]
    script += [submit(checklist=[item(evidence=["E1"])]), SUPPORTED]
    client = FakeClient(script)
    report = review(root, client)
    assert report["status"] == "ok" and len(report["checklist"]) == 1
    forced = client.calls[MAX_STEPS]
    assert forced["tool_choice"] == FORCE
    assert forced["messages"][-1] == {"role": "user", "content": "Submit your report now."}
    assert all(c["tool_choice"] is None for c in client.calls[:MAX_STEPS])
    nudge = {"role": "user", "content": "Only 2 steps left. Submit your report now."}
    assert nudge in client.calls[MAX_STEPS - 2]["messages"]
    assert nudge not in client.calls[MAX_STEPS - 3]["messages"]


def test_token_budget_forces_submit(tmp_path):
    assert RUN_TOKEN_BUDGET == 20000
    root = feature_repo(tmp_path)
    big = Usage(10_000, 5_000)
    client = FakeClient([(HUNK, big), (tool_call("callers_of", {"path": "app.py"}, "k1"), big),
                         (submit(checklist=[item()]), Usage(1, 1)), SUPPORTED])
    report = review(root, client)
    assert [c["tool_choice"] for c in client.calls[:3]] == [None, None, FORCE]
    # the budget is spent, so the verifier is not called and the item is dropped (fail closed)
    assert len(client.calls) == 3
    assert report["status"] == "ok" and report["tokens"]["total"] == 30_002
    assert report["checklist"] == [] and report["dropped"] == 1
    assert report["notes"] == [pr_agent.BUDGET_NOTE]


def test_notes_empty_when_everything_was_checked(tmp_path):
    root = feature_repo(tmp_path)
    client = FakeClient([HUNK, submit(checklist=[item()]), SUPPORTED])
    report = review(root, client)
    assert report["notes"] == [] and len(report["checklist"]) == 1


def test_notes_on_every_report_shape(tmp_path):
    root = feature_repo(tmp_path)
    assert review_pr(root, "main", "main", t0(), "", FakeClient([]))["notes"] == []
    assert review_pr(root, "-bad", "feature", t0(), "", FakeClient([]))["notes"] == []


def test_verifier_stops_when_run_budget_is_reached(tmp_path):
    root = feature_repo(tmp_path)
    checklist = [item(text=f"Check thing {i}") for i in range(4)]
    client = FakeClient([(HUNK, Usage(1000, 100)), (submit(checklist=checklist), Usage(1000, 100)),
                         (SUPPORTED, Usage(RUN_TOKEN_BUDGET, 0)), SUPPORTED, SUPPORTED, SUPPORTED])
    report = review(root, client)
    assert len(client.calls) == 3                      # one verifier call, then the budget is spent
    assert report["status"] == "ok" and report["verified"] is True
    assert [c["item"] for c in report["checklist"]] == ["Check thing 0"]
    assert report["dropped"] == 3
    assert report["notes"] == [pr_agent.BUDGET_NOTE]
    assert set(report) == KEYS


def test_prompt_chars_brake_forces_submit(tmp_path):
    files = {f"m{i}.py": "".join(f"x{n} = {n + 10}\n" for n in range(3000)) for i in range(6)}
    root = feature_repo(tmp_path, base={"app.py": "a = 1\n"}, head=files)
    # Assistant content and each call's arguments are capped at 4000 chars, so fill both to the cap
    # (valid JSON padded with whitespace) to push the conversation past the brake.
    padded = [json.dumps({"path": p, "index": 0}).ljust(pr_agent.ASSISTANT_CHARS) for p in files]
    reads = multi(*(tool_call("read_hunk", raw, f"r{i}") for i, raw in enumerate(padded)),
                  content="N" * 40_000)
    client = FakeClient([reads, submit(), SUPPORTED])
    report = review(root, client, tier1="Q" * 100_000)
    first = client.calls[0]["messages"][1]["content"]
    assert first.count("Q") <= 12_000
    assert client.calls[1]["tool_choice"] == FORCE
    tools = tool_messages(client.calls[1]["messages"])
    assert len(tools) == 6 and all(len(t["content"]) <= READ_CHARS + 10 for t in tools)
    assert any("Not run" in t["content"] for t in tools)
    assert len(report["evidence"]) < 7 and report["status"] == "ok"
    assert chars(client.calls[1]["messages"]) < MAX_PROMPT_CHARS + READ_CHARS + 500


def test_huge_pr_is_bounded(tmp_path):
    files = {f"pkg/m{i:03}.py": f"v = {i}\n" for i in range(300)}
    root = feature_repo(tmp_path, head=files)
    client = FakeClient([submit(checklist=[item(evidence=["E1"])]), SUPPORTED])
    report = review(root, client)
    first = client.calls[0]["messages"][1]["content"]
    assert "Changed files: 300" in first
    rows = [ln for ln in first.splitlines() if ln.startswith("added pkg/")]
    more = re.search(r"\.\.\. and (\d+) more", first)
    assert 0 < len(rows) <= MAX_LIST and more and len(rows) + int(more.group(1)) == 300
    assert all(chars(c["messages"]) < MAX_PROMPT_CHARS for c in client.calls)
    assert report["status"] == "ok"


def test_model_text_appended_to_messages_is_capped(tmp_path):
    root = feature_repo(tmp_path)
    big_args = tool_call("list_changed_files", {"pad": "x" * 50_000}, "c1")
    client = FakeClient([multi(big_args, content="N" * 50_000), submit()])
    report = review(root, client)
    assert report["status"] == "ok"
    msgs = client.calls[1]["messages"]
    assistant = [m for m in msgs if m["role"] == "assistant"][0]
    assert len(assistant["content"]) <= pr_agent.ASSISTANT_CHARS == 4000
    assert all(len(c["function"]["arguments"]) <= 4000 for c in assistant["tool_calls"])
    assert pr_agent._chars(msgs) < MAX_PROMPT_CHARS


def test_tool_calls_per_message_capped_and_fast(tmp_path):
    root = feature_repo(tmp_path)
    flood = {"role": "assistant", "content": None,
             "tool_calls": [tool_call("list_changed_files", {}, f"c{i}")["tool_calls"][0] for i in range(20_000)]}
    client = FakeClient([flood, submit()])
    start = time.monotonic()
    report = review(root, client)
    assert time.monotonic() - start < 2
    assert report["status"] == "ok"
    msgs = client.calls[1]["messages"]
    assistant = [m for m in msgs if m["role"] == "assistant"][0]
    assert len(assistant["tool_calls"]) == pr_agent.MAX_CALLS_PER_MESSAGE == 8
    assert [t["tool_call_id"] for t in tool_messages(msgs)] == [f"c{i}" for i in range(8)]


def test_refs_are_redacted_in_opening_message(tmp_path):
    root = feature_repo(tmp_path)
    run(root, "branch", KEY, "feature")
    client = FakeClient([submit()])
    report = review_pr(root, "main", KEY, t0(), "Project.", client)
    assert report["status"] == "ok"
    assert KEY not in sent(client)
    assert "[REDACTED]" in client.calls[0]["messages"][1]["content"]


@pytest.mark.parametrize("args", [{}, {"summary": ""}, {"summary": "   ", "changes": [], "checklist": []}])
def test_empty_submit_is_incomplete(tmp_path, args):
    root = feature_repo(tmp_path)
    client = FakeClient([tool_call("submit_report", args, "s1")])
    report = review(root, client)
    assert report["status"] == "incomplete" and report["summary"] == STUB
    assert set(report) == KEYS


def test_forced_conclusion_accepts_plain_json(tmp_path):
    root = feature_repo(tmp_path)
    script = [tool_call("list_changed_files", {}, f"c{i}") for i in range(MAX_STEPS)]
    script.append({"role": "assistant", "content": 'Report: {"summary": "Forced but fine."}'})
    client = FakeClient(script)
    report = review(root, client)
    assert client.calls[-1]["tool_choice"] == FORCE
    assert report["status"] == "ok" and report["summary"] == "Forced but fine."


def test_submit_twice_in_one_message_uses_first(tmp_path):
    root = feature_repo(tmp_path)
    client = FakeClient([multi(submit(summary="First.", call_id="a"), submit(summary="Second.", call_id="b"))])
    report = review(root, client)
    assert report["summary"] == "First." and len(client.calls) == 1


def test_tool_calls_before_submit_get_tool_messages(tmp_path):
    root = feature_repo(tmp_path)
    client = FakeClient([multi(HUNK, submit(checklist=[item()])), SUPPORTED])
    report = review(root, client)
    assert report["evidence"]["E2"]["tool"] == "read_hunk"
    assert report["checklist"] == [item()]
    # an invalid submit is not executed: every call in that message is answered and the run goes on
    bad = tool_call("submit_report", "[1, 2]", "s0")
    client = FakeClient([multi(tool_call("read_hunk", {"path": "app.py", "index": 0}, "h1"), bad), submit()])
    report = review(root, client)
    tools = tool_messages(client.calls[1]["messages"])
    assert [t["tool_call_id"] for t in tools] == ["h1", "s0"]
    assert tools[1]["content"] == "Your arguments were not valid JSON."
    assert report["status"] == "ok"


# --- report assembly ----------------------------------------------------------------

def test_changes_with_unknown_path_dropped(tmp_path):
    root = feature_repo(tmp_path)
    changes = [{"path": "app.py", "what": "Adds sub"}, {"path": "nope.py", "what": "Invented"},
               "not a dict", {"what": "no path"}]
    client = FakeClient([submit(changes=changes)])
    report = review(root, client)
    assert report["changes"] == [{"path": "app.py", "what": "Adds sub"}]
    assert report["dropped"] == 3


def test_verdict_scrub_applies_only_to_verdict_phrases(tmp_path):
    root = feature_repo(tmp_path)
    client = FakeClient([submit(
        summary="Approved! This is ready to merge. LGTM.",
        changes=[{"path": "app.py", "what": "copied the original helper; not plagiarism"}],
        title="Merge-ready: add sub", desc="Reviewers rejected nothing; approves sub.")])
    report = review(root, client)
    assert report["summary"] == "[removed]! This is [removed]. [removed]."
    assert report["changes"][0]["what"] == "[removed] the original helper; not [removed]"
    assert report["suggested_title"] == "[removed]: add sub"
    assert report["suggested_description"] == "Reviewers [removed] nothing; [removed] sub."


def test_authorship_wording_is_scrubbed(tmp_path):
    root = feature_repo(tmp_path)
    client = FakeClient([submit(
        summary="This loop was copied from StackOverflow; the original author is unknown.",
        changes=[{"path": "app.py", "what": "Code was Copied verbatim; originality is low; Plagiarised."}],
        title="Original Authors: add sub", desc="Keeps the original behaviour of add().")])
    report = review(root, client)
    assert report["summary"] == "This loop was [removed] StackOverflow; the [removed] is unknown."
    assert report["changes"][0]["what"] == "Code was [removed] verbatim; [removed] is low; [removed]."
    assert report["suggested_title"] == "[removed]: add sub"
    assert report["suggested_description"] == "Keeps the original behaviour of add()."
    out = json.dumps(report).lower()
    for word in ("copied", "original author", "originality", "plagiari"):
        assert word not in out


def test_report_wording_has_no_verdict_words(tmp_path):
    for word in ("approve", "reject", "merge-ready", "LGTM", "submit_report"):
        assert word in SYSTEM_PROMPT
    root = feature_repo(tmp_path)
    client = FakeClient([HUNK, submit(checklist=[item()]), SUPPORTED])
    report = review(root, client)
    assert client.calls[0]["messages"][0]["content"] == SYSTEM_PROMPT
    out = json.dumps(report).lower()
    for word in ("approve", "reject", "merge-ready", "lgtm", "plagiari"):
        assert word not in out


def test_fields_are_capped(tmp_path):
    root = feature_repo(tmp_path)
    many = [item(text=f"Item {i}", evidence=["E1"]) for i in range(20)]
    client = FakeClient([submit(summary="s" * 5000, title="line one\nline two " + "t" * 300,
                                desc="d" * 9000, checklist=many,
                                changes=[{"path": "app.py", "what": "w" * 900}] * 40,
                                extra_key="dropped")]
                        + [SUPPORTED] * 12)
    report = review(root, client)
    assert len(report["summary"]) <= 1200 and len(report["suggested_description"]) <= 2000
    assert len(report["suggested_title"]) <= 100 and "\n" not in report["suggested_title"]
    assert report["suggested_title"].startswith("line one line two")
    assert len(report["checklist"]) == 12 and len(report["changes"]) == 30
    assert all(len(c["what"]) <= 300 for c in report["changes"])
    assert set(report) == KEYS


@pytest.mark.parametrize("args", [
    {"summary": None, "checklist": "do stuff", "changes": {"path": "app.py"}, "suggested_title": ["x"],
     "suggested_description": 5},
    {"summary": 3, "checklist": [{"item": "x", "why": None, "evidence": 4}, 7, None],
     "changes": [{"path": ["app.py"], "what": {}}]},
    {"checklist": [{"item": ["x"], "evidence": ["E1"]}, {"item": "ok", "why": 2, "evidence": "E1"}]},
    {},
])
def test_never_raises_on_odd_submit_shapes(tmp_path, args):
    root = feature_repo(tmp_path)
    client = FakeClient([tool_call("submit_report", args, "s1")] + [SUPPORTED] * 3)
    report = review(root, client)
    # none of these shapes leaves a summary, a valid change or a valid checklist item
    assert set(report) == KEYS and report["status"] == "incomplete"
    assert isinstance(report["summary"], str) and isinstance(report["suggested_title"], str)
    assert isinstance(report["suggested_description"], str) and isinstance(report["dropped"], int)
    assert isinstance(report["changes"], list) and isinstance(report["checklist"], list)


# --- failures never reach the student -------------------------------------------------

def test_llm_error_gives_incomplete_stub(tmp_path):
    root = feature_repo(tmp_path)
    client = FakeClient([HUNK, LLMError("down")])
    report = review(root, client)
    assert report["status"] == "incomplete" and report["summary"] == STUB
    assert report["changes"] == [] and report["checklist"] == [] and report["findings"] == []
    assert report["suggested_title"] == "" and report["suggested_description"] == ""
    assert report["dropped"] == 0 and len(client.calls) == 2
    assert "E2" in report["evidence"] and report["tokens"]["total"] == 150


def test_unexpected_client_exception_gives_incomplete_stub(tmp_path):
    root = feature_repo(tmp_path)

    def boom(_messages):
        raise RuntimeError("socket exploded")

    report = review(root, FakeClient([boom]))
    assert report["status"] == "incomplete" and report["summary"] == STUB
