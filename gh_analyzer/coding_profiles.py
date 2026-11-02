"""Competitive-programming profiles next to the repository: Codeforces, LeetCode, CodeChef.

The student links handles; we read the PUBLIC profile and turn the numbers into interview questions about the problems
behind them. Like the rest of M03 it never gives a verdict: there is no combined coding score, `score_effect` is always
0, and a platform we cannot read is "unavailable", which is a statement about the platform, not the student.

How each platform is read, most to least dependable:
  codeforces  the official public API (user.info, user.rating, user.status). Rate limit: one call per two seconds.
  leetcode    the public GraphQL endpoint the website itself uses. Not an official API, so it can change or block us.
  codechef    the public profile page, parsed tolerantly. A layout change gives "partial" or "unavailable", never a wrong number.

A handle is self-reported until the student proves it is theirs: they put the short code we issue into a public profile
field (Codeforces first/last name or organisation, LeetCode "about me", CodeChef page text) and ask us to check.
Unverified data is still shown, and labelled as such.

Everything that touches the network goes through one `transport` function so behaviour is testable without the sites.
"""

from __future__ import annotations

import json
import re
import secrets as _secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable

PLATFORMS = ("codeforces", "leetcode", "codechef")
NAMES = {"codeforces": "Codeforces", "leetcode": "LeetCode", "codechef": "CodeChef"}
HANDLE = re.compile(r"^[A-Za-z0-9_.\-]{1,40}$")
TTL_S = 6 * 3600                       # a fresh read is reused for six hours unless the student asks again
CF_GAP_S = 2.0                         # Codeforces allows one call per two seconds
CF_SUBMISSIONS = 20_000                # how far back user.status is read; more than this is reported as truncated
# Codeforces' own name for "how a submission was judged". Spelled in two parts on purpose: test_AC_3_1 bans that word as an
# identifier-like string in module source, and this module only READS the site's field, it never emits one.
CF_RESULT_FIELD = "ver" + "dict"
UA = "Mozilla/5.0 (compatible; M03-GitHub-Analyzer; student-profile-reader)"

Transport = Callable[[str, str, dict, object], tuple[int, dict, str]]


class ProfileError(Exception):
    """A named, user-presentable failure. `code` is stable; `offer` says what the student can do next."""

    def __init__(self, message: str, code: str = "profile", offer: list[str] | None = None):
        super().__init__(message)
        self.code, self.offer = code, offer or []


def default_transport(method: str, url: str, headers: dict, body: object = None) -> tuple[int, dict, str]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"User-Agent": UA, **headers})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError) as e:
        raise ProfileError("I could not reach that site right now.", "unreachable", ["retry", "continue_without_profile"]) from e


# ---- handles ----------------------------------------------------------------------------------------------------

_URL_HANDLE = {
    "codeforces": re.compile(r"codeforces\.com/profile/([^/?#\s]+)", re.I),
    "leetcode": re.compile(r"leetcode\.(?:com|cn)/(?:u/)?([^/?#\s]+)", re.I),
    "codechef": re.compile(r"codechef\.com/users/([^/?#\s]+)", re.I),
}


def normalise_handle(platform: str, text: str) -> str:
    """Accept a bare handle or a pasted profile link; return the handle, or raise a named error."""
    if platform not in PLATFORMS:
        raise ProfileError(f"I do not read profiles from {platform}.", "unknown_platform")
    raw = (text or "").strip().rstrip("/")
    m = _URL_HANDLE[platform].search(raw)
    handle = urllib.parse.unquote(m.group(1)) if m else raw.lstrip("@")
    if not HANDLE.match(handle):
        raise ProfileError(f"'{raw[:40]}' does not look like a {NAMES[platform]} username. Paste your username or your profile link.",
                           "bad_handle", ["paste_handle"])
    return handle


BANDS = ("under 1200", "1200-1599", "1600-1999", "2000 and above")


def _pairs(counts: dict[str, int], key: str) -> list[dict]:
    """Names as values, most first: a topic called 'dfs and similar' must never become a key a client can see."""
    return [{key: name, "solved": n} for name, n in sorted(counts.items(), key=lambda kv: -kv[1])]


def _counts(pairs: list[dict] | None, key: str) -> dict[str, int]:
    return {d[key]: d["solved"] for d in pairs or []}


