"""Stage 7b of M03: which decisions are worth asking about.

    interest = 0.30·kind_weight + 0.25·authored_confidence + 0.20·centrality
             + 0.15·(1 − has_explanatory_comment) + 0.10·resume_claim_linkage

The five weights come from the M03 plan and are PRIORS: nobody has fitted them to
data. The per-kind weights below are also priors, set by hand to put decisions with
an obvious "why that value?" ahead of ones that are merely present.

Rules that are not weights:
  * a decision whose authored_confidence is below 0.5 is never asked about
    (we do not question the student about code someone else wrote);
  * no two decisions with the same kind and the same wording (the same setting repeated in many places is asked once);
  * at most 3 decisions per file and at least 3 distinct kinds when the repository has them;
  * at most 12 decisions are returned.

authored_confidence is the share of the decision's own lines that git blame attributes
to the student. When identities or blame are unavailable it is 0.5, deliberately
neutral: not asked less, not asked more.
"""

from __future__ import annotations

import ast
from pathlib import Path

from . import modgraph, reference
from .decisions import Decision

W_KIND, W_AUTH, W_CENTRAL, W_NOCOMMENT, W_RESUME = 0.30, 0.25, 0.20, 0.15, 0.10
KIND_WEIGHT = {
    "hyperparameter": 1.0, "model_arch": 1.0, "magic_constant": 0.9, "data_split": 0.9, "algorithm_choice": 0.8,
    "schema_design": 0.8, "concurrency": 0.8, "error_handling": 0.7, "caching": 0.7, "security": 0.6,
    "api_contract": 0.6, "dependency_choice": 0.5,
}
MIN_AUTHORED = 0.5
NEUTRAL_AUTHORED = 0.5
TOP_N, PER_FILE, MIN_KINDS = 12, 3, 3


def import_graph(root: Path, analysed: list[str]) -> dict[str, int]:
    """In-degree of each file: how many other files import it (Python by `ast`, the rest by modgraph)."""
    py = [p for p in analysed if p.endswith(".py")]
    modules: dict[str, str] = {}
    for rel in py:
        parts = rel[:-3].split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            modules[".".join(parts)] = rel
    indeg = {rel: 0 for rel in py}
    for rel in py:
        try:
            tree = ast.parse((Path(root) / rel).read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError, ValueError):
            continue
        pkg = rel[:-3].split("/")[:-1]
        targets: set[str] = set()
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = pkg[: len(pkg) - (node.level - 1)] if node.level else []
                head = ".".join(base + ([node.module] if node.module else []))
                names = [head] + [f"{head}.{a.name}".strip(".") for a in node.names]
            for n in names:
                parts = n.split(".")
                for i in range(len(parts), 0, -1):          # longest local prefix wins
                    hit = modules.get(".".join(parts[:i]))
                    if hit:
                        targets.add(hit)
                        break
        targets.discard(rel)
        for t in targets:
            indeg[t] += 1
    indeg.update(modgraph.in_degree(root, analysed))      # JavaScript, TypeScript, Java and Go
    return indeg


def authored_confidence(d: Decision, blame: dict[str, dict[int, bool]] | None) -> float:
    if not blame or d.path not in blame:
        return NEUTRAL_AUTHORED
    attributed = [blame[d.path][n] for n in range(d.line_start, d.line_end + 1) if n in blame[d.path]]
    return round(sum(attributed) / len(attributed), 3) if attributed else NEUTRAL_AUTHORED


def score(decisions: list[Decision], root: Path, analysed: list[str], blame: dict | None = None,
          resume_keywords: list[str] | None = None, corpus: "reference.Corpus | None" = None) -> list[Decision]:
    indeg = import_graph(root, analysed)
    top = max(indeg.values(), default=0)
    kw = [k.lower() for k in (resume_keywords or []) if k.strip()]
    for d in decisions:
        d.authored_confidence = authored_confidence(d, blame)
        if corpus is not None:                       # code that matches reference material is not asked about as a design choice
            reference.cap_confidence([d], root, corpus)
        central = (indeg.get(d.path, 0) / top) if top else 0.0
        linked = 1.0 if kw and any(k in (d.snippet + " " + d.path + " " + d.summary).lower() for k in kw) else 0.0
        d.interest_score = round(W_KIND * KIND_WEIGHT.get(d.kind, 0.5) + W_AUTH * d.authored_confidence
                                 + W_CENTRAL * central + W_NOCOMMENT * (0.0 if d.has_comment else 1.0)
                                 + W_RESUME * linked, 4)
    return decisions


def select_top(decisions: list[Decision], n: int = TOP_N, per_file: int = PER_FILE, min_kinds: int = MIN_KINDS) -> list[Decision]:
    pool = sorted((d for d in decisions if (d.authored_confidence if d.authored_confidence is not None else NEUTRAL_AUTHORED) >= MIN_AUTHORED),
                  key=lambda d: (-d.interest_score, d.path, d.line_start))
    chosen: list[Decision] = []
    per: dict[str, int] = {}
    said: set[tuple[str, str]] = set()          # one question per distinct finding, not ten for the same setting repeated
    for d in pool:
        if per.get(d.path, 0) >= per_file or (d.kind, d.summary) in said:
            continue
        said.add((d.kind, d.summary))
        chosen.append(d)
        per[d.path] = per.get(d.path, 0) + 1
        if len(chosen) == n:
            break
    kinds_available = {d.kind for d in pool}
    # diversity: if fewer than min_kinds kinds were picked but more exist, swap in the best of each missing kind
    while len({d.kind for d in chosen}) < min(min_kinds, len(kinds_available)):
        missing = [d for d in pool if d.kind not in {c.kind for c in chosen} and d not in chosen
                   and per.get(d.path, 0) < per_file and (d.kind, d.summary) not in said]
        if not missing:
            break
        add = missing[0]
        counts: dict[str, int] = {}
        for c in chosen:
            counts[c.kind] = counts.get(c.kind, 0) + 1
        over = [c for c in chosen if counts[c.kind] > 1]
        if len(chosen) >= n and over:
            drop = min(over, key=lambda c: c.interest_score)
            chosen.remove(drop)
            per[drop.path] -= 1
        elif len(chosen) >= n:
            break
        chosen.append(add)
        said.add((add.kind, add.summary))
        per[add.path] = per.get(add.path, 0) + 1
    return sorted(chosen, key=lambda d: (-d.interest_score, d.path, d.line_start))
