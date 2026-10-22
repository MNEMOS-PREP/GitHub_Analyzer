"""Stage 8 of M03: a code-grounded question ladder for each decision.

Every question
  * carries a `file:line` citation that must resolve in the snapshot (verify_citation);
  * quotes only the name or number the detector found, never a secret and never a code body;
  * comes with a gentler fallback that reframes instead of escalating.

Four depths: 0 recall ("how did you pick it"), 1 alternatives, 2 failure or edge case, 3 transfer to a new setting.
The wording is fixed templates. A model can be added later to phrase them, behind a gate like the one in the
behavioural demo; nothing here needs one.

Copy rule from the M03 plan: no student-facing string may contain plagiarism, copied, fake, authentic, original
or cheat. `copy_violations` checks that and the tests run it over every question the pipeline produces.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .decisions import Decision

BANNED = re.compile(r"(plagiar|copied|copying|fake|authentic|original|cheat|suspicious|fraud)", re.I)

LADDER: dict[str, list[str]] = {
    "hyperparameter": [
        "In {cite} you set {name} to {value}. How did you pick that value?",
        "What would you expect to change if {name} were half of that, or double?",
        "How could you tell from your results that {name} was too high or too low?",
        "If you moved this to a different dataset, how would you decide {name} from scratch?",
    ],
    "magic_constant": [
        "In {cite} the number {value} appears in {where}. What does it stand for, and why that value?",
        "What changes in the behaviour if that number is moved a little in either direction?",
        "Which input would make this number the wrong choice?",
        "How would you turn this number into something that can be set or tuned without editing the code?",
    ],
    "model_arch": [
        "In {cite}, {summary}. Why did you choose this structure?",
        "Which part would you remove first to see whether it matters, and what would you expect?",
        "How did you check that the model was not simply memorising the training data?",
        "If you had ten times less data, what would you change about this design?",
    ],
    "data_split": [
        "In {cite}, {summary}. Why split or seed it this way?",
        "What would go wrong if the same record appeared on both sides of the split?",
        "How did you check that your evaluation numbers were not leaking information?",
        "How would you split the data if it were ordered in time?",
    ],
    "error_handling": [
        "In {cite}, {summary}. What did you intend to happen when this fails?",
        "What would the user or the next function see after that failure?",
        "How would you find out in production that this error was happening?",
        "How would you redesign this so that a real failure is not hidden?",
    ],
    "concurrency": [
        "In {cite}, {summary}. Why did you choose to do this work concurrently?",
        "What shared state could two of these touch at the same time?",
        "How would you test for a race condition here?",
        "What would you change if this had to handle a hundred times more work?",
    ],
    "caching": [
        "In {cite}, {summary}. What made caching worth it here?",
        "When does a cached value become wrong, and how do you find out?",
        "What happens to memory if this runs for a week?",
        "How would you invalidate it when the underlying data changes?",
    ],
    "security": [
        "In {cite}, {summary}. What was the reason for doing it this way?",
        "What could someone do with this if they wanted to misuse the system?",
        "How would you check that this is safe before the code goes live?",
        "What would you change to remove the risk without losing the behaviour?",
    ],
    "schema_design": [
        "In {cite}, {summary}. How did you decide which fields belong together?",
        "Which of these fields would you index, and why?",
        "What would break if one of these fields were missing or duplicated?",
        "How would you change this design if the data grew a hundredfold?",
    ],
    "api_contract": [
        "In {cite}, {summary}. What does a client send and what does it get back?",
        "What happens if the client sends something unexpected?",
        "What happens if the same request is sent twice?",
        "How would you change this endpoint without breaking existing clients?",
    ],
    "algorithm_choice": [
        "In {cite}, {summary}. Why this approach and not a simpler one?",
        "What is the cost of this as the input gets larger?",
        "What input would make this slow or wrong?",
        "What would you use instead if the data no longer fit in memory?",
    ],
    "dependency_choice": [
        "In {cite}, {summary}. What made this library the right choice for that one use?",
        "What would it take to do that without the library?",
        "What does carrying this dependency cost the project?",
        "How would you decide when a library is worth adding?",
    ],
}
FALLBACK = {
    "hyperparameter": "No worries if you don't remember the exact reason. What would you check to decide it now?",
    "magic_constant": "No worries if you don't remember. How would you work out a sensible value from scratch?",
    "model_arch": "That is fine. Tell me how you would decide on the structure if you started again.",
    "data_split": "That is fine. What would you want to be true about a good split?",
    "error_handling": "No worries. What would you want to happen when something like this fails?",
    "security": "That is fine. What would you check first to make this safe?",
}
DEFAULT_FALLBACK = "No worries if the detail is gone. How would you approach deciding this today?"
EXPECTS = {
    "hyperparameter": ["tuning", "validation", "trade-offs"], "magic_constant": ["thresholds", "trade-offs"],
    "model_arch": ["architecture", "regularisation"], "data_split": ["leakage", "reproducibility"],
    "error_handling": ["failure modes", "logging"], "concurrency": ["race conditions", "scaling"],
    "caching": ["invalidation", "staleness"], "security": ["threats", "mitigations"],
    "schema_design": ["constraints", "indexes"], "api_contract": ["validation", "status codes"],
    "algorithm_choice": ["complexity", "alternatives"], "dependency_choice": ["alternatives", "cost"],
}


@dataclass
class RepoQuestion:
    decision_id: str
    depth: int
    question: str
    expects: list[str]
    citation: str
    fallback_if_unanswered: str

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def citation(d: Decision) -> str:
    return f"{d.path}:{d.line_start}"


def ladder(d: Decision) -> list[RepoQuestion]:
    templates = LADDER.get(d.kind)
    if not templates:
        return []
    facts = {"name": "this setting", "value": "that value", "where": "the code", "summary": d.summary,
             "cite": citation(d), **{k: str(v) for k, v in d.facts.items() if v is not None}}
    out = []
    for depth, t in enumerate(templates):
        q = t.format_map(_Safe(facts))
        out.append(RepoQuestion(d.id, depth, q, d.concepts or EXPECTS.get(d.kind, []), citation(d),
                                FALLBACK.get(d.kind, DEFAULT_FALLBACK)))
    return out


class _Safe(dict):
    def __missing__(self, key):
        return "that"


def copy_violations(text: str) -> list[str]:
    return sorted({m.group(0).lower() for m in BANNED.finditer(text)})


def verify_citation(root: Path, d: Decision) -> bool:
    """The cited line exists and is the first line of the decision's snippet (before redaction, compared loosely)."""
    try:
        lines = (Path(root) / d.path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    if not (1 <= d.line_start <= len(lines)):
        return False
    first = d.snippet.splitlines()[0].strip() if d.snippet else ""
    actual = lines[d.line_start - 1].strip()
    return actual == first or "[REDACTED]" in first


def questions_for(decisions: list[Decision], root: Path) -> tuple[list[RepoQuestion], list[str]]:
    """Depth-0..3 ladders for each decision. Returns (questions, reasons any decision was skipped)."""
    out: list[RepoQuestion] = []
    skipped: list[str] = []
    for d in decisions:
        if not verify_citation(root, d):
            skipped.append(f"{d.id}: citation does not resolve")
            continue
        qs = ladder(d)
        bad = [v for q in qs for v in copy_violations(q.question + " " + q.fallback_if_unanswered)]
        if bad or not qs:
            skipped.append(f"{d.id}: wording check failed ({', '.join(sorted(set(bad))) or 'no template'})")
            continue
        out += qs
    return out, skipped