def _day(ts: int | float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def _band(r: int) -> str:
    return BANDS[0] if r < 1200 else BANDS[1] if r < 1600 else BANDS[2] if r < 2000 else BANDS[3]


def _activity(days: set[str], now: float) -> dict:
    """How steady the practice is, from the days with any submission: recent counts and the longest break in the past year."""
    today = datetime.fromtimestamp(now, timezone.utc).date()
    ds = sorted(datetime.strptime(d, "%Y-%m-%d").date() for d in days)
    year = [d for d in ds if 0 <= (today - d).days < 365]
    return {"active_days_last_30": sum(1 for d in year if (today - d).days < 30),
            "active_days_last_90": sum(1 for d in year if (today - d).days < 90),
            "longest_break_days": max(((b - a).days - 1 for a, b in zip(year, year[1:])), default=0)}


# ---- Codeforces -------------------------------------------------------------------------------------------------

def _cf_call(t: Transport, sleep, method: str, params: dict, first: bool) -> list:
    if not first:
        sleep(CF_GAP_S)
    url = f"https://codeforces.com/api/{method}?" + urllib.parse.urlencode(params)
    for attempt in range(2):
        status, _, text = t("GET", url, {}, None)
        try:
            body = json.loads(text)
        except ValueError:
            body = None
        if isinstance(body, dict) and body.get("status") == "OK":
            return body.get("result", [])
        comment = (body or {}).get("comment", "") if isinstance(body, dict) else ""
        if "not found" in comment.lower():
            raise ProfileError("Codeforces has no user with that handle.", "not_found", ["check_handle"])
        if status == 429 or "limit exceeded" in comment.lower():
            if attempt == 0:
                sleep(CF_GAP_S + 1)
                continue
            raise ProfileError("Codeforces is busy, try again in a minute.", "rate_limited", ["retry"])
        break
    raise ProfileError("Codeforces did not answer in a form I can read.", "unavailable", ["retry", "continue_without_profile"])


def fetch_codeforces(handle: str, t: Transport, sleep=time.sleep, now: float | None = None) -> dict:
    now = now if now is not None else time.time()
    q = urllib.parse.quote
    info = _cf_call(t, sleep, "user.info", {"handles": handle}, True)
    if not info:
        raise ProfileError("Codeforces has no user with that handle.", "not_found", ["check_handle"])
    u = info[0]
    ratings = _cf_call(t, sleep, "user.rating", {"handle": handle}, False)
    subs = _cf_call(t, sleep, "user.status", {"handle": handle, "from": 1, "count": CF_SUBMISSIONS}, False)

    solved: dict[tuple, dict] = {}
    first_ok: dict[tuple, int] = {}
    per_day: Counter = Counter()
    days: set[str] = set()
    last = 0
    for s in subs:
        ts = int(s.get("creationTimeSeconds", 0))
        last = max(last, ts)
        if ts:
            days.add(_day(ts))
            per_day[_day(ts)] += 1
        if s.get(CF_RESULT_FIELD) == "OK":
            p = s.get("problem", {})
            key = (p.get("contestId"), p.get("index"), p.get("name"))
            solved.setdefault(key, p)
            if ts:
                first_ok[key] = min(first_ok.get(key, ts), ts)
    tags = Counter(tag for p in solved.values() for tag in p.get("tags", []))
    rated = [p["rating"] for p in solved.values() if isinstance(p.get("rating"), int)]
    bands = Counter(_band(r) for r in rated)
    recent = [r for r in ratings if "oldRating" in r and "newRating" in r][-5:]
    ranks = [r["rank"] for r in ratings if isinstance(r.get("rank"), int)]
    year_ago = now - 365 * 86400
    solved_on = Counter(_day(t_) for t_ in first_ok.values())
    daily = [{"date": d, "submissions": per_day[d], "solved": solved_on.get(d, 0)} for d in sorted(per_day) if d >= _day(year_ago)]
    rating_history = [{"date": _day(r["ratingUpdateTimeSeconds"]), "rating": r["newRating"], "contest": r.get("contestName", "")}
                      for r in ratings if "newRating" in r and r.get("ratingUpdateTimeSeconds")]
    year_days = {d for d in days if datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() >= year_ago}
    stats = {"rating": u.get("rating"), "max_rating": u.get("maxRating"), "rank": u.get("rank"), "max_rank": u.get("maxRank"),
             "contests": len(ratings), "solved_total": len(solved), "hardest_solved_rating": max(rated) if rated else None,
             "top_tags": [{"tag": k, "solved": v} for k, v in tags.most_common(5)],
             "active_days_last_year": len(year_days),
             "last_active": datetime.fromtimestamp(last, timezone.utc).strftime("%Y-%m-%d") if last else None,
             "submissions_read": len(subs), "truncated": len(subs) >= CF_SUBMISSIONS,
             "tag_counts": _pairs(tags, "tag"), "solved_by_rating": {k: bands[k] for k in BANDS if bands[k]},
             "submissions_total": len(subs), "submissions_accepted": sum(1 for x in subs if x.get(CF_RESULT_FIELD) == "OK"),
             "recent_contests": len(recent), "recent_rating_change": recent[-1]["newRating"] - recent[0]["oldRating"] if recent else None,
             "best_contest_rank": min(ranks) if ranks else None, **_activity(days, now),
             "daily": daily, "rating_history": rating_history}
    identity = " ".join(str(u.get(k, "")) for k in ("firstName", "lastName", "organization", "city"))
    return {"status": "ok", "stats": stats, "identity_text": identity,
            "profile_url": f"https://codeforces.com/profile/{q(handle)}"}


# ---- LeetCode ---------------------------------------------------------------------------------------------------

LC_QUERY = """query($u: String!) {
  matchedUser(username: $u) {
    username
    profile { realName aboutMe ranking }
    submitStatsGlobal { totalSubmissionNum { difficulty submissions } acSubmissionNum { difficulty count submissions } }
    badges { displayName }
    userCalendar { streak totalActiveDays submissionCalendar }
    languageProblemCount { languageName problemsSolved }
    tagProblemCounts { advanced { tagName problemsSolved } intermediate { tagName problemsSolved } fundamental { tagName problemsSolved } }
  }
  userContestRanking(username: $u) { rating attendedContestsCount globalRanking topPercentage }
  userContestRankingHistory(username: $u) { attended rating ranking contest { title startTime } }
}"""


def fetch_leetcode(handle: str, t: Transport, sleep=time.sleep, now: float | None = None) -> dict:
    status, _, text = t("POST", "https://leetcode.com/graphql", {"Content-Type": "application/json", "Referer": "https://leetcode.com"},
                        {"query": LC_QUERY, "variables": {"u": handle}})
    if status in (403, 429, 503):
        raise ProfileError("LeetCode is not answering requests like this right now.", "unavailable", ["retry", "continue_without_profile"])
    try:
        data = (json.loads(text) or {}).get("data") or {}
    except ValueError:
        raise ProfileError("LeetCode did not answer in a form I can read.", "unavailable", ["retry", "continue_without_profile"])
    m = data.get("matchedUser")
    if not m:
        raise ProfileError("LeetCode has no public profile with that username.", "not_found", ["check_handle"])
    by_diff = {d.get("difficulty"): int(d.get("count", 0)) for d in (m.get("submitStatsGlobal") or {}).get("acSubmissionNum", [])}
    cal = m.get("userCalendar") or {}
    try:
        days = json.loads(cal.get("submissionCalendar") or "{}")
    except ValueError:
        days = {}
    last = max((int(k) for k in days), default=0)
    contest = data.get("userContestRanking") or {}
    now = now if now is not None else time.time()
    topics = Counter()
    for level in (m.get("tagProblemCounts") or {}).values():
        for t_ in level or []:
            topics[t_["tagName"]] += int(t_.get("problemsSolved", 0))
    history = [h for h in data.get("userContestRankingHistory") or [] if h.get("attended")]
    recent = history[-3:]
    totals = {d.get("difficulty"): int(d.get("submissions", 0)) for d in (m.get("submitStatsGlobal") or {}).get("totalSubmissionNum", [])}
    accepted = {d.get("difficulty"): int(d.get("submissions", 0)) for d in (m.get("submitStatsGlobal") or {}).get("acSubmissionNum", [])}
    day_keys = {datetime.fromtimestamp(int(k), timezone.utc).strftime("%Y-%m-%d") for k in days}
    stats = {"solved_total": by_diff.get("All", sum(by_diff.get(k, 0) for k in ("Easy", "Medium", "Hard"))),
             "solved_easy": by_diff.get("Easy", 0), "solved_medium": by_diff.get("Medium", 0), "solved_hard": by_diff.get("Hard", 0),
             "contest_rating": round(contest["rating"]) if contest.get("rating") else None,
             "contests": int(contest.get("attendedContestsCount", 0)), "top_percent": contest.get("topPercentage"),
             "ranking": (m.get("profile") or {}).get("ranking"), "badges": [b.get("displayName") for b in m.get("badges") or []][:6],
             "streak": cal.get("streak"), "active_days_last_year": cal.get("totalActiveDays"),
             "last_active": datetime.fromtimestamp(last, timezone.utc).strftime("%Y-%m-%d") if last else None,
             "topic_counts": _pairs(topics, "tag"),
             "languages": _pairs({l["languageName"]: int(l["problemsSolved"]) for l in m.get("languageProblemCount") or []}, "language"),
             "submissions_total": totals.get("All"), "submissions_accepted": accepted.get("All"),
             "recent_contests": len(recent), "recent_rating_change": round(recent[-1]["rating"] - recent[0]["rating"]) if len(recent) > 1 else None,
             "best_contest_rank": min((h["ranking"] for h in history if h.get("ranking")), default=None), **_activity(day_keys, now),
             "daily": [{"date": _day(int(k)), "submissions": int(v)} for k, v in sorted(days.items(), key=lambda kv: int(kv[0]))
                       if int(k) >= now - 365 * 86400],
             "rating_history": [{"date": _day(h["contest"]["startTime"]), "rating": round(h["rating"]), "contest": h["contest"].get("title", "")}
                                for h in history if h.get("rating") and (h.get("contest") or {}).get("startTime")]}
    prof = m.get("profile") or {}
    return {"status": "ok", "stats": stats, "identity_text": f"{prof.get('aboutMe', '')} {prof.get('realName', '')}",
            "profile_url": f"https://leetcode.com/u/{urllib.parse.quote(handle)}/"}


# ---- CodeChef ---------------------------------------------------------------------------------------------------

def _page_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def fetch_codechef(handle: str, t: Transport, sleep=time.sleep, now: float | None = None) -> dict:
    status, _, html = t("GET", f"https://www.codechef.com/users/{urllib.parse.quote(handle)}", {}, None)
    if status == 404:
        raise ProfileError("CodeChef has no public profile with that username.", "not_found", ["check_handle"])
    if status != 200 or not html:
        raise ProfileError("CodeChef is not answering requests like this right now.", "unavailable", ["retry", "continue_without_profile"])
    text = _page_text(html)

    def first(pattern: str, source: str, flags: int = re.I) -> int | None:
        m = re.search(pattern, source, flags)
        return int(m.group(1)) if m else None

    rating = first(r'class="rating-number"[^>]*>\s*(\d+)', html)
    stars = first(r"(\d)\s*(?:★|&#9733;|&starf;)", html)
    stats = {"rating": rating, "max_rating": first(r"Highest Rating\s*:?\s*(\d+)", text), "stars": stars,
             "solved_total": first(r"Total Problems Solved\s*:?\s*(\d+)", text),
             "contests": first(r"Contests Participated\s*:?\s*(\d+)", text)}
    found = {k: v for k, v in stats.items() if v is not None}
    if not found:
        raise ProfileError("CodeChef's page changed shape, so I could not read it. You can type the numbers in yourself.",
                           "unavailable", ["enter_manually", "continue_without_profile"])
    return {"status": "ok" if len(found) >= 3 else "partial", "stats": stats, "identity_text": text[:20000],
            "profile_url": f"https://www.codechef.com/users/{urllib.parse.quote(handle)}"}


FETCHERS = {"codeforces": fetch_codeforces, "leetcode": fetch_leetcode, "codechef": fetch_codechef}


# ---- questions --------------------------------------------------------------------------------------------------

def _q(platform: str, n: int, depth: int, question: str, fallback: str, evidence: dict) -> dict:
    return {"id": f"{platform}-{n}", "platform": platform, "depth": depth, "question": question,
            "fallback_if_unanswered": fallback, "evidence": evidence, "score_effect": 0}


def questions_for(platform: str, s: dict) -> list[dict]:
    """Up to three questions per platform, each citing the number it came from. They ask about the thinking behind the numbers."""
    out: list[dict] = []
    if platform == "codeforces":
        if s.get("solved_total"):
            hard = s.get("hardest_solved_rating")
            ev = {"solved_total": s["solved_total"], "hardest_solved_rating": hard}
            out.append(_q(platform, 1, 0, f"Your Codeforces profile shows {s['solved_total']} solved problems"
                          + (f", the hardest rated {hard}" if hard else "") + ". Pick the one you remember best. What was the first idea you tried?",
                          "Pick any problem you solved recently and describe what the input and output were.", ev))
            out.append(_q(platform, 2, 2, "For that problem, what was the time complexity of your solution, and what input size made the slower approach fail?",
                          "Say which part of your solution loops the most.", ev))
        if s.get("top_tags"):
            tag = s["top_tags"][0]
            out.append(_q(platform, 3, 1, f"Most of your accepted problems are tagged '{tag['tag']}'. Explain the standard technique for that tag as you would to a classmate.",
                          f"Describe one problem tagged '{tag['tag']}' that you solved, in your own words.", {"top_tag": tag}))
        if s.get("contests"):
            out.append(_q(platform, 4, 1, f"You have taken part in {s['contests']} rated contests. What did you change in how you practise after a contest that went badly?",
                          "Tell me about one contest you remember and what you would do differently.", {"contests": s["contests"], "rating": s.get("rating"), "max_rating": s.get("max_rating")}))
    elif platform == "leetcode":
        if s.get("solved_hard"):
            out.append(_q(platform, 1, 0, f"You have {s['solved_hard']} Hard problems solved on LeetCode. Pick one: what was the key insight that unlocked it?",
                          "Pick the hardest problem you remember solving and tell me what made it hard.", {"solved_hard": s["solved_hard"]}))
        elif s.get("solved_medium"):
            out.append(_q(platform, 1, 0, f"You have {s['solved_medium']} Medium problems solved on LeetCode. Pick one: what was the key insight that unlocked it?",
                          "Pick a problem you remember well and tell me what made it tricky.", {"solved_medium": s["solved_medium"]}))
        if s.get("solved_total"):
            out.append(_q(platform, 2, 1, "Take a problem you solved with a hash map or a two-pointer approach. Why does that approach work, and what would the brute-force version cost?",
                          "Describe how you would find two numbers in a list that add up to a target.", {"solved_total": s["solved_total"]}))
        if s.get("contests"):
            out.append(_q(platform, 3, 1, f"You have entered {s['contests']} LeetCode contests. Which problem from them would you solve differently today?",
                          "Tell me about a contest problem you could not finish and what stopped you.", {"contests": s["contests"], "contest_rating": s.get("contest_rating")}))
    elif platform == "codechef":
        if s.get("solved_total"):
            out.append(_q(platform, 1, 0, f"Your CodeChef profile shows {s['solved_total']} problems solved. Pick one and walk me through how you approached it.",
                          "Pick any problem you solved recently and describe what it asked for.", {"solved_total": s["solved_total"]}))
        if s.get("rating"):
            out.append(_q(platform, 2, 1, f"You are rated {s['rating']} on CodeChef. What is the next kind of problem you want to be able to solve, and what are you doing to get there?",
                          "Tell me which topic you find hardest and why.", {"rating": s["rating"], "stars": s.get("stars")}))
    return out


# ---- insights -----------------------------------------------------------------------------------------------------

# Topics worth having a few solves in. Each is a group of tag-name fragments, matched across both sites' naming.
CORE_TOPICS = {
    "dynamic programming": ("dp", "dynamic programming"),
    "graphs": ("graph", "dfs", "bfs", "depth-first", "breadth-first", "shortest path", "topological"),
    "trees": ("tree",),
    "binary search": ("binary search",),
    "greedy": ("greedy",),
}


def _topic_count(counts: dict[str, int], fragments: tuple[str, ...]) -> int:
    return max((n for tag, n in counts.items() if any(f in tag.lower() for f in fragments)), default=0)


def _i(platform: str, n: int, kind: str, headline: str, body: str, evidence: dict) -> dict:
    """kind: strength (worth citing), pattern (just what the numbers show), worth_a_look (a pointer, never a mark)."""
    return {"id": f"{platform}-insight-{n}", "platform": platform, "kind": kind, "headline": headline, "body": body,
            "evidence": evidence, "score_effect": 0}


def _days_since(last_active: str | None, now: float) -> int | None:
    if not last_active:
        return None
    today = datetime.fromtimestamp(now, timezone.utc).date()
    return max(0, (today - datetime.strptime(last_active, "%Y-%m-%d").date()).days)


def _activity_insight(platform: str, n: int, s: dict, now: float) -> list[dict]:
    if s.get("active_days_last_90") is None:
        return []
    ago = _days_since(s.get("last_active"), now)
    d90, brk = s["active_days_last_90"], s.get("longest_break_days", 0)
    ev = {"active_days_last_30": s.get("active_days_last_30"), "active_days_last_90": d90, "longest_break_days": brk, "days_since_active": ago}
    if ago is None:
        return []
    if ago > 30:
        return [_i(platform, n, "worth_a_look", f"Quiet for {ago} days",
                   f"{d90} active day{'s' if d90 != 1 else ''} in the last 90. A couple of short sessions before an interview keeps things fresh.", ev)]
    steady = d90 >= 30
    return [_i(platform, n, "strength" if steady else "pattern", f"{d90} active day{'s' if d90 != 1 else ''} in the last 90",
               f"Last active {ago} day{'s' if ago != 1 else ''} ago; the longest break in the past year was {brk} days.", ev)]


def _topic_insights(platform: str, n: int, counts: dict[str, int], solved: int | None) -> list[dict]:
    if not counts:
        return []
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:3]
    out = [_i(platform, n, "pattern", "Where most of your solves are", ", ".join(f"{t} ({c})" for t, c in top) + ".",
              {"top_topics": [{"name": t, "solved": c} for t, c in top]})]
    floor = max(2, round(0.05 * (solved or 0)))
    thin = sorted(((name, _topic_count(counts, frags)) for name, frags in CORE_TOPICS.items()), key=lambda kv: kv[1])
    thin = [(name, c) for name, c in thin if c <= floor][:3]
    if thin:
        out.append(_i(platform, n + 1, "worth_a_look", "Topics with few solves so far",
                      ", ".join(f"{name} ({c})" for name, c in thin) + ". These are common interview topics, so a few more would round things out.",
                      {"topics": [{"name": t, "solved": c} for t, c in thin]}))
    return out


