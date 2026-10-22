"""Stage 1 of M03: which of a student's repositories is worth an interview round, and the no-repo path.

    usefulness = 0.30·loc_norm + 0.20·span_norm + 0.15·has_readme + 0.15·resume_title_match
               + 0.10·has_tests + 0.10·(1 − is_practice_repo)

Weights are the plan's PRIORS. The two normalisers (3,000 lines and 90 days count as "full") are CONVENTIONS chosen
so a modest student project can reach 1.0; nothing was fitted. The function takes plain metadata so it can be used
with or without a live GitHub connection.
"""

from __future__ import annotations

import re

PRACTICE = re.compile(r"(dsa|leetcode|practice|assignment|hackerrank|striver|codeforces|algo-?practice)", re.I)
LOC_FULL, SPAN_FULL_DAYS = 3000, 90


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2}


def usefulness(repo: dict, resume_titles: list[str] | None = None) -> dict:
    """`repo` keys: name, loc, span_days, has_readme, has_tests. Returns the score and the human hint."""
    name = repo.get("name", "")
    practice = bool(PRACTICE.search(name))
    name_words = _words(name.replace("-", " ").replace("_", " "))
    match = any(len(name_words & _words(t)) >= max(1, min(2, len(name_words))) for t in (resume_titles or []) if name_words)
    s = (0.30 * min(1.0, repo.get("loc", 0) / LOC_FULL) + 0.20 * min(1.0, repo.get("span_days", 0) / SPAN_FULL_DAYS)
         + 0.15 * bool(repo.get("has_readme")) + 0.15 * match + 0.10 * bool(repo.get("has_tests"))
         + 0.10 * (0 if practice else 1))
    hint = ("This looks like practice code, not a project. It is usually not worth an interview round, but you can pick it."
            if practice else ("Matches a project on your resume." if match else ""))
    return {"name": name, "usefulness": round(s, 3), "practice": practice, "resume_match": match, "hint": hint}


def rank_repos(repos: list[dict], resume_titles: list[str] | None = None, top: int = 8) -> list[dict]:
    scored = [usefulness(r, resume_titles) | {"repo": r} for r in repos]
    return sorted(scored, key=lambda x: (-x["usefulness"], x["name"]))[:top]


NO_REPO = {
    "headline": "No repo? That is completely normal.",
    "body": ("I will ask about your projects the way a professor would: design, trade-offs, and what you would do "
             "differently, instead of asking about specific code."),
    "starter_builds": [
        "A REST API with login and tests",
        "A small data pipeline with a scheduled job",
        "A model trained on a public dataset with a proper evaluation",
    ],
    "affects_score": False,
}
