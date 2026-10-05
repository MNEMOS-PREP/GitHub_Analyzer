"""Stage 4 of M03: how much of the code did this student write, honestly.

Design decisions that come straight from the spec and that tests enforce:

  * Contribution is a RANGE with a stated method, never a single number (Trap 2).
    Two independent estimates, additions share and blame share, are widened by
    five points either way. If they disagree by more than 30 points we say so.
  * Squashed history (one or two commits, or the whole tree added within an
    hour) means `history_usable = False` and NO number is produced (FR-3.16).
  * No verdict. Cadence labels downgrade our own confidence; they are never
    shown as a judgement. `single_dump` in particular is not an accusation.
  * Identity comes first. Without confirmed identities we list the candidates
    and compute nothing about "you".

Shallow clones: the oldest commit of a shallow clone appears to add the entire
tree, so who wrote that part is unknown. Those "boundary" additions and lines are
not credited to anyone; instead the range is stretched to cover both extremes
(all of it theirs, or none of it), so a recent contributor cannot be shown as the
author of code that predates the history we could read.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from .inventory import is_code, path_excluded
from .snapshot import Commit, SnapshotError, _boundary_shas, git

WIDEN = 0.05                 # ± applied to the min/max of the two estimates (spec §6.4)
DISAGREE = 0.30              # estimates further apart than this get a note (spec §6.4)
SINGLE_DUMP_FRACTION = 0.80  # of additions inside one 24-hour window (spec §6.4)
STEADY_DAYS, STEADY_SPAN_DAYS = 8, 21
BURSTY_MAX_SESSIONS, SESSION_GAP_DAYS = 3, 7
MIN_COMMITS_FOR_CADENCE = 5
MAX_BLAME_FILES = 400
_HEADER = re.compile(r"^[0-9a-f]{40} \d+ \d+")
LAZY_MESSAGES = {"update", "updates", "fix", "fixes", "changes", "change", "commit", "initial commit", "first commit",
                 "wip", "test", "tests", "added files", "add files", "minor changes", "final", "done", "stuff", "."}


@dataclass
class Contribution:
    history_usable: bool = True
    reason_if_unusable: str | None = None
    total_commits: int = 0
    candidate_commits: int = 0
    candidate_identities: list[str] = field(default_factory=list)
    other_contributors: int = 0
    additions_share: float | None = None
    blame_share: float | None = None
    share_low: float | None = None
    share_high: float | None = None
    cadence: str = "unknown"
    active_days: int = 0
    span_days: int = 0
    primary_areas: list[str] = field(default_factory=list)
    commit_message_quality: float | None = None
    uses_branches: bool = False
    uses_prs: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def identity_candidates(log: list[Commit]) -> list[dict]:
    """Who appears in the history, for the one-question 'are these commits yours?' step."""
    by_email: dict[str, Counter] = defaultdict(Counter)
    for c in log:
        by_email[c.email][c.name] += 1
    out = [{"email": e, "names": [n for n, _ in names.most_common()], "commits": sum(names.values())}
           for e, names in by_email.items()]
    return sorted(out, key=lambda d: -d["commits"])


def _matches(c: Commit, me: set[str]) -> bool:
    return c.email in me or c.name.lower() in me


def _usable_history(log: list[Commit]) -> tuple[bool, str | None]:
    real = [c for c in log if c.parents <= 1]
    if len(log) <= 2:
        return False, "it has only one or two commits, so it looks like it was uploaded in one go"
    dates = [c.date for c in log]
    span = max(dates) - min(dates)
    adds = [sum(a for a, _, _ in c.files) for c in real if not c.boundary]
    total = sum(adds)
    if span <= timedelta(hours=1) and total and max(adds) >= 0.8 * total:
        return False, "all of it was committed within one hour"
    return True, None


def _additions(log: list[Commit], me: set[str]) -> tuple[int, int, int, dict[str, float]]:
    """(mine, total, unknown, per-area additions by me) over analysed code files.

    `unknown` is the additions of shallow-boundary commits: their diff is the whole tree, so who wrote it is not known.
    """
    mine = total = unknown = 0
    areas: dict[str, float] = defaultdict(float)
    for c in log:
        for a, _, path in c.files:
            if not is_code(path) or path_excluded(path):
                continue
            if c.boundary:
                unknown += a
                continue
            total += a
            if _matches(c, me):
                mine += a
                area = path.split("/")[0] if "/" in path else "."
                areas[area] += a
    return mine, total, unknown, areas


def _interval(mine: int, total: int, unknown: int) -> tuple[float, float] | None:
    """Lower and upper bound of the share when `unknown` units could belong to anyone (all theirs, or none theirs)."""
    denom = total + unknown
    if denom == 0:
        return None
    return mine / denom, (mine + unknown) / denom


def blame_share(root: Path, paths: list[str], me: set[str], exclude: set[str] | None = None) -> tuple[int, int, int, dict]:
    """(mine, total, unattributed, info) over surviving lines at HEAD. Lines from shallow-boundary commits are 'unattributed'.

    `exclude` holds commits that are not part of the student's project at all (a fork's upstream history): their lines
    count toward neither side."""
    exclude = exclude or set()
    mine = total = unattributed = 0
    used = 0
    boundary_shas = _boundary_shas(root)
    for rel in paths[:MAX_BLAME_FILES]:
        if rel.endswith(".ipynb"):
            continue
        try:
            out = git(root, "blame", "--line-porcelain", "-w", "--", rel, timeout=60)
        except SnapshotError:
            continue
        used += 1
        email = name = sha = ""
        for ln in out.splitlines():
            if _HEADER.match(ln):
                sha = ln[:40]
            elif ln.startswith("author-mail "):
                email = ln[12:].strip().strip("<>").lower()
            elif ln.startswith("author "):
                name = ln[7:].strip().lower()
            elif ln.startswith("	"):            # the source line closes a record
                if sha in exclude:
                    pass
                elif sha in boundary_shas:
                    unattributed += 1
                else:
                    total += 1
                    if email in me or name in me:
                        mine += 1
                email = name = sha = ""
    info = {"files_blamed": used, "files_skipped": max(0, len(paths) - MAX_BLAME_FILES), "unattributed_lines": unattributed}
    return mine, total, unattributed, info


def blame_lines_by_file(root: Path, paths: list[str], me: set[str]) -> dict[str, dict[int, bool]]:
    """For authored_confidence: for each file, line number -> True if mine, False if someone else's. Boundary lines omitted."""
    out: dict[str, dict[int, bool]] = {}
    boundary_shas = _boundary_shas(root)
    for rel in paths[:MAX_BLAME_FILES]:
        if rel.endswith(".ipynb"):
            continue
        try:
            raw = git(root, "blame", "--line-porcelain", "-w", "--", rel, timeout=60)
        except SnapshotError:
            continue
        lines: dict[int, bool] = {}
        email = name = sha = ""
        lineno = 0
        for ln in raw.splitlines():
            if _HEADER.match(ln):
                sha, lineno = ln[:40], int(ln.split()[2])
            elif ln.startswith("author-mail "):
                email = ln[12:].strip().strip("<>").lower()
            elif ln.startswith("author "):
                name = ln[7:].strip().lower()
            elif ln.startswith("	"):
                if sha not in boundary_shas:
                    lines[lineno] = email in me or name in me
                email = name = sha = ""
        out[rel] = lines
    return out