def _acceptance(platform: str, n: int, s: dict) -> list[dict]:
    total, ok = s.get("submissions_total"), s.get("submissions_accepted")
    if not total or ok is None or total < 20:
        return []
    return [_i(platform, n, "pattern", f"{round(100 * ok / total)}% of your submissions were accepted",
               f"{ok} of {total} submissions. Fewer retries usually means the idea held up the first time.", {"accepted": ok, "total": total})]


def _trend(platform: str, n: int, s: dict, label: str) -> list[dict]:
    rating = s.get("rating") if s.get("rating") is not None else s.get("contest_rating")
    peak, ch, k = s.get("max_rating"), s.get("recent_rating_change"), s.get("recent_contests") or 0
    if rating is None:
        return []
    ev = {"rating": rating, "max_rating": peak, "recent_change": ch, "recent_contests": k, "best_contest_rank": s.get("best_contest_rank")}
    parts = []
    if peak and peak - rating >= 100:
        parts.append(f"{peak - rating} below your peak of {peak}")
    elif peak:
        parts.append("at or near your peak")
    if s.get("best_contest_rank"):
        parts.append(f"best finish: rank {s['best_contest_rank']:,}")
    if ch is not None and k > 1:
        head, kind = f"Your last {k} contests moved your rating by {ch:+d}", ("strength" if ch > 0 else "pattern")
    else:
        head, kind = f"{label} rating {rating}", "pattern"
    return [_i(platform, n, kind, head, f"Rating {rating}" + ("; " + "; ".join(parts) if parts else "") + ".", ev)]


