"""The M03 pipeline end to end: snapshot, inventory, contribution, secrets, decisions, ranking, questions.

`analyse_repo` returns a plain dict. Two parts are kept apart on purpose:

  result["student"]  what the student may see: the read-back, the contribution range, the questions
  result["private"]  secret notices, and the audit trail (what was skipped and why)

There is no authenticity, similarity or originality field anywhere (AC-3.1), and nothing in the repository is executed.
Reference matching (reference.py) runs only when a corpus is supplied; without one `authored_confidence` comes from blame alone.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import contribution, decisions, forks, inventory, questions, ranking, reference, secrets, selection, snapshot, ts_detect

CONFIG_SCAN = (".env", ".json", ".yml", ".yaml", ".cfg", ".ini", ".toml", ".properties", ".txt")
ENTRY_NAMES = {"main.py", "app.py", "server.py", "manage.py", "run.py", "wsgi.py", "asgi.py", "index.js", "server.js",
               "app.js", "main.js", "index.ts", "main.ts", "main.go", "Main.java", "Program.cs"}


def _secret_scan_paths(root: Path, analysed: list[str]) -> list[str]:
    extra = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in inventory.EXCLUDE_DIRS]
        for fn in filenames:
            if fn.lower().endswith(CONFIG_SCAN) or fn.lower().startswith(".env"):
                rel = (Path(dirpath) / fn).relative_to(root).as_posix()
                if not inventory.path_excluded(rel):
                    try:
                        if (Path(dirpath) / fn).stat().st_size <= inventory.MAX_FILE_BYTES:
                            extra.append(rel)
                    except OSError:
                        pass
    return sorted(set(analysed) | set(extra))


def understanding(inv: inventory.Inventory) -> dict:
    """The deterministic 'what I understood' card: a file map, not prose. (The plan's model-written paragraph is not built.)"""
    top: dict[str, int] = {}
    entries = []
    for rel in inv.analysed:
        top[rel.split("/")[0] if "/" in rel else "."] = top.get(rel.split("/")[0] if "/" in rel else ".", 0) + 1
        if rel.split("/")[-1] in ENTRY_NAMES:
            entries.append(rel)
    return {"languages": inv.languages, "frameworks": inv.frameworks, "entry_points": sorted(entries)[:6],
            "top_level": dict(sorted(top.items(), key=lambda kv: -kv[1])[:8]),
            "needs_confirmation": True}


def analyse_repo(target: str, me: list[str] | None = None, resume_keywords: list[str] | None = None,
                 workdir: str | None = None, fork_of: str | None = None, reference_corpus: str | None = None) -> dict:
    try:
        snap = snapshot.snapshot(target, workdir)
    except snapshot.EmptyRepo as exc:
        return {"student": {"status": "empty", "message": str(exc), "no_repo_path": selection.NO_REPO}, "private": {}}
    try:
        root = snap.root
        log = snapshot.export_log(root)
        inv = inventory.scan(root)
        mine = [m.lower() for m in (me or [])]
        fork = forks.detect(root, snap.repo, snap.source, fork_of)
        upstream = fork.upstream_shas if fork.usable else None
        contrib = contribution.analyse(log, inv.analysed, root, mine or None, upstream)
        own_log = [c for c in log if c.sha not in upstream] if upstream else log
        blame = None
        if mine and contrib.history_usable and contrib.candidate_commits:
            blame = contribution.blame_lines_by_file(root, inv.analysed, set(mine))
        found = decisions.detect_repo(root, inv.analysed)
        corpus = reference.default_corpus(reference_corpus)
        found = ranking.score(found, root, inv.analysed, blame, resume_keywords, corpus)
        top = ranking.select_top(found)
        qs, skipped = questions.questions_for(top, root)
        qs_ok = {q.decision_id for q in qs}
        top = [d for d in top if d.id in qs_ok]
        secret_hits = secrets.scan_files(root, _secret_scan_paths(root, inv.analysed))

        notes = list(snap.notes) + fork.notes + contrib.notes
        if not top:
            notes.append("This repository is mostly configuration or has few decisions I can read. I will ask design questions instead.")
        elif len(top) < 5:
            notes.append("I found fewer than five decisions here, so the round will add questions from your resume.")
        kinds_total = {}
        for d in found:
            kinds_total[d.kind] = kinds_total.get(d.kind, 0) + 1
        parsed = {"Python"} | ({"JavaScript", "TypeScript", "Java", "Go"} if ts_detect.available() else set())
        non_py = [lang for lang in inv.languages if lang not in parsed | {"Jupyter", "HTML", "CSS", "SQL", "Shell"}]
        if non_py:
            notes.append("I read " + ", ".join(non_py) + " with reduced depth (a short list of checks, not a full parse).")
        if "Jupyter" in inv.languages:
            notes.append("Notebook code counts toward line totals but I did not look for decisions inside notebooks.")

        result = {
            "student": {
                "status": "ok",
                "repo": snap.repo,
                "pinned_sha": snap.sha[:12],
                "understanding": understanding(inv),
                "inventory": {k: v for k, v in inv.to_dict().items() if k in (
                    "total_files", "analysed_files", "excluded_files", "loc_total", "languages", "frameworks",
                    "has_tests", "test_loc", "has_ci", "has_dockerfile", "has_readme", "licence")},
                "fork": None if not fork.is_fork else {
                    **fork.to_dict(),
                    "statement": forks.statement(fork, sum(a for c in own_log for a, _, p in c.files if inventory.is_code(p)
                                                           and not inventory.path_excluded(p)), len(own_log)) if fork.usable else None},
                "contribution": {
                    "statement": contribution.describe(contrib),
                    "history_usable": contrib.history_usable,
                    "range": None if contrib.share_low is None else [contrib.share_low, contrib.share_high],
                    "cadence": contrib.cadence,
                    "active_days": contrib.active_days,
                    "span_days": contrib.span_days,
                    "primary_areas": contrib.primary_areas,
                    "other_contributors": contrib.other_contributors,
                    "identity_candidates": contribution.identity_candidates(own_log) if not mine else [],
                },
                "decisions": [{k: v for k, v in d.to_dict().items() if k in (
                    "id", "path", "line_start", "line_end", "snippet", "kind", "summary", "interest_score", "detector")} for d in top],
                "questions": [q.to_dict() for q in qs],
                "notes": notes,
            },
            "private": {
                "secret_notices": [s.notice() for s in secret_hits],
                "audit": {"skipped_decisions": skipped, "decisions_found_by_kind": kinds_total,
                          "decisions_set_aside_for_reference": sum(1 for d in found if d.reference_match),
                          "exclusion_reasons": inv.exclusion_reasons, "blame_share": contrib.blame_share,
                          "additions_share": contrib.additions_share, "shallow": snap.shallow},
            },
        }
        return result
    finally:
        snap.cleanup()


def render_markdown(result: dict) -> str:
    s = result["student"]
    if s.get("status") == "empty":
        nr = s["no_repo_path"]
        return f"# {nr['headline']}\n\n{nr['body']}\n\n" + "\n".join(f"- {b}" for b in nr["starter_builds"]) + "\n"
    u, inv, c = s["understanding"], s["inventory"], s["contribution"]
    out = [f"# {s['repo']} (read at {s['pinned_sha']})", ""]
    out += ["## What I understood", f"- Languages: " + ", ".join(f"{k} ({v:,} lines)" for k, v in u["languages"].items()) if u["languages"] else "- No source files recognised",
            f"- Frameworks: {', '.join(u['frameworks']) or 'none detected'}",
            f"- Entry points: {', '.join(u['entry_points']) or 'none found'}",
            f"- Top-level folders (files): " + ", ".join(f"{k} ({v})" for k, v in u["top_level"].items()),
            f"- {inv['analysed_files']} files read, {inv['excluded_files']} left out; tests: {'yes' if inv['has_tests'] else 'no'}; "
            f"CI: {'yes' if inv['has_ci'] else 'no'}", "  (Is this right? Tell me if not, and I will fix it.)", ""]
    if s.get("fork") and s["fork"].get("statement"):
        out += ["## A fork", s["fork"]["statement"], ""]
    out += ["## Your contribution", c["statement"], ""]
    if c["primary_areas"]:
        out.append("Most of your work is in: " + ", ".join(c["primary_areas"]) + "\n")
    if c["identity_candidates"]:
        out.append("Which of these are you?")
        out += [f"- {i['email']} ({', '.join(i['names'])}): {i['commits']} commits" for i in c["identity_candidates"][:8]]
        out.append("")
    out.append(f"## {len(s['decisions'])} things I could ask you about")
    qs_by = {}
    for q in s["questions"]:
        qs_by.setdefault(q["decision_id"], []).append(q)
    for d in s["decisions"]:
        first = qs_by.get(d["id"], [{}])[0]
        out += [f"### {d['path']}:{d['line_start']} ({d['kind'].replace('_', ' ')})", f"```\n{d['snippet']}\n```",
                f"> {first.get('question', '')}", ""]
    if s["notes"]:
        out += ["## Notes"] + [f"- {n}" for n in s["notes"]]
    return "\n".join(out) + "\n"