def classify_cadence(commits: list[Commit], additions_by_commit: dict[str, int] | None = None) -> tuple[str, int, int]:
    """(label, active_days, span_days). Order: unknown, single_dump, bursty, steady.

    `bursty` is checked before `steady` so that three tight bursts months apart are not called steady.
    """
    real = [c for c in commits if not c.boundary]
    if len(real) < MIN_COMMITS_FOR_CADENCE:
        return "unknown", len({c.date.date() for c in real}), 0
    real.sort(key=lambda c: c.date)
    days = {c.date.date() for c in real}
    span = (real[-1].date - real[0].date).days
    weights = [max(1, sum(a for a, _, _ in c.files)) for c in real]
    total = sum(weights)
    j, window, best = 0, 0, 0
    for i in range(len(real)):
        window += weights[i]
        while real[i].date - real[j].date > timedelta(hours=24):
            window -= weights[j]
            j += 1
        best = max(best, window)
    if best >= SINGLE_DUMP_FRACTION * total:
        return "single_dump", len(days), span
    sessions = 1 + sum(1 for a, b in zip(real, real[1:]) if (b.date - a.date) > timedelta(days=SESSION_GAP_DAYS))
    if 2 <= sessions <= BURSTY_MAX_SESSIONS:
        return "bursty", len(days), span
    if len(days) >= STEADY_DAYS and span >= STEADY_SPAN_DAYS:
        return "steady", len(days), span
    return "unknown", len(days), span


def message_quality(commits: list[Commit]) -> float | None:
    msgs = [c.subject.strip().lower().rstrip(".") for c in commits if c.parents <= 1]
    if not msgs:
        return None
    good = sum(1 for m in msgs if len(m.split()) >= 3 and m not in LAZY_MESSAGES)
    return round(good / len(msgs), 2)


