"""The no-repo path (FR-3.18) and the top-up for a repo with too few decisions (spec section 10).

A student with no repository gets a round that is as good as the repository one: the same four-step ladder, asked the
way a professor would, about a project they name. Nothing here reads code, so nothing here can cite a line; the
`citation` field is None and the question says so by being about the design, not the file.

Nothing in this module changes a score. `affects_score` is False on every object it returns, and the wording goes
through the same banned-word check as the repository questions.
"""

from __future__ import annotations

import re

from .questions import BANNED, copy_violations

# Four steps, same shape as the repository ladder: what -> why -> what breaks -> what would you change.
DESIGN_LADDER = [
    "Tell me about {project}. What problem does it solve, and who would use it?",
    "What were the two or three biggest design choices in {project}, and what did you give up by making them?",
    "If {project} had ten times the users or ten times the data, what would break first, and how would you find out?",
    "If you rebuilt {project} this weekend, what would you do differently, and why?",
]
DESIGN_FALLBACK = "No worries if the details have faded. How would you approach the design of something like {project} today?"
DESIGN_EXPECTS = [["purpose", "users"], ["trade-offs", "alternatives"], ["scaling", "failure modes", "monitoring"], ["reflection", "learning"]]

# Extra depth when the student names a technology: one question per technology, still about reasoning.
TECH_PROBE = "You used {tech} in {project}. What made it the right choice there, and what would you have used if you could not use it?"

STARTER_BUILDS = [
    {"title": "A REST API with login and tests", "hours": 6,
     "brief": "Users sign up and log in, a few protected endpoints, input validation, and a test suite that runs in CI.",
     "talking_points": ["how you store passwords", "how you handle errors", "what your tests do and do not cover"]},
    {"title": "A small data pipeline with a scheduled job", "hours": 6,
     "brief": "Pull data from a public source on a schedule, clean it, store it, and report when a run fails.",
     "talking_points": ["what happens when a run fails halfway", "how you avoid duplicates", "how you would scale it"]},
    {"title": "A model trained on a public dataset with a proper evaluation", "hours": 6,
     "brief": "Pick a public dataset, hold out a test set properly, compare against a simple baseline, and write down what the numbers do and do not show.",
     "talking_points": ["why the split is fair", "what the baseline tells you", "where the model is wrong"]},
]


def _title(p: dict) -> str:
    return (p.get("title") or p.get("name") or p.get("text") or "").strip()


def _clean(project: str) -> str:
    return re.sub(r"\s+", " ", project).strip().strip(".")


def _question(text: str, depth: int, project: str, expects: list[str], fallback: str) -> dict:
    return {"depth": depth, "question": text, "expects": expects, "citation": None,
            "fallback_if_unanswered": fallback, "project": project, "kind": "design", "affects_score": False}


def _safe(qs: list[dict]) -> list[dict]:
    """Drop (never rewrite silently) anything that trips the wording check."""
    return [q for q in qs if not copy_violations(q["question"] + " " + q["fallback_if_unanswered"])]


def project_round(projects: list[dict], max_projects: int = 3) -> dict:
    """`projects`: [{'title': str, 'tech': [str]}] taken from the resume (M1) or typed by the student."""
    named = [p for p in projects if _clean(_title(p))][:max_projects]
    if not named:
        named = [{"title": "a project you are proud of", "tech": []}]
        generic = True
    else:
        generic = False
    qs: list[dict] = []
    for p in named:
        name = _clean(_title(p))
        fb = DESIGN_FALLBACK.format(project=name)
        for depth, tmpl in enumerate(DESIGN_LADDER):
            qs.append(_question(tmpl.format(project=name), depth, name, DESIGN_EXPECTS[depth], fb))
        for tech in (p.get("tech") or [])[:2]:
            qs.append(_question(TECH_PROBE.format(tech=tech, project=name), 1, name, ["alternatives", "trade-offs"], fb))
    qs = _safe(qs)
    return {"mode": "no_repo", "professor_style": True, "generic": generic, "affects_score": False,
            "projects": [_clean(_title(p)) for p in named], "questions": qs,
            "starter_builds": STARTER_BUILDS}


def supplement(claims: list[dict], inv, needed: int) -> list[dict]:
    """Design questions to top a thin repository round up to five (FR-3.10, section 10).

    Resume claims come first, because they are what the student said they did. If there are none, the repository's own
    stack gives the project a name to ask about. The questions never cite a line: they are about design.
    """
    if needed <= 0:
        return []
    projects = [{"title": c.get("text", ""), "tech": c.get("keywords", [])} for c in claims if c.get("text")]
    if not projects:
        stack = ", ".join((inv.frameworks or list(inv.languages))[:2])
        projects = [{"title": f"this project ({stack})" if stack else "this project", "tech": []}]
    out: list[dict] = []
    for p in projects:
        # one question per step of the ladder, shallowest first, so every project gets its opening question before any gets a third
        out += project_round([p], max_projects=1)["questions"][:4]
    out.sort(key=lambda q: q["depth"])
    return out[:max(needed, 0)]


def banned_in(obj) -> list[str]:
    """For tests and the CI scan: every banned word found in any string the module can return."""
    found: list[str] = []
    if isinstance(obj, str):
        found += BANNED.findall(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            found += banned_in(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            found += banned_in(v)
    return [f if isinstance(f, str) else f[0] for f in found]
