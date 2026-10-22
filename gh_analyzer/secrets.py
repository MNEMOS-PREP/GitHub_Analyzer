"""Secrets committed to a repository: found, redacted, and reported privately.

Three rules, all enforced by tests:
  1. A secret is never shown in a question, a decision snippet or any output.
     Only the first four characters and a length are kept.
  2. The only thing a student sees is a private, kind notice to rotate it.
  3. Ordinary strings such as version numbers and placeholders do not fire.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

PATTERNS: list[tuple[str, re.Pattern]] = [
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("OpenAI-style API key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----")),
]
GENERIC = re.compile(r"""(?ix)\b(api[_-]?key|secret|passw(?:or)?d|token|auth[_-]?key)\b\s*[:=]\s*['"]([^'"\s]{16,})['"]""")
PLACEHOLDER = re.compile(r"(your|xxx|changeme|example|placeholder|dummy|sample|<.*>|\$\{|process\.env|os\.environ|todo|test)", re.I)
ENTROPY_MIN = 3.0   # bits per character; ordinary words and paths sit below this, random keys above


@dataclass
class SecretFinding:
    path: str
    line: int
    kind: str
    preview: str          # first four characters only

    def notice(self) -> str:
        return (f"Heads up: there is what looks like a {self.kind} committed in {self.path}:{self.line}. "
                "Rotate it and add it to .gitignore.")


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = {ch: s.count(ch) for ch in set(s)}
    return -sum(c / len(s) * math.log2(c / len(s)) for c in counts.values())


def _preview(s: str) -> str:
    return s[:4] + "…"


def scan_text(path: str, text: str) -> list[SecretFinding]:
    found: list[SecretFinding] = []
    for n, line in enumerate(text.splitlines(), 1):
        if len(line) > 2000:
            continue
        hit = False
        for kind, rx in PATTERNS:
            m = rx.search(line)
            if m:
                found.append(SecretFinding(path, n, kind, _preview(m.group(0))))
                hit = True
                break
        if hit:
            continue
        m = GENERIC.search(line)
        if m:
            value = m.group(2)
            if PLACEHOLDER.search(value) or PLACEHOLDER.search(line.split("=")[0] + value) or _entropy(value) < ENTROPY_MIN:
                continue
            found.append(SecretFinding(path, n, "hard-coded credential", _preview(value)))
    return found


def redact(text: str) -> str:
    """Remove every secret from `text` before it can reach a snippet, question or log."""
    for _, rx in PATTERNS:
        text = rx.sub("[REDACTED]", text)

    def _generic(m: re.Match) -> str:
        value = m.group(2)
        if PLACEHOLDER.search(value) or _entropy(value) < ENTROPY_MIN:
            return m.group(0)
        return m.group(0).replace(value, "[REDACTED]")

    return GENERIC.sub(_generic, text)


def scan_files(root: Path, paths: list[str]) -> list[SecretFinding]:
    out: list[SecretFinding] = []
    for rel in paths:
        try:
            out += scan_text(rel, (Path(root) / rel).read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    return out