def analyse(log: list[Commit], analysed_paths: list[str], root: Path | None = None, me: list[str] | None = None,
            upstream: set[str] | None = None) -> Contribution:
    """`me` is the set of emails (or names) the student confirmed as theirs. Without it, no share is computed.

    `upstream` is the set of commits that belong to a fork's parent. They are not the student's project: they are left
    out of every count, and whether the history is usable is judged on the commits added on top of the fork."""
    if upstream:
        log = [c for c in log if c.sha not in upstream]
    out = Contribution(total_commits=len(log))
    out.uses_prs = any(c.subject.lower().startswith("merge pull request") for c in log)
    out.uses_branches = any(c.parents > 1 for c in log)
    usable, why = _usable_history(log) if not upstream else (True, None)
    if upstream and not log:
        usable, why = False, "nothing has been added on top of the fork yet"
    out.history_usable, out.reason_if_unusable = usable, why
    mine_set = {m.strip().lower() for m in (me or []) if m.strip()}
    out.candidate_identities = sorted(mine_set)
    if not usable:
        out.notes.append("I can't tell contribution from this history: " + why + ".")
        out.cadence = "unknown"
        return out
    if not mine_set:
        out.notes.append("Tell me which commits are yours (see the identity list) and I will work out your share.")
        out.cadence, out.active_days, out.span_days = classify_cadence(log)
        out.other_contributors = len({c.email for c in log}) - 1 if log else 0
        return out

    mine_commits = [c for c in log if _matches(c, mine_set)]
    out.candidate_commits = len(mine_commits)
    out.other_contributors = len({c.email for c in log if not _matches(c, mine_set)})
    if not mine_commits:
        out.notes.append("None of the commits match the identities you gave, so I cannot say anything about your share.")
        out.cadence, out.active_days, out.span_days = classify_cadence(log)
        return out
    out.cadence, out.active_days, out.span_days = classify_cadence(mine_commits if len(mine_commits) >= MIN_COMMITS_FOR_CADENCE else log)
    out.commit_message_quality = message_quality(mine_commits)

    a_mine, a_total, a_unknown, areas = _additions(log, mine_set)
    out.additions_share = round(a_mine / a_total, 3) if a_total else None
    intervals = []
    if (iv := _interval(a_mine, a_total, a_unknown)):
        intervals.append(iv)
    if root is not None:
        b_mine, b_total, b_unknown, info = blame_share(root, analysed_paths, mine_set, upstream)
        out.blame_share = round(b_mine / b_total, 3) if b_total else None
        if (iv := _interval(b_mine, b_total, b_unknown)):
            intervals.append(iv)
        if info["files_skipped"]:
            out.notes.append(f"Ownership was read for the first {MAX_BLAME_FILES} files only.")
    if a_unknown or (root is not None and b_unknown):
        out.notes.append("Part of this repository was written before the history I could read, so the range is wide on purpose.")
    if intervals:
        lo, hi = min(i[0] for i in intervals), max(i[1] for i in intervals)
        if len(intervals) == 1:
            out.notes.append("Only one way of measuring was available, so the range is wider than usual.")
            lo, hi = lo - 2 * WIDEN, hi + 2 * WIDEN
        out.share_low, out.share_high = round(max(0.0, lo - WIDEN), 3), round(min(1.0, hi + WIDEN), 3)
        if out.additions_share is not None and out.blame_share is not None and abs(out.additions_share - out.blame_share) > DISAGREE:
            out.notes.append("Attribution is unclear in this repository (it looks like a big refactor or a formatting pass).")
    total_mine = sum(areas.values())
    if total_mine:
        out.primary_areas = [a for a, v in sorted(areas.items(), key=lambda kv: -kv[1]) if v / total_mine >= 0.2][:3]
    return out


def describe(c: Contribution, rounding: int = 5) -> str:
    """The sentence a student sees. Always a range, always with a method, never a verdict."""
    if not c.history_usable:
        return "I can't tell from this history. " + (c.reason_if_unusable or "").capitalize() + "."
    if c.share_low is None or c.share_high is None:
        return "Tell me which commits are yours and I will work out your share."
    lo = int(round(c.share_low * 100 / rounding) * rounding)
    hi = int(round(c.share_high * 100 / rounding) * rounding)
    text = f"roughly {lo}–{hi}% of the code, based on commits and current file ownership"
    if c.other_contributors:
        n = c.other_contributors
        text += f". Also in this repository: {n} other contributor{'s' if n != 1 else ''}"
    return text
