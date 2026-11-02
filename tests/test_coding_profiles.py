"""Coding profiles: parsing each platform, failing one without hurting the others, ownership check, questions, routes."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from gh_analyzer import coding_profiles as cp
from gh_analyzer import guard
from gh_analyzer.api import create_app
from gh_analyzer.service import AnalyzerService

H = {"X-Candidate-Id": "cand-1"}
NOW = 1_800_000_000.0           # a fixed "today" so the activity windows are stable


def cf_body(method: str, handle: str, bio: str = ""):
    if method == "user.info":
        return {"status": "OK", "result": [{"handle": handle, "rating": 1520, "maxRating": 1610, "rank": "specialist",
                                            "maxRank": "specialist", "firstName": "Asha", "organization": bio}]}
    if method == "user.rating":
        return {"status": "OK", "result": [{"contestId": 1, "contestName": "Round 1", "ratingUpdateTimeSeconds": int(NOW) - 40 * 86400, "oldRating": 1400, "newRating": 1450},
                                           {"contestId": 2}, {"contestId": 3}]}
    day = int(NOW) - 5 * 86400
    prob = lambda i, r, tags: {"contestId": 10, "index": i, "name": f"P{i}", "rating": r, "tags": tags}
    return {"status": "OK", "result": [
        {"creationTimeSeconds": day, "verdict": "OK", "problem": prob("A", 800, ["math", "greedy", "dfs and similar"])},
        {"creationTimeSeconds": day, "verdict": "WRONG_ANSWER", "problem": prob("B", 1400, ["dp"])},
        {"creationTimeSeconds": day + 100, "verdict": "OK", "problem": prob("B", 1400, ["dp", "greedy"])},
        {"creationTimeSeconds": day + 200, "verdict": "OK", "problem": prob("B", 1400, ["dp", "greedy"])},   # same problem again
        {"creationTimeSeconds": day - 400 * 86400, "verdict": "OK", "problem": prob("C", 1900, ["graphs"])},
    ]}


LC_OK = {"data": {"matchedUser": {
    "username": "asha", "profile": {"realName": "Asha", "aboutMe": "", "ranking": 41234},
    "submitStatsGlobal": {"acSubmissionNum": [{"difficulty": "All", "count": 210}, {"difficulty": "Easy", "count": 100},
                                              {"difficulty": "Medium", "count": 90}, {"difficulty": "Hard", "count": 20}]},
    "badges": [{"displayName": "100 Days Badge"}],
    "languageProblemCount": [{"languageName": "Python3", "problemsSolved": 190}, {"languageName": "C++", "problemsSolved": 20}],
    "tagProblemCounts": {"fundamental": [{"tagName": "Array", "problemsSolved": 80}], "intermediate": [{"tagName": "Tree", "problemsSolved": 30}],
                         "advanced": [{"tagName": "Dynamic Programming", "problemsSolved": 25}]},
    "userCalendar": {"streak": 12, "totalActiveDays": 140, "submissionCalendar": json.dumps({"1799000000": 3, "1799500000": 1})}},
    "userContestRanking": {"rating": 1712.6, "attendedContestsCount": 9, "topPercentage": 18.4}}}

CC_HTML = """<html><body><div class="rating-number">1623</div><span class="rating">3&#9733;</span>
<small>(Highest Rating 1701)</small><section><h3>Total Problems Solved: 87</h3></section>
<div>Contests Participated: 14</div> <p>about me gha-CODE</p></body></html>"""


class Net:
    """A fake transport. `rules` maps a URL fragment to a (status, text) or a callable giving one."""

    def __init__(self, **over):
        self.calls, self.over = [], over
        self.bio = ""

    def __call__(self, method, url, headers, body=None):
        self.calls.append((method, url))
        for frag, resp in self.over.items():
            if frag in url:
                r = resp(url, body) if callable(resp) else resp
                if isinstance(r, Exception):
                    raise r
                return r[0], {}, r[1]
        if "codeforces.com/api/" in url:
            m = url.split("/api/")[1].split("?")[0]
            return 200, {}, json.dumps(cf_body(m, "asha", self.bio))
        if "leetcode.com/graphql" in url:
            return 200, {}, json.dumps(LC_OK)
        if "codechef.com/users" in url:
            return 200, {}, CC_HTML.replace("gha-CODE", self.bio)
        return 404, {}, ""


def book(net=None, **kw):
    return cp.CodingProfiles(net or Net(), sleep=lambda s: None, clock=lambda: NOW, **kw)


# ---- handles ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("platform,text,expected", [
    ("codeforces", "https://codeforces.com/profile/Asha_K", "Asha_K"),
    ("leetcode", "https://leetcode.com/u/asha/", "asha"),
    ("leetcode", "leetcode.com/asha", "asha"),
    ("codechef", "https://www.codechef.com/users/asha_cc", "asha_cc"),
    ("codeforces", "@asha", "asha"),
])
def test_links_and_bare_handles_are_accepted(platform, text, expected):
    assert cp.normalise_handle(platform, text) == expected


@pytest.mark.parametrize("text", ["", "a b", "x/../y", "<script>", "a" * 41, "https://evil.example/profile/x"])
def test_junk_is_refused_with_a_named_error(text):
    with pytest.raises(cp.ProfileError) as e:
        cp.normalise_handle("codeforces", text)
    assert e.value.code == "bad_handle"


# ---- platforms --------------------------------------------------------------------------------------------------

def test_codeforces_counts_distinct_problems_and_reads_the_window():
    r = cp.fetch_codeforces("asha", Net(), lambda s: None, NOW)
    s = r["stats"]
    assert s["solved_total"] == 3                       # A, B (twice counts once), C
    assert s["hardest_solved_rating"] == 1900 and s["contests"] == 3 and s["rating"] == 1520
    assert s["top_tags"][0] == {"tag": "greedy", "solved": 2}
    assert s["active_days_last_year"] == 1 and s["truncated"] is False


def test_codeforces_waits_between_calls_as_the_site_asks():
    waits = []
    cp.fetch_codeforces("asha", Net(), waits.append, NOW)
    assert waits == [cp.CF_GAP_S, cp.CF_GAP_S]          # three calls, two gaps


def test_codeforces_unknown_handle_is_not_found():
    net = Net(**{"user.info": (400, json.dumps({"status": "FAILED", "comment": "handles: User with handle zz not found"}))})
    with pytest.raises(cp.ProfileError) as e:
        cp.fetch_codeforces("zz", net, lambda s: None, NOW)
    assert e.value.code == "not_found"


def test_codeforces_rate_limit_retries_once_then_names_it():
    limited = (429, json.dumps({"status": "FAILED", "comment": "Call limit exceeded"}))
    with pytest.raises(cp.ProfileError) as e:
        cp.fetch_codeforces("asha", Net(**{"user.info": limited}), lambda s: None, NOW)
    assert e.value.code == "rate_limited"


def test_leetcode_reads_difficulty_split_and_contest():
    s = cp.fetch_leetcode("asha", Net(), lambda s: None, NOW)["stats"]
    assert (s["solved_total"], s["solved_easy"], s["solved_medium"], s["solved_hard"]) == (210, 100, 90, 20)
    assert s["contest_rating"] == 1713 and s["contests"] == 9 and s["last_active"] is not None


def test_leetcode_missing_user_and_blocked_are_different_answers():
    gone = Net(**{"graphql": (200, json.dumps({"data": {"matchedUser": None}}))})
    with pytest.raises(cp.ProfileError) as e:
        cp.fetch_leetcode("nobody", gone, lambda s: None, NOW)
    assert e.value.code == "not_found"
    blocked = Net(**{"graphql": (403, "<html>Just a moment...</html>")})
    with pytest.raises(cp.ProfileError) as e:
        cp.fetch_leetcode("asha", blocked, lambda s: None, NOW)
    assert e.value.code == "unavailable"


def test_codechef_reads_what_it_can_and_admits_when_the_page_changed():
    r = cp.fetch_codechef("asha", Net(), lambda s: None, NOW)
    assert r["status"] == "ok"
    assert (r["stats"]["rating"], r["stats"]["max_rating"], r["stats"]["stars"], r["stats"]["solved_total"], r["stats"]["contests"]) == (1623, 1701, 3, 87, 14)
    changed = Net(**{"codechef.com": (200, "<html><body>Totally new layout</body></html>")})
    with pytest.raises(cp.ProfileError) as e:
        cp.fetch_codechef("asha", changed, lambda s: None, NOW)
    assert e.value.code == "unavailable" and "enter_manually" in e.value.offer
    partial = Net(**{"codechef.com": (200, '<div class="rating-number">1500</div>')})
    assert cp.fetch_codechef("asha", partial, lambda s: None, NOW)["status"] == "partial"


# ---- the book ---------------------------------------------------------------------------------------------------

def test_one_platform_failing_leaves_the_others_intact():
    b = book(Net(**{"graphql": (403, "blocked")}))
    b.link("c", {"codeforces": "asha", "leetcode": "asha", "codechef": "asha"})
    v = b.refresh("c")
    by = {p["platform"]: p for p in v["profiles"]}
    assert by["codeforces"]["status"] == "ok" and by["codechef"]["status"] == "ok"
    assert by["leetcode"]["status"] == "unavailable" and by["leetcode"]["error"]["offer"]
    assert v["summary"]["readable"] == 2 and v["score_effect"] == 0


def test_a_parser_crash_becomes_unavailable_not_a_500():
    b = book(Net(**{"graphql": (200, json.dumps({"data": {"matchedUser": {"submitStatsGlobal": 5}}}))}))
    b.link("c", {"leetcode": "asha"})
    assert b.refresh("c")["profiles"][0]["status"] == "unavailable"


def test_fresh_reads_are_reused_and_force_reads_again():
    net = Net()
    b = book(net)
    b.link("c", {"leetcode": "asha"})
    b.refresh("c"); b.refresh("c")
    assert sum("graphql" in u for _, u in net.calls) == 1
    b.refresh("c", force=True)
    assert sum("graphql" in u for _, u in net.calls) == 2


def test_stale_data_survives_a_later_failure_and_says_so():
    state = {"fail": False}
    net = Net(**{"graphql": lambda u, body: (403, "x") if state["fail"] else (200, json.dumps(LC_OK))})
    b = book(net)
    b.link("c", {"leetcode": "asha"})
    b.refresh("c")
    state["fail"] = True
    row = b.refresh("c", force=True)["profiles"][0]
    assert row["stats"]["solved_total"] == 210 and row["stale"] is True and row["error"]["code"] == "unavailable"


def test_ownership_is_proved_by_the_issued_code_and_is_per_platform():
    net = Net()
    b = book(net)
    v = b.link("c", {"codeforces": "asha", "codechef": "asha"})
    codes = {p["platform"]: p["verification"]["code"] for p in v["profiles"]}
    assert all(c.startswith("gha-") for c in codes.values()) and all(not p["verified"] for p in v["profiles"])
    net.bio = codes["codeforces"]                       # the student pastes the Codeforces code, and only that one
    by = {p["platform"]: p for p in b.verify("c")["profiles"]}
    assert by["codeforces"]["verified"] and "verification" not in by["codeforces"]
    assert not by["codechef"]["verified"]


def test_changing_a_handle_drops_old_data_and_verification():
    net = Net()
    b = book(net)
    b.link("c", {"leetcode": "asha"})
    code = b.view("c")["profiles"][0]["verification"]["code"]
    b.refresh("c")
    again = b.link("c", {"leetcode": "asha"})["profiles"][0]          # same handle: kept
    assert again["stats"] is not None and again["verification"]["code"] == code
    changed = b.link("c", {"leetcode": "someone_else"})["profiles"][0]
    assert changed["stats"] is None and changed["verification"]["code"] != code and not changed["verified"]


def test_candidates_cannot_see_each_other_and_delete_clears():
    b = book()
    b.link("a", {"leetcode": "asha"})
    assert b.view("b")["profiles"] == []
    b.delete("a")
    assert b.view("a")["profiles"] == []


# ---- questions and the module's rules ---------------------------------------------------------------------------

def test_questions_cite_the_number_they_came_from_and_never_score():
    b = book()
    b.link("c", {"codeforces": "asha", "leetcode": "asha", "codechef": "asha"})
    b.refresh("c")
    qs = b.questions("c")
    assert {q["platform"] for q in qs} == set(cp.PLATFORMS)
    for q in qs:
        assert q["score_effect"] == 0 and q["fallback_if_unanswered"] and q["evidence"] and q["question"].endswith(("?", "."))
    assert any("20 Hard" in q["question"] for q in qs)
    assert guard.problems({"questions": qs}) == [] and guard.problems(b.view("c")) == []


def test_no_data_means_no_questions_rather_than_made_up_ones():
    b = book(Net(**{"graphql": (403, "x")}))
    b.link("c", {"leetcode": "asha"})
    b.refresh("c")
    assert b.questions("c") == []


# ---- routes -----------------------------------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    b = book()
    svc = AnalyzerService(tmp_path / "store", sync=True)
    return TestClient(create_app(svc, profiles=b)), b


def test_routes_need_a_candidate(client):
    c, _ = client
    for method, path in [("get", "/v1/coding-profiles"), ("post", "/v1/coding-profiles/refresh"), ("get", "/v1/coding-profiles/questions")]:
        assert getattr(c, method)(path).status_code == 401


def test_link_refresh_questions_delete_over_http(client):
    c, _ = client
    r = c.put("/v1/coding-profiles", json={"leetcode": "https://leetcode.com/u/asha/", "codeforces": "asha"}, headers=H)
    assert r.status_code == 200 and {p["handle"] for p in r.json()["profiles"]} == {"asha"}
    assert all(p["status"] == "pending" for p in r.json()["profiles"])
    r = c.post("/v1/coding-profiles/refresh", headers=H)
    assert r.status_code == 200 and r.json()["summary"]["readable"] == 2
    q = c.get("/v1/coding-profiles/questions", headers=H).json()["questions"]
    assert q and all(x["score_effect"] == 0 for x in q)
    assert c.get("/v1/coding-profiles", headers={"X-Candidate-Id": "other"}).json()["profiles"] == []
    assert c.delete("/v1/coding-profiles", headers=H).status_code == 204
    assert c.get("/v1/coding-profiles", headers=H).json()["profiles"] == []


def test_a_bad_handle_is_a_named_400_not_a_stack_trace(client):
    c, _ = client
    r = c.put("/v1/coding-profiles", json={"codeforces": "not a handle!"}, headers=H)
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_handle" and r.json()["error"]["offer"]


def test_a_handle_that_does_not_exist_is_not_offered_for_verification_and_the_name_is_spelled_properly():
    b = book(Net(**{"graphql": (200, json.dumps({"data": {"matchedUser": None}}))}))
    b.link("c", {"leetcode": "nobody"})
    row = b.refresh("c")["profiles"][0]
    assert row["status"] == "not_found" and "verification" not in row
    with pytest.raises(cp.ProfileError) as e:
        cp.normalise_handle("leetcode", "not a handle!")
    assert "LeetCode username" in str(e.value)


# ---- pulling the statistics on their own ------------------------------------------------------------------------

def test_stats_rows_are_flat_one_per_platform_with_lists_joined():
    b = book()
    b.link("c", {"codeforces": "asha", "leetcode": "asha"})
    rows = cp.stats_rows(b.refresh("c"))
    by = {r["platform"]: r for r in rows}
    assert by["codeforces"]["solved_total"] == 3 and by["codeforces"]["top_tags"].startswith("greedy:2|")
    assert by["leetcode"]["solved_hard"] == 20 and by["leetcode"]["badges"] == "100 Days Badge"
    assert all(isinstance(v, (str, int, float, bool, type(None))) for r in rows for v in r.values())


def test_the_command_writes_json_and_csv_and_needs_a_handle(tmp_path, monkeypatch, capsys):
    import csv
    monkeypatch.setattr(cp, "default_transport", Net())       # LeetCode needs no waits, so the real sleep is never reached
    out_j, out_c = tmp_path / "s.json", tmp_path / "s.csv"
    assert cp.main(["--leetcode", "https://leetcode.com/u/asha/", "--json", str(out_j), "--csv", str(out_c)]) == 0
    assert json.loads(out_j.read_text())["summary"]["readable"] == 1
    row = list(csv.DictReader(out_c.open()))[0]
    assert row["handle"] == "asha" and row["solved_total"] == "210" and row["solved_hard"] == "20" and row["verified"] == "False"
    shown = capsys.readouterr().out
    assert "LeetCode · asha" in shown and "solved_hard" in shown and "20" in shown
    with pytest.raises(SystemExit):
        cp.main([])
    assert cp.main(["--leetcode", "not a handle!"]) == 2


# ---- insights ---------------------------------------------------------------------------------------------------

def read(net=None, **handles):
    b = book(net)
    b.link("c", handles or {"codeforces": "asha", "leetcode": "asha", "codechef": "asha"})
    b.refresh("c")
    return b


def test_insights_are_descriptive_cited_and_pass_the_guard():
    b = read()
    ins = b.insights("c")
    assert {i["platform"] for i in ins} == {"codeforces", "leetcode", "codechef", "all"}
    for i in ins:
        assert i["kind"] in ("strength", "pattern", "worth_a_look") and i["headline"] and i["body"] and i["evidence"] and i["score_effect"] == 0
    assert guard.problems({"insights": ins}) == []


def test_leetcode_insights_name_the_real_numbers_and_the_thin_topics():
    ins = {i["id"]: i for i in read(leetcode="asha").insights("c")}
    assert ins["leetcode-insight-1"]["headline"].startswith("Easy 48% · Medium 43% · Hard 10%")
    assert ins["leetcode-insight-1"]["kind"] == "strength"                       # 20 hard of 210 is not "few"
    assert "top 18.4%" in ins["leetcode-insight-2"]["body"]
    assert ins["leetcode-insight-3"]["headline"] == "Python3 for 90% of your solves"
    assert ins["leetcode-insight-4"]["evidence"]["top_topics"][0] == {"name": "Array", "solved": 80}


def test_a_thin_core_topic_is_flagged_only_when_the_site_data_shows_it():
    net = Net(**{"graphql": (200, json.dumps({"data": {**LC_OK["data"], "matchedUser": {**LC_OK["data"]["matchedUser"], "tagProblemCounts": {
        "fundamental": [{"tagName": "Array", "problemsSolved": 90}], "intermediate": [{"tagName": "Tree", "problemsSolved": 30}, {"tagName": "Greedy", "problemsSolved": 20}],
        "advanced": [{"tagName": "Dynamic Programming", "problemsSolved": 15}, {"tagName": "Binary Search", "problemsSolved": 12}, {"tagName": "Graph", "problemsSolved": 1}]}}}}))})
    ins = {i["id"]: i for i in read(net, leetcode="asha").insights("c")}
    assert ins["leetcode-insight-5"]["evidence"]["topics"] == [{"name": "graphs", "solved": 1}]


def test_a_long_quiet_spell_is_a_pointer_not_a_mark():
    net = Net()
    b = cp.CodingProfiles(net, sleep=lambda s: None, clock=lambda: NOW + 90 * 86400)       # three months after the last activity
    b.link("c", {"leetcode": "asha"})
    b.refresh("c")
    quiet = [i for i in b.insights("c") if i["id"] == "leetcode-insight-7"][0]
    assert quiet["kind"] == "worth_a_look" and quiet["headline"].startswith("Quiet for") and quiet["score_effect"] == 0


def test_codeforces_insights_use_rating_bands_and_the_recent_trend():
    ins = {i["id"]: i for i in read(codeforces="asha").insights("c")}
    assert ins["codeforces-insight-3"]["evidence"]["hardest_solved_rating"] == 1900
    assert "rated 800" in ins["codeforces-insight-3"]["headline"] or "under 1200" in ins["codeforces-insight-3"]["headline"]


def test_one_site_gives_no_across_sites_insight_and_no_data_gives_none():
    assert not any(i["platform"] == "all" for i in read(leetcode="asha").insights("c"))
    b = book(Net(**{"graphql": (403, "x")}))
    b.link("c", {"leetcode": "asha"}); b.refresh("c")
    assert b.insights("c") == []


def test_insights_route_and_the_command_print_them(client, monkeypatch, capsys):
    c, _ = client
    c.put("/v1/coding-profiles", json={"leetcode": "asha"}, headers=H)
    r = c.get("/v1/coding-profiles/insights", headers=H)
    assert r.status_code == 200 and r.json()["insights"] and c.get("/v1/coding-profiles/insights").status_code == 401
    monkeypatch.setattr(cp, "default_transport", Net())
    assert cp.main(["--leetcode", "asha"]) == 0
    assert "Insights" in capsys.readouterr().out


def test_a_topic_named_like_a_forbidden_key_never_becomes_a_key_in_anything_sent():
    """Codeforces really has a tag 'dfs and similar'. Names are values; the API guard must pass on a full payload."""
    b = read(codeforces="asha", leetcode="asha")
    payload = {"view": b.view("c"), "insights": b.insights("c"), "questions": b.questions("c")}
    assert "dfs and similar" in json.dumps(payload) and guard.problems(payload) == []


# ---- the series the charts draw ----------------------------------------------------------------------------------

def test_codeforces_series_count_submissions_per_day_and_the_day_a_problem_was_first_solved():
    s = cp.fetch_codeforces("asha", Net(), lambda s: None, NOW)["stats"]
    day = cp._day(int(NOW) - 5 * 86400)
    assert {"date": day, "submissions": 4, "solved": 2} in s["daily"]          # A and B first solved that day; the repeat of B adds no solve
    assert all(d["date"] >= cp._day(NOW - 365 * 86400) for d in s["daily"])    # the 400-day-old one is outside the window
    assert s["rating_history"] == [{"date": cp._day(int(NOW) - 40 * 86400), "rating": 1450, "contest": "Round 1"}]


def test_leetcode_series_come_from_the_calendar_and_the_contest_history():
    net = Net(**{"graphql": (200, json.dumps({"data": {**LC_OK["data"], "userContestRankingHistory": [
        {"attended": True, "rating": 1500.4, "ranking": 900, "contest": {"title": "Weekly 1", "startTime": 1798000000}},
        {"attended": False, "rating": 0, "ranking": 0, "contest": {"title": "Weekly 2", "startTime": 1798600000}}]}}))})
    s = cp.fetch_leetcode("asha", net, lambda s: None, NOW)["stats"]
    assert [d["submissions"] for d in s["daily"]] == [3, 1] and s["daily"][0]["date"] < s["daily"][1]["date"]
    assert s["rating_history"] == [{"date": cp._day(1798000000), "rating": 1500, "contest": "Weekly 1"}]


def test_series_are_in_the_view_and_the_json_but_not_the_one_row_csv_and_pass_the_guard():
    b = read(codeforces="asha", leetcode="asha")
    view = b.view("c")
    assert all(p["stats"]["daily"] for p in view["profiles"]) and guard.problems(view) == []
    assert not any(k in r for r in cp.stats_rows(view) for k in cp.SERIES)
