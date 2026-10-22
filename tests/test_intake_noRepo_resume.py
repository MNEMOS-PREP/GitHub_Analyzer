"""Intake (FR-3.1), the no-repo round (FR-3.18), the resume link (FR-3.13), timing (FR-3.17) and the CI safety scans (AC-3.1..3)."""

import ast
import json
import time
from pathlib import Path

import pytest

from gh_analyzer import guard, intake, no_repo, pipeline, resume_link
from test_pipeline_safety import A, build

PKG = Path(__file__).resolve().parent.parent / "gh_analyzer"


# ── intake ───────────────────────────────────────────────────────────────

def repo_json(name, size=100, fork=False, private=False, desc="a thing", created="2026-01-01T00:00:00Z", pushed="2026-04-01T00:00:00Z"):
    return {"name": name, "full_name": f"me/{name}", "size": size, "fork": fork, "private": private, "description": desc,
            "created_at": created, "pushed_at": pushed}


def test_oauth_url_asks_for_no_scope_so_access_is_read_only_public():
    url = intake.authorize_url("cid", "https://app/cb", "st")
    assert "client_id=cid" in url and "state=st" in url and "scope" not in url
    assert intake.new_state() != intake.new_state()


def test_repo_list_is_ranked_hides_private_and_shows_top_eight_EC_3_17():
    body = [repo_json(f"proj{i}", size=i * 50) for i in range(1, 21)] + [repo_json("secret", private=True), repo_json("leetcode-dsa", size=999)]
    c = intake.GitHubClient("tok", transport=lambda m, u, h, p: (200, {}, body))
    out = c.list_repos()
    assert out["total_public"] == 21 and out["shown"] == 8 and len(out["repos"]) == 8
    assert "secret" not in json.dumps(out) and "private" in out["note"].lower()
    assert "leetcode-dsa" not in [r["name"] for r in out["repos"]]                  # practice code ranks below real projects
    found = c.search_repos("dsa")
    assert [r["name"] for r in found] == ["leetcode-dsa"]                           # the rest are searchable


def test_rate_limit_backs_off_then_succeeds_EC_3_15():
    calls, slept, said = [], [], []

    def transport(m, u, h, p):
        calls.append(1)
        if len(calls) < 3:
            return 403, {"x-ratelimit-remaining": "0", "retry-after": "2"}, None
        return 200, {}, []

    c = intake.GitHubClient("t", transport=transport, sleep=slept.append)
    c.on_wait = said.append
    assert c.list_repos()["repos"] == []
    assert slept == [2.0, 2.0] and said == [intake.BUSY, intake.BUSY]


def test_rate_limit_that_never_clears_gives_a_named_error_not_a_hang():
    c = intake.GitHubClient("t", transport=lambda *a: (429, {"retry-after": "1"}, None), sleep=lambda s: None, max_retries=2)
    with pytest.raises(intake.RateLimited) as e:
        c.list_repos()
    assert "keep trying" in str(e.value) and e.value.code == "rate_limited"