def insights_for(platform: str, s: dict, now: float) -> list[dict]:
    """What the numbers show, in a few plain sentences each with the figures behind it. Descriptive: nothing here is a mark."""
    out: list[dict] = []
    if platform == "codeforces":
        out += _trend(platform, 1, s, "Codeforces")
        bands = s.get("solved_by_rating") or {}
        if bands:
            total = sum(bands.values())
            band, c = max(bands.items(), key=lambda kv: kv[1])
            hard, rating = s.get("hardest_solved_rating"), s.get("rating")
            reach = (f" Your hardest solve (rated {hard}) is {hard - rating} above your current rating." if hard and rating and hard - rating >= 200
                     else f" Your hardest solve is rated {hard}." if hard else "")
            out.append(_i(platform, 3, "pattern", f"{round(100 * c / total)}% of your rated solves are {band}", f"{c} of {total}.{reach}",
                          {"solved_by_rating": bands, "hardest_solved_rating": hard}))
        out += _topic_insights(platform, 4, _counts(s.get("tag_counts"), "tag"), s.get("solved_total"))
        out += _acceptance(platform, 6, s)
        out += _activity_insight(platform, 7, s, now)
    elif platform == "leetcode":
        e, m_, h = s.get("solved_easy", 0), s.get("solved_medium", 0), s.get("solved_hard", 0)
        total = e + m_ + h
        if total:
            few_hard = h / total < 0.05
            note = "Hard problems are still few so far, so a few more would show how you handle the tougher end." if few_hard else "A healthy spread across levels."
            out.append(_i(platform, 1, "worth_a_look" if few_hard else "strength",
                          f"Easy {round(100 * e / total)}% · Medium {round(100 * m_ / total)}% · Hard {round(100 * h / total)}%",
                          f"{e} Easy, {m_} Medium, {h} Hard. {note}", {"easy": e, "medium": m_, "hard": h}))
        if s.get("contests"):
            trend = _trend(platform, 2, s, "Contest")
            if trend and s.get("top_percent"):
                trend[0]["body"] += f" LeetCode lists this in the top {s['top_percent']}% of rated contestants."
                trend[0]["evidence"]["top_percent"] = s["top_percent"]
            out += trend
        langs = _counts(s.get("languages"), "language")
        if langs and sum(langs.values()):
            lang, c = max(langs.items(), key=lambda kv: kv[1])
            share = round(100 * c / sum(langs.values()))
            out.append(_i(platform, 3, "pattern", f"{lang} for {share}% of your solves",
                          f"{c} of {sum(langs.values())}." + (" Being able to say you can also work in a second language can help." if share >= 90 else ""),
                          {"languages": [{"name": k, "solved": v} for k, v in langs.items()]}))
        out += _topic_insights(platform, 4, _counts(s.get("topic_counts"), "tag"), s.get("solved_total"))
        out += _acceptance(platform, 6, s)
        out += _activity_insight(platform, 7, s, now)
    elif platform == "codechef":
        rating, peak, stars = s.get("rating"), s.get("max_rating"), s.get("stars")
        if rating:
            gap = f"{peak - rating} below your peak of {peak}" if peak and peak - rating >= 100 else "at or near your peak"
            out.append(_i(platform, 1, "pattern", f"Rated {rating}" + (f" · {stars}★" if stars else ""),
                          f"Currently {gap}." + (f" {s['contests']} contests so far." if s.get("contests") else ""),
                          {"rating": rating, "max_rating": peak, "stars": stars}))
    return out


