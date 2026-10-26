"""The last check before anything leaves the module (AC-3.1, FR-3.15).

Two rules, enforced on every payload the API sends to a client:
  1. No key that could carry an authenticity or similarity value (`similarity`, `plagiarism`, `verdict`, ...).
  2. None of the banned words in the fields that hold our own wording.

It fails closed: a violation raises, the API returns a plain 500, and nothing is sent. Code snippets and paths are the
student's own text and are not scanned for words, only for keys.
"""

from __future__ import annotations

import re

FORBIDDEN_KEYS = re.compile(r"(similar|plagiar|authentic|originalit|verdict|reference_match|match_source|minhash|copied|suspicio|fraud|cheat)", re.I)
# `authentic`, not authenticate / authentication: the login code is not a word about the student.
BANNED_WORDS = re.compile(r"(plagiar|copied|copying|fake|authentic(?!at)|original|cheat|suspicious|fraud)", re.I)
# fields that hold wording we wrote; values elsewhere (snippets, code, paths, student-typed text) are not ours to police
COPY_FIELDS = {"question", "fallback_if_unanswered", "statement", "notes", "message", "hint", "body", "headline", "prompt",
               "read_aloud", "reason", "reason_if_unusable", "brief", "title", "talking_points", "starter_builds", "rating_prompt",
               "note", "offer_text"}
# keys whose values are the student's own (their code, file, function, project): our wording beside them quotes them,
# and what is theirs is taken out of it before the words are checked. Checked whole, "Function authenticate_user, in
# auth.py" made the explain gate a 500, and "Could not clone github.com/asha/fake-news" hid the failed repo's
# no-repo offer behind one.
THEIRS = {"code", "snippet", "path", "function", "citation", "project", "projects", "repo", "target", "tech", "claim"}
LINK_OR_PATH = re.compile(r"\S*[/\\]\S*")


class LeakError(RuntimeError):
    pass


def _theirs(value, out: list[str]) -> None:
    """The student's text under a THEIRS key, line by line (a read-aloud joins code lines with '. ')."""
    if isinstance(value, str):
        out.extend(line.strip() for line in value.splitlines() if line.strip())
    elif isinstance(value, (list, tuple)):
        for v in value:
            _theirs(v, out)
    elif isinstance(value, dict):
        for v in value.values():
            _theirs(v, out)


def _ours(text: str, theirs: list[str]) -> str:
    """The wording we wrote: the student's pieces, links and paths taken out."""
    for piece in sorted(theirs, key=len, reverse=True):
        text = text.replace(piece, " ")
    return LINK_OR_PATH.sub(" ", text)


def _walk(obj, path: str, in_copy: bool, problems: list[str], theirs: list[str] | None = None) -> None:
    theirs = list(theirs or [])
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in THEIRS:
                _theirs(v, theirs)
        for k, v in obj.items():
            if isinstance(k, str) and FORBIDDEN_KEYS.search(k):
                problems.append(f"{path}.{k}: key not allowed")
            _walk(v, f"{path}.{k}", k in COPY_FIELDS, problems, theirs)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _walk(v, f"{path}[{i}]", in_copy, problems, theirs)
    elif isinstance(obj, str) and in_copy:
        m = BANNED_WORDS.search(_ours(obj, theirs))
        if m:
            problems.append(f"{path}: banned word '{m.group(0).lower()}'")


def problems(payload) -> list[str]:
    out: list[str] = []
    _walk(payload, "$", False, out)
    return out


def assert_clean(payload):
    found = problems(payload)
    if found:
        raise LeakError("; ".join(found[:5]))
    return payload
