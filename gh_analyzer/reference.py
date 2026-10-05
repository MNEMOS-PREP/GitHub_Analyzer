"""Stage 5 of M03: code that matches reference material is not asked about as if the student designed it.

A student who follows a tutorial and commits the result under their own name has still, by `git blame`, written those
lines. Asking "why did you choose this value?" about a value the tutorial chose is unfair to them and useless to an
interviewer. So decisions whose surrounding code matches a *reference corpus* (tutorials, starter templates, course
handouts, library examples: whatever the platform collects) are set aside.

This is the plan's MinHash step, and it follows the plan's hard rules:
  * the match is NEVER shown to the student and is never a field in any output. Its only effect is that fewer questions
    are asked (it can lower `authored_confidence`, never raise it);
  * it never changes a score: a repository that resembles a tutorial is not penalised, it is simply asked about
    something else;
  * it makes no claim about how the code got there, so no wording about it exists anywhere student-facing.

Method. Source is normalised (comments dropped, identifiers, numbers and strings replaced by placeholders, so a renamed
copy still matches), cut into overlapping token shingles, and reduced to a 64-value MinHash signature. A signature is
made per window of code, not per file, because a copied snippet inside a larger file would never match as a whole file.
Candidate windows are found with banded hashing (16 bands of 4 rows) and confirmed by comparing the full signatures.

No corpus ships with this module. Without one, nothing is matched and behaviour is unchanged. Build one with

    python -m gh_analyzer.reference build <folder of reference code> -o corpus.json

and use it with `--reference corpus.json` (or the GH_ANALYZER_REFERENCE environment variable).

The constants below are CONVENTIONS, not fitted: nobody has measured the false-match rate on real tutorials yet.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .inventory import CODE_EXT, path_excluded

SHINGLE = 6                 # tokens per shingle
PERMS = 64                  # signature length
BANDS, ROWS = 16, 4         # BANDS * ROWS == PERMS
WINDOW, STRIDE = 30, 15     # lines per window and step, for corpus entries
RADIUS = 12                 # lines each side of a decision when it is looked up
MIN_TOKENS = 40             # a window with fewer tokens is too short to say anything
THRESHOLD = 0.6             # estimated Jaccard at or above which a window "matches"
CONFIDENCE_CAP = 0.4        # authored_confidence of a matching decision: below ranking.MIN_AUTHORED, so it is not asked
_MASK = (1 << 61) - 1
_TOKEN = re.compile(r"[A-Za-z_]\w*|\d+(?:\.\d+)?|==|!=|<=|>=|=>|->|&&|\|\||\+\+|--|\S")
_COMMENT = re.compile(r"(?m)#.*$|//.*$|/\*.*?\*/", re.S)
_STRING = re.compile(r"\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|`[^`]*`")
KEYWORDS = frozenset("""def class return if else elif for while try except finally with as import from in is not and or lambda yield
    async await function const let var new this throw catch switch case break continue public private protected static
    final void int float double boolean string package interface extends implements func go chan range defer struct type
    select fallthrough True False None true false null nil""".split())


def tokens(src: str) -> list[str]:
    """Code with the choices of the person who wrote it removed: names, numbers and strings become placeholders."""
    src = _STRING.sub(' "S" ', _COMMENT.sub(" ", src))
    out = []
    for t in _TOKEN.findall(src):
        if t[0].isdigit():
            out.append("N")
        elif t[0].isalpha() or t[0] == "_":
            out.append(t if t in KEYWORDS else "I")
        else:
            out.append(t)
    return out


def _h(shingle: tuple[str, ...]) -> tuple[int, int]:
    d = hashlib.blake2b(" ".join(shingle).encode(), digest_size=16).digest()
    return int.from_bytes(d[:8], "big") & _MASK, (int.from_bytes(d[8:], "big") & _MASK) | 1


def signature(toks: list[str]) -> list[int] | None:
    """MinHash signature of the token shingles, or None when the window is too short to compare."""
    if len(toks) < MIN_TOKENS:
        return None
    sig = [_MASK] * PERMS
    for i in range(len(toks) - SHINGLE + 1):
        a, b = _h(tuple(toks[i:i + SHINGLE]))
        for k in range(PERMS):
            v = (a + k * b) & _MASK
            if v < sig[k]:
                sig[k] = v
    return sig


def estimate(a: list[int], b: list[int]) -> float:
    return sum(x == y for x, y in zip(a, b)) / PERMS


@dataclass
class Corpus:
    entries: list[dict] = field(default_factory=list)            # {"name": str, "sig": [int]}
    _index: dict = field(default_factory=dict, repr=False)

    def add(self, name: str, sig: list[int]) -> None:
        i = len(self.entries)
        self.entries.append({"name": name, "sig": sig})
        for band in range(BANDS):
            self._index.setdefault((band, tuple(sig[band * ROWS:(band + 1) * ROWS])), []).append(i)

    def best(self, sig: list[int]) -> float:
        """Highest estimated Jaccard between `sig` and any corpus window (0.0 when none is a candidate)."""
        cands: set[int] = set()
        for band in range(BANDS):
            cands.update(self._index.get((band, tuple(sig[band * ROWS:(band + 1) * ROWS])), ()))
        return max((estimate(sig, self.entries[i]["sig"]) for i in cands), default=0.0)

    def save(self, path: str) -> None:
        Path(path).write_text(json.dumps({"version": 1, "perms": PERMS, "entries": self.entries}), encoding="utf-8")

    @staticmethod
    def load(path: str) -> "Corpus":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("perms") != PERMS:
            raise ValueError(f"corpus was built with {data.get('perms')} hashes, this version uses {PERMS}")
        c = Corpus()
        for e in data["entries"]:
            c.add(e["name"], e["sig"])
        return c


def _windows(src: str):
    lines = src.splitlines()
    for start in range(0, max(1, len(lines) - WINDOW + STRIDE), STRIDE):
        yield start + 1, "\n".join(lines[start:start + WINDOW])


def build(folder: str) -> Corpus:
    c = Corpus()
    root = Path(folder)
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and p.suffix.lower() in CODE_EXT and not path_excluded(rel):
            try:
                src = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line, text in _windows(src):
                sig = signature(tokens(text))
                if sig:
                    c.add(f"{rel}:{line}", sig)
    return c


def default_corpus(path: str | None = None) -> Corpus | None:
    path = path or os.environ.get("GH_ANALYZER_REFERENCE")
    if not path or not Path(path).is_file():
        return None
    try:
        return Corpus.load(path)
    except (ValueError, OSError, KeyError):
        return None


def matches(corpus: Corpus, root: Path, path: str, line_start: int, line_end: int) -> bool:
    """Does the code around this decision match something in the corpus?"""
    try:
        lines = (Path(root) / path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    lo, hi = max(0, line_start - 1 - RADIUS), min(len(lines), line_end + RADIUS)
    sig = signature(tokens("\n".join(lines[lo:hi])))
    return bool(sig) and corpus.best(sig) >= THRESHOLD


def cap_confidence(decisions, root: Path, corpus: Corpus | None) -> int:
    """Lower authored_confidence of decisions that match the corpus. Returns how many were set aside (for the audit trail)."""
    if corpus is None:
        return 0
    n = 0
    for d in decisions:
        if matches(corpus, root, d.path, d.line_start, d.line_end):
            d.authored_confidence = min(d.authored_confidence if d.authored_confidence is not None else 1.0, CONFIDENCE_CAP)
            d.reference_match = True
            n += 1
    return n


def main(argv: list[str]) -> int:
    if len(argv) >= 4 and argv[0] == "build" and argv[2] == "-o":
        c = build(argv[1])
        c.save(argv[3])
        print(f"{len(c.entries)} windows written to {argv[3]}")
        return 0
    print("usage: python -m gh_analyzer.reference build <folder> -o corpus.json")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