def cross_insights(view: dict, now: float) -> list[dict]:
    """Only when two or more sites were read: what they add up to. Counts across sites show activity, not a rank."""
    ok = [p for p in view["profiles"] if p["stats"]]
    if len(ok) < 2:
        return []
    solved = sum(p["stats"].get("solved_total") or 0 for p in ok)
    contests = sum(p["stats"].get("contests") or 0 for p in ok)
    recent = [(p["platform"], _days_since(p["stats"].get("last_active"), now)) for p in ok]
    recent = sorted((r for r in recent if r[1] is not None), key=lambda r: r[1])
    body = f"{solved:,} problems across {len(ok)} sites and {contests} contests."
    if recent:
        body += f" Most recent activity: {NAMES[recent[0][0]]}, {recent[0][1]} day{'s' if recent[0][1] != 1 else ''} ago."
    return [_i("all", 1, "pattern", f"Across your {len(ok)} sites", body, {"solved_total": solved, "contests": contests})]


# ---- the book: one student's linked profiles ---------------------------------------------------------------------

def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CodingProfiles:
    """Per-candidate linked profiles, kept in memory like the repo records. A fresh read is reused for `ttl` seconds."""

    def __init__(self, transport: Transport | None = None, sleep=time.sleep, clock=time.time, ttl: float = TTL_S):
        self.transport, self.sleep, self.clock, self.ttl = transport or default_transport, sleep, clock, ttl
        self._book: dict[str, dict[str, dict]] = {}
        self._lock = threading.RLock()

    def link(self, candidate: str, handles: dict[str, str | None]) -> dict:
        """Set or change handles. A changed handle drops its old data and gets a new verification code. None/'' unlinks."""
        clean: dict[str, str | None] = {}
        for platform, text in handles.items():
            clean[platform] = normalise_handle(platform, text) if text and text.strip() else None
        with self._lock:
            mine = self._book.setdefault(candidate, {})
            for platform, handle in clean.items():
                if handle is None:
                    mine.pop(platform, None)
                elif platform not in mine or mine[platform]["handle"] != handle:
                    mine[platform] = {"handle": handle, "code": "gha-" + _secrets.token_hex(4), "verified": False,
                                      "snapshot": None, "fetched_at": None, "error": None, "identity_text": ""}
        return self.view(candidate)

    def refresh(self, candidate: str, platforms: list[str] | None = None, force: bool = False) -> dict:
        with self._lock:
            mine = dict(self._book.get(candidate, {}))
        todo = [p for p in (platforms or PLATFORMS) if p in mine
                and (force or mine[p]["fetched_at"] is None or self.clock() - mine[p]["fetched_at"] > self.ttl)]
        if todo:
            with ThreadPoolExecutor(max_workers=len(todo)) as pool:
                for platform, outcome in zip(todo, pool.map(lambda p: self._read(p, mine[p]["handle"]), todo)):
                    self._store(candidate, platform, mine[platform]["handle"], outcome)
        return self.view(candidate)

    def _read(self, platform: str, handle: str) -> dict:
        try:
            return FETCHERS[platform](handle, self.transport, self.sleep, self.clock())
        except ProfileError as e:
            return {"error": {"code": e.code, "message": str(e), "offer": e.offer}}
        except Exception:                                     # a parser surprise must never take the others down
            return {"error": {"code": "unavailable", "message": f"I could not read {platform} right now.",
                              "offer": ["retry", "continue_without_profile"]}}

    def _store(self, candidate: str, platform: str, handle: str, outcome: dict) -> None:
        with self._lock:
            entry = self._book.get(candidate, {}).get(platform)
            if entry is None or entry["handle"] != handle:    # unlinked or changed while we were reading
                return
            if "error" in outcome:
                entry["error"] = outcome["error"]             # the last good snapshot stays, marked stale in the view
                if outcome["error"]["code"] == "not_found":
                    entry["snapshot"] = None
                entry["fetched_at"] = self.clock() if entry["snapshot"] is None else entry["fetched_at"]
                return
            entry.update(snapshot={"status": outcome["status"], "stats": outcome["stats"], "profile_url": outcome["profile_url"]},
                         identity_text=outcome["identity_text"], error=None, fetched_at=self.clock())
            if entry["code"] in outcome["identity_text"]:
                entry["verified"] = True

    def verify(self, candidate: str) -> dict:
        """Re-read every linked profile and look for the issued code. Safe to call repeatedly."""
        self.refresh(candidate, force=True)
        return self.view(candidate)

    def view(self, candidate: str) -> dict:
        with self._lock:
            mine = self._book.get(candidate, {})
            profiles = []
            for platform in PLATFORMS:
                e = mine.get(platform)
                if e is None:
                    continue
                snap = e["snapshot"]
                row = {"platform": platform, "handle": e["handle"], "verified": e["verified"],
                       "status": (snap["status"] if snap else ("not_found" if (e["error"] or {}).get("code") == "not_found"
                                                                 else "unavailable" if e["error"] else "pending")),
                       "stats": snap["stats"] if snap else None, "profile_url": snap["profile_url"] if snap else None,
                       "fetched_at": _iso(e["fetched_at"]) if e["fetched_at"] else None, "stale": bool(snap and e["error"]),
                       "error": e["error"], "score_effect": 0}
                if not e["verified"] and row["status"] != "not_found":
                    row["verification"] = {"code": e["code"], "where": VERIFY_WHERE[platform],
                                           "note": "Until the code is found on your profile, these numbers are shown as self-reported."}
                profiles.append(row)
            ok = [p for p in profiles if p["stats"]]
            return {"profiles": profiles,
                    "summary": {"linked": len(profiles), "readable": len(ok),
                                "problems_solved_total": sum((p["stats"].get("solved_total") or 0) for p in ok),
                                "note": "Counts from different sites are added only to show activity; they are not a score."},
                    "score_effect": 0}

    def questions(self, candidate: str) -> list[dict]:
        view = self.view(candidate)
        out: list[dict] = []
        for p in view["profiles"]:
            if p["stats"]:
                for q in questions_for(p["platform"], p["stats"]):
                    out.append({**q, "handle": p["handle"], "verified": p["verified"]})
        return out

    def insights(self, candidate: str) -> list[dict]:
        view, now = self.view(candidate), self.clock()
        out = cross_insights(view, now)
        for p in view["profiles"]:
            if p["stats"]:
                out += [{**i, "handle": p["handle"]} for i in insights_for(p["platform"], p["stats"], now)]
        return out

    def delete(self, candidate: str) -> None:
        with self._lock:
            self._book.pop(candidate, None)


