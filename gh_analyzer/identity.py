"""Resolve committer identity to the student, with a confirmation step (FR-3.6).

We never guess who the student is. The history lists who appears in it; the student says which are theirs; only then is
a share computed. A confirmed identity must be one that actually appears in the history, so a typo cannot silently
produce a "0%" that reads as a judgement.
"""

from __future__ import annotations

from .contribution import identity_candidates
from .snapshot import Commit


class IdentityError(ValueError):
    """A named, user-presentable problem with the confirmed identities."""


def candidates(log: list[Commit]) -> list[dict]:
    return identity_candidates(log)


def suggest(cands: list[dict], hints: list[str]) -> list[str]:
    """Pre-tick likely matches from what we already know (account email, GitHub login, name). Only a suggestion."""
    hints = [h.strip().lower() for h in hints if h and h.strip()]
    out: list[str] = []
    for c in cands:
        names = [n.lower() for n in c["names"]]
        local = c["email"].split("@")[0].lower()
        if any(h == c["email"].lower() or h == local or h in names or any(h in n for n in names) for h in hints):
            out.append(c["email"])
    return out


def confirm(cands: list[dict], chosen: list[str]) -> list[str]:
    """Validate the student's choice against the identities in the history; return them normalised (lower case)."""
    chosen = [c.strip().lower() for c in chosen if c and c.strip()]
    if not chosen:
        raise IdentityError("Pick at least one of the identities in the list, or tell me none of them are yours.")
    known = {c["email"].lower(): c for c in cands}
    by_name = {n.lower(): c["email"].lower() for c in cands for n in c["names"]}
    resolved: list[str] = []
    unknown: list[str] = []
    for c in chosen:
        if c in known:
            resolved.append(c)
        elif c in by_name:
            resolved.append(by_name[c])
        else:
            unknown.append(c)
    if unknown:
        raise IdentityError("I could not find " + ", ".join(unknown) + " in this repository's history. Choose from the list I showed.")
    return sorted(set(resolved))