def test_rate_limit_reset_header_is_honoured():
    n = []
    c = intake.GitHubClient("t", transport=lambda *a: (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1030"}, None) if not n.append(1) and len(n) < 2 else (200, {}, []),
                            sleep=lambda s: n.append(("slept", s)), clock=lambda: 1000.0)
    c.list_repos()
    assert ("slept", 31.0) in n


def test_a_private_or_missing_repo_offers_zip_or_skip_EC_3_6():
    c = intake.GitHubClient(transport=lambda *a: (404, {}, {"message": "Not Found"}))
    with pytest.raises(intake.IntakeError) as e:
        intake.resolve("owner/hidden", c)
    assert e.value.code == "private_or_missing" and {"upload_zip", "continue_without_repo"} <= set(e.value.offer)


def test_resolve_pins_a_sha_for_a_public_repo():
    def transport(m, url, h, p):
        if url.endswith("/repos/o/n"):
            return 200, {}, {"default_branch": "main", "fork": True, "private": False}
        if "/commits/main" in url:
            return 200, {}, {"sha": "abc123"}
        return 500, {}, None

    info = intake.resolve("https://github.com/o/n", intake.GitHubClient(transport=transport))
    assert info["sha"] == "abc123" and info["is_fork"] is True and info["repo"] == "o/n"


def test_oauth_exchange_failure_is_a_named_error():
    c = intake.GitHubClient(transport=lambda *a: (200, {}, {"error": "bad_verification_code"}))
    with pytest.raises(intake.IntakeError) as e:
        c.exchange_code("id", "secret", "code")
    assert e.value.code == "oauth_failed" and "paste" in " ".join(e.value.offer)


# ── no-repo path ────────────────────────────────────────────────────────

def test_the_no_repo_round_has_the_same_four_step_ladder_per_project_FR_3_18():
    r = no_repo.project_round([{"title": "Hostel booking app", "tech": ["Django", "Postgres"]}, {"title": "Spam filter"}])
    depths = [q["depth"] for q in r["questions"] if q["project"] == "Hostel booking app"]
    assert {0, 1, 2, 3} <= set(depths)
    assert all(q["citation"] is None and q["fallback_if_unanswered"] and q["affects_score"] is False for q in r["questions"])
    assert any("Django" in q["question"] for q in r["questions"])
    assert r["affects_score"] is False and len(r["starter_builds"]) == 3 and all(b["hours"] == 6 for b in r["starter_builds"])


def test_the_no_repo_round_works_with_no_projects_at_all():
    r = no_repo.project_round([])
    assert r["generic"] and len(r["questions"]) >= 4


def test_no_repo_wording_has_no_banned_word_and_no_negative_tone():
    r = no_repo.project_round([{"title": "x", "tech": ["a", "b"]}])
    assert no_repo.banned_in(r) == [] and guard.problems(r) == []


def test_a_thin_repo_is_topped_up_to_five_with_resume_questions_FR_3_10(tmp_repo):
    repo = tmp_repo([{"author": A, "files": {"config.yml": "name: demo\n", "notes.txt": "hello\n", "a.py": "x = 1\n"}},
                     {"author": A, "files": {"b.py": "y = 2\n"}}, {"author": A, "files": {"c.py": "z = 3\n"}}])
    claims = [{"id": "c1", "text": "Built a recommender with collaborative filtering", "keywords": ["recommender", "Python"]}]
    s = pipeline.analyse_repo(str(repo), resume_claims=claims)["student"]
    assert len(s["decisions"]) + len(s["supplementary_questions"]) >= 5 or len(s["supplementary_questions"]) >= 4
    assert s["supplementary_questions"] and all(q["citation"] is None for q in s["supplementary_questions"])
    assert any("design questions" in n or "from your resume" in n for n in s["notes"])


# ── resume link ─────────────────────────────────────────────────────────

class Inv:
    frameworks = ["Flask"]
    languages = {"Python": 400}
    dependencies = ["scikit-learn", "flask"]


def test_resume_claims_are_linked_with_verifiability_and_never_a_negative_FR_3_13():
    claims = [{"id": 1, "text": "Built a Flask API with scikit-learn models"}, {"id": 2, "text": "Wrote Kubernetes operators in Rust"},
              {"id": 3, "text": "Python data tooling"}]
    out = {x["claim_id"]: x for x in resume_link.link(claims, "me/model-api", Inv, [])}
    assert out[1]["verifiability"] == "supported" and out[3]["verifiability"] in ("supported", "partly_supported")
    assert out[2]["verifiability"] == "not_checkable"                       # a statement about the repo, not about the student
    assert all(x["score_effect"] == 0 for x in out.values())
    assert not any("contradict" in x["verifiability"] or "false" in x["verifiability"] for x in out.values())


def test_the_pipeline_returns_resume_links_and_the_score_effect_is_zero(tmp_repo):
    result = pipeline.analyse_repo(str(build(tmp_repo)), ["asha@example.com"], resume_claims=[{"id": "a", "text": "Python machine learning project"}])
    links = result["student"]["resume_links"]
    assert links and links[0]["verifiability"] in ("supported", "partly_supported") and links[0]["score_effect"] == 0


# ── performance (FR-3.17 / AC-3.8) ──────────────────────────────────────

def test_a_median_student_repo_is_analysed_well_inside_the_budget(tmp_repo):
    commits = [{"author": A, "date": f"2026-03-{(i % 28) + 1:02d}T10:00:00",
                "files": {f"pkg/m{i}.py": "".join(f"def f{j}(x):\n    return x * {j + 3}\n\n" for j in range(15))}, "msg": f"module {i}"} for i in range(60)]
    repo = tmp_repo(commits)
    t = time.perf_counter()
    result = pipeline.analyse_repo(str(repo), ["asha@example.com"])
    wall = time.perf_counter() - t
    timings = result["private"]["audit"]["timings_s"]
    assert wall < 65, f"local analysis took {wall:.1f}s; the budget is 90s minus a 25s clone"         # AC-3.8
    assert {"snapshot", "inventory", "contribution", "decisions", "questions"} <= set(timings)
    assert "timings_s" not in json.dumps(result["student"])                                          # never shown to the student


# ── CI safety scans (AC-3.1, AC-3.2, AC-3.3) ────────────────────────────

def _string_constants(path: Path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value


def test_AC_3_1_no_module_source_defines_a_similarity_or_verdict_field_in_a_client_reachable_place():
    skip = {"guard.py", "questions.py", "reference.py"}            # the first two define the word lists; reference.py is internal-only
    offenders = []
    for f in PKG.glob("*.py"):
        if f.name in skip:
            continue
        for ln, s in _string_constants(f):
            if guard.FORBIDDEN_KEYS.search(s) and len(s) < 40 and " " not in s:          # identifier-like strings, i.e. dict keys
                offenders.append(f"{f.name}:{ln}: {s}")
    assert offenders == []


def test_AC_3_1_the_api_module_never_imports_the_reference_matcher():
    tree = ast.parse((PKG / "api.py").read_text(encoding="utf-8"))
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names} | \
            {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert "reference" not in names and "ranking" not in names


def test_AC_3_1_a_full_run_has_no_forbidden_key_or_word_anywhere_a_client_can_see(tmp_repo):
    for repo in (build(tmp_repo), tmp_repo([{"author": A, "files": {"train.py": "learning_rate = 0.01\n", "a.py": "x = 1\n"}}], name="tiny")):
        result = pipeline.analyse_repo(str(repo), ["asha@example.com"], resume_claims=[{"id": 1, "text": "ML project"}])
        assert guard.problems(result["student"]) == []


def test_AC_3_2_a_repo_never_changes_a_score_the_module_has_no_score_output():
    # The module emits no score for the student at all; the only numeric outputs about a student are a range and counts.
    # This pins the contract: any new field with 'score' in its name must be justified here.
    allowed = {"interest_score", "usefulness", "score_effect", "affects_score", "scored"}
    found = set()
    for f in PKG.glob("*.py"):
        for _, s in _string_constants(f):
            if "score" in s and " " not in s and len(s) < 30:
                found.add(s)
    assert found <= allowed | {"score"}, found - allowed


def test_AC_3_2_every_object_that_could_touch_a_score_declares_zero_effect(tmp_repo):
    s = pipeline.analyse_repo(str(build(tmp_repo)), ["asha@example.com"], resume_claims=[{"id": 1, "text": "anything at all"}])["student"]
    assert all(l["score_effect"] == 0 for l in s["resume_links"])
    assert pipeline.selection.NO_REPO["affects_score"] is False and no_repo.project_round([])["affects_score"] is False


def test_AC_3_3_nothing_in_the_package_can_execute_repository_code():
    bare = {"exec", "eval", "compile", "__import__"}                       # builtins: only dangerous when called as a bare name
    attrs = {"system", "popen", "import_module", "run_path", "run_module", "spawnl", "spawnv", "execv", "execl"}
    bad = []
    for f in PKG.glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                fn = node.func
                if (isinstance(fn, ast.Name) and fn.id in bare) or (isinstance(fn, ast.Attribute) and fn.attr in attrs):
                    bad.append(f"{f.name}:{node.lineno}")
    assert bad == []
    # and the only subprocess in the package is git
    for f in PKG.glob("*.py"):
        text = f.read_text(encoding="utf-8")
        if "subprocess" in text:
            assert f.name in {"snapshot.py"}, f.name
