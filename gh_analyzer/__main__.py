"""python -m gh_analyzer <repo path | owner/name | github link> [--me email ...] [--json out.json] [--md out.md]"""

import argparse
import json
import sys

from . import pipeline
from .snapshot import SnapshotError


def main() -> int:
    ap = argparse.ArgumentParser(prog="gh_analyzer", description="M03: what you built, how much of it you wrote, and what to ask about")
    ap.add_argument("target", help="a local git folder, owner/name, or a github.com link")
    ap.add_argument("--me", nargs="*", default=[], help="your commit emails or names (without them no share is computed)")
    ap.add_argument("--keywords", nargs="*", default=[], help="words from your resume to favour (e.g. project names, tools)")
    ap.add_argument("--fork-of", help="owner/name of the repository this one was forked from (found automatically when possible)")
    ap.add_argument("--reference", help="a corpus built with `python -m gh_analyzer.reference build` (or GH_ANALYZER_REFERENCE)")
    ap.add_argument("--json", help="write the full result (including the private part) here")
    ap.add_argument("--md", help="write the student-facing read-back here")
    a = ap.parse_args()
    try:
        result = pipeline.analyse_repo(a.target, a.me, a.keywords, fork_of=a.fork_of, reference_corpus=a.reference)
    except SnapshotError as exc:
        print(f"Could not read that repository: {exc}")
        return 2
    md = pipeline.render_markdown(result)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=1, default=str)
    if a.md:
        with open(a.md, "w", encoding="utf-8") as fh:
            fh.write(md)
    sys.stdout.reconfigure(encoding="utf-8")
    print(md)
    notices = result.get("private", {}).get("secret_notices", [])
    for n in notices:
        print("PRIVATE:", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