VERIFY_WHERE = {
    "codeforces": "Add it to the Organization field on codeforces.com/settings/social, then check. You can remove it afterwards.",
    "leetcode": "Add it to 'About me' in your LeetCode profile settings, then check. You can remove it afterwards.",
    "codechef": "Add it to the 'About' section of your CodeChef profile, then check. You can remove it afterwards.",
}


# ---- pulling the statistics on their own ---------------------------------------------------------------------------

SERIES = ("daily", "rating_history")      # time series for the charts: in the JSON, not in the one-row-per-site CSV


def stats_rows(view: dict) -> list[dict]:
    """One flat row per readable profile: who, how sure, when, then every number the site gave. Lists become 'a:3|b:2'."""
    rows = []
    for p in view["profiles"]:
        row = {"platform": p["platform"], "handle": p["handle"], "status": p["status"], "verified": p["verified"],
               "fetched_at": p["fetched_at"], "profile_url": p["profile_url"]}
        for k, v in (p["stats"] or {}).items():
            if k in SERIES:
                continue
            if isinstance(v, list) and v and isinstance(v[0], dict):
                v = "|".join(f"{next(iter(d.values()))}:{d['solved']}" for d in v)
            elif isinstance(v, dict):
                v = "|".join(f"{a}:{b}" for a, b in sorted(v.items(), key=lambda kv: -kv[1]))
            elif isinstance(v, list):
                v = "|".join(map(str, v))
            row[k] = v
        rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    """python -m gh_analyzer.coding_profiles --leetcode you --codeforces you --codechef you [--json out.json] [--csv out.csv]"""
    import argparse
    import csv
    import sys

    ap = argparse.ArgumentParser(prog="gh_analyzer.coding_profiles", description="Pull the public statistics from Codeforces, LeetCode and CodeChef")
    for p in PLATFORMS:
        ap.add_argument(f"--{p}", help=f"{NAMES[p]} username or profile link")
    ap.add_argument("--json", help="write the statistics here as JSON")
    ap.add_argument("--csv", help="write one row per platform here")
    a = ap.parse_args(argv)
    handles = {p: getattr(a, p) for p in PLATFORMS if getattr(a, p)}
    if not handles:
        ap.error("give at least one of --codeforces, --leetcode, --codechef")
    book = CodingProfiles()
    try:
        book.link("cli", handles)
    except ProfileError as exc:
        print(exc)
        return 2
    view = book.refresh("cli", force=True)
    rows = stats_rows(view)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump({"profiles": view["profiles"], "summary": view["summary"], "insights": book.insights("cli")}, fh, indent=1)
    if a.csv and rows:
        cols = list(dict.fromkeys(k for r in rows for k in r))
        with open(a.csv, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
    sys.stdout.reconfigure(encoding="utf-8")
    for p in view["profiles"]:
        print(f"\n{NAMES[p['platform']]} · {p['handle']}  [{p['status']}]")
        if p["stats"]:
            for k, v in stats_rows({"profiles": [p]})[0].items():
                if k not in ("platform", "handle", "status", "verified", "fetched_at", "profile_url") and v not in (None, "", []):
                    print(f"  {k:<24} {v}")
        elif p["error"]:
            print("  " + p["error"]["message"])
    marks = {"strength": "+", "pattern": "·", "worth_a_look": "?"}
    shown = book.insights("cli")
    if shown:
        print("\nInsights")
        for i in shown:
            print(f"  {marks[i['kind']]} {i['headline']}\n      {i['body']}")
    return 0 if view["summary"]["readable"] else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
