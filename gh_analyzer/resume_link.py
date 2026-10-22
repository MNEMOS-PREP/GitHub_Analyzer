"""Link a repository to the claims on a student's resume (FR-3.13).

`verifiability` says how much of a claim this repository can speak to. It is a pointer for the interviewer, not a
mark against the student: a claim the repository cannot speak to is "not_checkable", which is a statement about the
repository, never about the claim. There is no value that means "contradicted" and `score_effect` is always 0
(FR-3.14, AC-3.2).
"""

from __future__ import annotations

import re

STOP = {"the", "and", "for", "with", "using", "built", "build", "made", "make", "that", "this", "from", "into", "project",
        "app", "application", "system", "developed", "implemented", "created", "based", "web", "simple"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9+#.]+", text.lower()) if len(w) > 2 and w not in STOP}


def claim_terms(claim: dict) -> set[str]:
    terms = _words(claim.get("text", ""))
    for k in claim.get("keywords", []) or []:
        terms |= _words(str(k))
    return terms


def link(claims: list[dict], repo: str, inv, decisions) -> list[dict]:
    """One entry per claim: which evidence in this repository relates to it, and how much."""
    stack = {s.lower() for s in list(inv.frameworks) + list(inv.languages) + list(inv.dependencies)}
    stack_words = set().union(*[_words(s) for s in stack]) if stack else set()
    repo_words = _words(repo.replace("-", " ").replace("_", " ").replace("/", " "))
    decision_words = set().union(*[_words(f"{d.path} {d.summary}") for d in decisions]) if decisions else set()
    out = []
    for c in claims:
        terms = claim_terms(c)
        by_stack = sorted(terms & (stack | stack_words))
        by_name = sorted(terms & repo_words)
        by_code = sorted(terms & decision_words)
        evidence = [{"source": "stack", "terms": by_stack}] if by_stack else []
        evidence += [{"source": "repo_name", "terms": by_name}] if by_name else []
        evidence += [{"source": "decisions", "terms": by_code}] if by_code else []
        hit = set(by_stack) | set(by_name) | set(by_code)
        if len(hit) >= 2 or (terms and len(hit) / len(terms) >= 0.5 and hit):
            v = "supported"
        elif hit:
            v = "partly_supported"
        else:
            v = "not_checkable"
        out.append({"claim_id": c.get("id"), "claim": c.get("text", ""), "repo": repo, "verifiability": v,
                    "evidence": evidence, "score_effect": 0})
    return out
