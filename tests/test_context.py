from __future__ import annotations

import json

import pytest

from gh_analyzer import context
from gh_analyzer.repomap import Symbol, Tier0


def _t0(n_files: int = 500, n_syms: int = 50, sig_len: int = 120, depth: int = 5) -> Tier0:
    files = [f"pkg{i % 7}/sub{i % 5}/deep{i % 3}/leaf/mod{i}.py" if depth > 3 else f"mod{i}.py" for i in range(n_files)]
    in_degree = {f: i for i, f in enumerate(files)}            # last file has the highest in-degree
    symbols = {
        f: [Symbol(f"fn{j}", "function", j + 1, f"def fn{j}(" + "a: int, " * (sig_len // 8) + ")") for j in range(n_syms)]
        for f in files
    }
    return Tier0(files, set(), in_degree, symbols, [], {"Python": 1000, "Go": 50}, "full")


def test_estimate_tokens():
    assert context.estimate_tokens("") == 0
    assert context.estimate_tokens("abcd") == 1
    assert context.estimate_tokens("abcde") == 2
    assert context.estimate_tokens("x" * 400) == 100


def test_budget_never_exceeded():
    t0 = _t0(500, 50, sig_len=400)
    text, tokens = context.build_tier1(t0, "readme " * 2000)
    assert tokens <= context.TIER1_BUDGET
    assert tokens == context.estimate_tokens(text)
    assert t0.files[-1] in text                                 # highest in-degree survives


def test_budget_holds_for_tiny_budget_and_empty_input():
    text, tokens = context.build_tier1(_t0(50, 10), "r" * 5000, budget=20)
    assert tokens <= 20 and tokens == context.estimate_tokens(text)
    empty = Tier0([], set(), {}, {}, [], {}, "tree_only")
    text, tokens = context.build_tier1(empty, "")
    assert tokens <= context.TIER1_BUDGET and tokens == context.estimate_tokens(text)


def test_non_ascii_readme_and_paths():
    t0 = Tier0(["src/café.py"], set(), {"src/café.py": 1}, {"src/café.py": [Symbol("f", "function", 1, "def f()")]},
               [], {"Python": 3}, "full")
    text, tokens = context.build_tier1(t0, "Résumé ✓ 日本語")
    assert "café.py" in text and "日本語" in text
    assert tokens <= context.TIER1_BUDGET


def test_deterministic():
    a = context.build_tier1(_t0(60, 5), "hello")
    b = context.build_tier1(_t0(60, 5), "hello")
    assert a == b


def test_truncation_order():
    t0 = _t0(120, 40, sig_len=100)
    readme = "word " * 600                                        # 3000 chars
    r = context._render
    full = context.estimate_tokens(r(t0, readme, context.SIGS_PER_FILE, None, context.README_CHARS, len(t0.files)))
    s1 = context.estimate_tokens(r(t0, readme, 6, None, context.README_CHARS, len(t0.files)))
    s2 = context.estimate_tokens(r(t0, readme, 6, 2, context.README_CHARS, len(t0.files)))
    s3 = context.estimate_tokens(r(t0, readme, 6, 2, 600, len(t0.files)))
    assert full > s1 > s2 > s3

    def run(budget):
        return context.build_tier1(t0, readme, budget=budget)[0]

    # each budget is just enough for one more step, so exactly that many steps must have been applied
    assert run(full) == r(t0, readme, context.SIGS_PER_FILE, None, context.README_CHARS, len(t0.files))
    assert run(full - 1) == r(t0, readme, 6, None, context.README_CHARS, len(t0.files))
    assert run(s1 - 1) == r(t0, readme, 6, 2, context.README_CHARS, len(t0.files))
    assert run(s2 - 1) == r(t0, readme, 6, 2, 600, len(t0.files))

    # only then are files dropped, lowest in-degree first, and the top file is never dropped
    text = run(s3 - 1)
    assert len(text) < len(r(t0, readme, 6, 2, 600, len(t0.files)))
    assert t0.files[-1] in text and t0.files[0] not in text
    small = context.build_tier1(t0, readme, budget=400)[0]
    assert t0.files[-1] in small
    assert context.estimate_tokens(small) <= 400


def test_ties_dropped_by_path_deterministically():
    files = [f"f{i}.py" for i in range(10)]
    t0 = Tier0(files, set(), {f: 0 for f in files}, {}, [], {"Python": 1}, "full")
    ranked = context._ranked(t0)
    assert ranked == sorted(files)


def test_cache_hit_skips_builder(tmp_path):
    calls = []

    def builder():
        calls.append(1)
        return {"text": "hi", "tokens": 1}

    v1, hit1 = context.cached(tmp_path, "abc123", "tier1", builder)
    v2, hit2 = context.cached(tmp_path, "abc123", "tier1", builder)
    assert (hit1, hit2) == (False, True)
    assert v1 == v2 == {"text": "hi", "tokens": 1}
    assert len(calls) == 1
    assert json.loads((tmp_path / "abc123" / "tier1.json").read_text(encoding="utf-8")) == v1
    assert [p.name for p in (tmp_path / "abc123").iterdir()] == ["tier1.json"]      # no stray temp files


def test_cache_corrupt_file_rebuilds(tmp_path):
    (tmp_path / "abc").mkdir()
    (tmp_path / "abc" / "tier1.json").write_text("{not json", encoding="utf-8")
    value, hit = context.cached(tmp_path, "abc", "tier1", lambda: {"ok": True})
    assert (value, hit) == ({"ok": True}, False)
    assert context.cached(tmp_path, "abc", "tier1", lambda: {"ok": False}) == ({"ok": True}, True)


def test_cache_non_dict_json_is_a_miss(tmp_path):
    (tmp_path / "abc").mkdir()
    (tmp_path / "abc" / "k.json").write_text("[1, 2]", encoding="utf-8")
    assert context.cached(tmp_path, "abc", "k", lambda: {"a": 1}) == ({"a": 1}, False)


@pytest.mark.parametrize("bad", ["", "..", "../x", "a/b", "a\\b", "x..y", "/abs"])
def test_cache_rejects_unsafe_path_components(tmp_path, bad):
    called = []
    with pytest.raises(ValueError):
        context.cached(tmp_path, bad, "tier1", lambda: called.append(1) or {})
    with pytest.raises(ValueError):
        context.cached(tmp_path, "abc", bad, lambda: called.append(1) or {})
    assert not called
    assert list(tmp_path.iterdir()) == []


def test_cached_validate_rejects_stale_entry(tmp_path):
    context.cached(tmp_path, "abc", "k", lambda: {"old": 1})
    value, hit = context.cached(tmp_path, "abc", "k", lambda: {"new": 1}, validate=lambda v: "new" in v)
    assert (value, hit) == ({"new": 1}, False)
    assert json.loads((tmp_path / "abc" / "k.json").read_text(encoding="utf-8")) == {"new": 1}
    assert context.cached(tmp_path, "abc", "k", lambda: {"x": 1}, validate=lambda v: "new" in v) == ({"new": 1}, True)


def test_cached_validate_default_unchanged(tmp_path):
    context.cached(tmp_path, "abc", "k", lambda: {"old": 1})
    assert context.cached(tmp_path, "abc", "k", lambda: {"new": 1}) == ({"old": 1}, True)


def test_cached_hit_and_miss_identical(tmp_path):
    def build():
        return {"z": (1, 2), "a": {"y": 1, "b": 2}}
    miss, h1 = context.cached(tmp_path, "abc", "k", build)
    hit, h2 = context.cached(tmp_path, "abc", "k", build)
    assert (h1, h2) == (False, True)
    assert miss == hit and json.dumps(miss) == json.dumps(hit)
    assert miss["z"] == [1, 2] and list(miss) == ["z", "a"]
