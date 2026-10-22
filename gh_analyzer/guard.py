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
BANNED_WORDS = re.compile(r"(plagiar|copied|copying|fake|authentic|original|cheat|suspicious|fraud)", re.I)
# fields that hold wording we wrote; values elsewhere (snippets, code, paths, student-typed text) are not ours to police
COPY_FIELDS = {"question", "fallback_if_unanswered", "statement", "notes", "message", "hint", "body", "headline", "prompt",
               "read_aloud", "reason", "reason_if_unusable", "brief", "title", "talking_points", "starter_builds", "rating_prompt",
               "note", "offer_text"}


class LeakError(RuntimeError):
    pass


def _walk(obj, path: str, in_copy: bool, problems: list[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and FORBIDDEN_KEYS.search(k):
                problems.append(f"{path}.{k}: key not allowed")
            _walk(v, f"{path}.{k}", k in COPY_FIELDS, problems)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _walk(v, f"{path}[{i}]", in_copy, problems)
    elif isinstance(obj, str) and in_copy:
        m = BANNED_WORDS.search(obj)
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
