"""python -m gh_analyzer.agents pr <folder | owner/name | github link> --base <ref> --head <ref> [--json OUT] [--no-verify]

Prints a teach-first, evidence-cited review of the change from `--base` to `--head` as markdown. Read-only: the
repository is never written to, nothing in it runs, no cache is written, and every string sent to the model is
redacted first. Exit codes: 0 for a report (or "no changes"), 1 when the review could not finish (an unknown
ref included), 2 when `LLM_API_KEY` is missing, the repository cannot be read, or the `--json` file cannot be
written (the markdown report is still printed).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .. import context, inventory, repomap, snapshot
from .gitread import valid_ref
from .llm import ChatClient, OpenAICompatClient
from .pr_agent import review_pr
from .safe import safe_text

README_CHARS = 20000
README_SUFFIXES = {"", ".md", ".markdown", ".rst", ".txt", ".adoc"}
EXIT = {"ok": 0, "no_changes": 0, "incomplete": 1}
NO_KEY = "Set LLM_API_KEY in the environment to run the PR agent (LLM_BASE_URL and LLM_MODEL are optional)."


def _readme(root: Path) -> str:
    """The first README at the repository root, read as text only; empty if there is none."""
    try:
        for p in sorted(root.iterdir(), key=lambda q: q.name):
            if p.name.lower().startswith("readme") and p.suffix.lower() in README_SUFFIXES and p.is_file():
                with open(p, encoding="utf-8", errors="replace") as fh:
                    return fh.read(README_CHARS)
    except OSError:
        pass
    return ""


def _fetch_refs(snap: snapshot.Snapshot, *refs: str) -> None:
    """A GitHub clone holds only the default branch: fetch any other ref into the throwaway clone (never a local repo)."""
    if snap.source != "github":
        return
    for ref in dict.fromkeys(refs):
        if not valid_ref(ref):
            continue
        if snapshot.git(snap.root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False).strip():
            continue
        snapshot.git(snap.root, "fetch", "--quiet", "--no-tags", "--depth", str(snapshot.CLONE_DEPTH), "origin",
                     f"+refs/heads/{ref}:refs/heads/{ref}", check=False, timeout=snapshot.CLONE_TIMEOUT_S)


def _context(root: Path) -> tuple[repomap.Tier0, str, int]:
    """Tier 0, Tier 1 and Tier 1's estimated token count, built in memory (no cache is written)."""
    inv = inventory.scan(root)
    t0 = repomap.build_tier0(root, inv.analysed, inv.languages)
    text, tokens = context.build_tier1(t0, _readme(root))
    return t0, text, tokens


def render(report: dict) -> str:
    lines = ["# Pull request review", "", report.get("summary") or ""]
    if report.get("changes"):
        lines += ["", "## What changed", ""]
        lines += [f"- `{c['path']}`: {c['what']}" for c in report["changes"]]
    if report.get("checklist"):
        lines += ["", "## Things to check before you open the pull request", ""]
        for c in report["checklist"]:
            cited = ", ".join(c.get("evidence", []))
            lines.append(f"- {c['item']} ({cited})")
            if c.get("why"):
                lines.append(f"  Why it matters: {c['why']}")
    if report.get("suggested_title") or report.get("suggested_description"):
        lines += ["", "## Suggested title", "", report.get("suggested_title") or "(none)",
                  "", "## Suggested description", "", report.get("suggested_description") or "(none)"]
    if report.get("evidence"):
        lines += ["", "## Evidence", ""]
        for eid, e in report["evidence"].items():
            shown = ", ".join(f"{k}={v}" for k, v in e.get("args", {}).items())
            lines.append(f"- {eid}: {e.get('tool')}({shown})")
    tokens = report.get("tokens", {})
    tier1 = f"; project overview {tokens['tier1']} of the prompt" if "tier1" in tokens else ""
    lines += ["", f"Tokens used: {tokens.get('total', 0)} "
                  f"(prompt {tokens.get('prompt', 0)}, completion {tokens.get('completion', 0)}{tier1})",
              f"Checklist items dropped for lack of evidence: {report.get('dropped', 0)}"]
    if report.get("status") == "ok" and not report.get("verified"):
        lines.append("Checklist items were not double-checked (--no-verify).")
    for note in report.get("notes") or []:
        lines.append(f"Note: {note}")
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="gh_analyzer.agents",
                                 description="Opt-in, read-only helpers that use a language model")
    sub = ap.add_subparsers(dest="command", required=True)
    pr = sub.add_parser("pr", help="explain a branch's changes and suggest a pull request title and description")
    pr.add_argument("target", help="a local git folder, owner/name, or a github.com link")
    pr.add_argument("--base", required=True, help="the branch or commit the pull request goes into")
    pr.add_argument("--head", required=True, help="the branch or commit with your changes")
    pr.add_argument("--json", help="also write the report as JSON here (evidence ids with tool and arguments only)")
    pr.add_argument("--no-verify", action="store_true", help="skip the second model pass that checks each item")
    return ap


def _progress(line: str) -> None:
    print(f"  {line}", file=sys.stderr)


def _reconfigure_streams() -> None:
    """UTF-8 stdout, and no UnicodeEncodeError on either stream (a cp1252 pipe and a non-ASCII path)."""
    for stream, kwargs in ((sys.stdout, {"encoding": "utf-8", "errors": "replace"}),
                           (sys.stderr, {"errors": "replace"})):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(**kwargs)
        except (ValueError, OSError, TypeError):
            pass


def _write_json(path: str, report: dict) -> bool:
    """Write the JSON report; on failure print one plain line to stderr and return False."""
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
    except OSError as exc:
        print(f"Could not write {path}: {exc.strerror or type(exc).__name__}", file=sys.stderr)
        return False
    return True


def main(argv: list[str] | None = None, client: ChatClient | None = None) -> int:
    _reconfigure_streams()
    a = _parser().parse_args(argv)
    if client is None:
        if not os.environ.get("LLM_API_KEY", "").strip():
            print(NO_KEY)
            return 2
        client = OpenAICompatClient()
    try:
        snap = snapshot.snapshot(a.target)
    except snapshot.SnapshotError as exc:
        print(f"Could not read that repository: {safe_text(str(exc), max_chars=300)}")
        return 2
    try:
        try:
            _fetch_refs(snap, a.base, a.head)
            t0, tier1, tier1_tokens = _context(snap.root)
        except (OSError, snapshot.SnapshotError) as exc:
            print(f"Could not read that repository: {safe_text(str(exc), max_chars=300)}")
            return 2
        report = review_pr(snap.root, a.base, a.head, t0, tier1, client, verify=not a.no_verify, log=_progress)
    finally:
        snap.cleanup()
    # Tier 1 is an estimate of the project overview's share of the opening prompt (it is already counted in
    # prompt/total when the model was called); the existing keys keep their meaning.
    report.setdefault("tokens", {})["tier1"] = int(tier1_tokens)
    written = _write_json(a.json, report) if a.json else True
    print(render(report))
    return EXIT.get(report.get("status"), 1) if written else 2


if __name__ == "__main__":
    sys.exit(main())
