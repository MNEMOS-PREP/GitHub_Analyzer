"""Stage 7 of M03: find the decisions in the code that only the author can explain.

Deterministic detectors, no model. Python is read with `ast`; JavaScript, TypeScript, Java and Go with
tree-sitter (ts_detect.py), so a detector can tell a number inside a comparison from a number inside a string.
Other languages, and those four when tree-sitter is not installed, get a short list of regex detectors only
(hyperparameters, empty catch blocks, eval) and the result says so. Notebooks are not scanned for decisions.

A detector produces a Decision with a real file:line. Nothing here ranks or
judges; ranking is ranking.py, wording is questions.py.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from .inventory import CODE_EXT, is_test_path
from .secrets import redact

KINDS = ["magic_constant", "algorithm_choice", "model_arch", "hyperparameter", "schema_design", "concurrency",
         "error_handling", "caching", "security", "dependency_choice", "data_split", "api_contract"]
SMALL = {0, 1, -1, 2}
HYPER = {"lr", "learning_rate", "batch_size", "epochs", "num_epochs", "n_epochs", "n_estimators", "max_depth",
         "dropout", "temperature", "max_iter", "hidden_size", "n_neighbors", "weight_decay", "momentum", "top_k",
         "top_p", "patience", "num_layers", "embedding_dim"}
CONCURRENCY_CALLS = {"Thread", "Process", "Pool", "ThreadPoolExecutor", "ProcessPoolExecutor", "Lock", "RLock",
                     "Semaphore", "Queue", "create_task", "gather", "run_in_executor"}
WORKER_KWARGS = {"n_jobs", "num_workers", "max_workers"}
CACHE_DECORATORS = {"lru_cache", "cache", "cached", "cache_page"}
ALGO_CALLS = {"heappush", "heappop", "heapify", "nlargest", "nsmallest", "bisect", "bisect_left", "bisect_right", "deque"}
SCHEMA_BASES = {"Model", "Base", "BaseModel", "SQLModel", "DeclarativeBase"}
ROUTE_METHODS = {"route", "get", "post", "put", "delete", "patch"}
HEAVY = {"pandas", "numpy", "scipy", "tensorflow", "torch", "sklearn", "matplotlib", "nltk", "spacy"}
BENIGN_KWARGS = {"timeout", "port", "indent", "width", "height", "size", "figsize", "random_state", "test_size", "seed",
                 "verbose", "dpi", "fontsize", "linewidth", "n_jobs", "num_workers", "max_workers",
                 "ge", "le", "gt", "lt", "min_length", "max_length", "min_items", "max_items", "min_value", "max_value",
                 "status", "status_code", "code", "exit_code"}
MIN_SCHEMA_FIELDS = 6          # smaller models are data carriers, not design decisions
LOG_CALLS = {"info", "warning", "warn", "error", "debug", "exception", "critical", "print", "log"}
SEED_NAMES = {"seed", "manual_seed", "set_seed", "set_random_seed"}


@dataclass
class Decision:
    id: str
    path: str
    line_start: int
    line_end: int
    snippet: str
    kind: str
    summary: str
    facts: dict = field(default_factory=dict)
    concepts: list[str] = field(default_factory=list)
    detector: str = "ast"                 # "ast" or "regex" (reduced depth)
    authored_confidence: float | None = None
    interest_score: float = 0.0
    has_comment: bool = False
    reference_match: bool = False         # set aside by reference.py; never shown to the student

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _num(node) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        v = _num(node.operand)
        return None if v is None else -v
    return None


def _name(func) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _dotted(node) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _owner(func) -> str:
    if isinstance(func, ast.Attribute):
        try:
            return ast.unparse(func.value)
        except Exception:
            return ""
    return ""


def _has_comment(lines: list[str], lineno: int) -> bool:
    """A comment on the same line, or a comment of three or more words on the line above."""
    cur = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
    if re.search(r"#\s*\S", re.sub(r"(['\"]).*?\1", "", cur)):
        return True
    prev = lines[lineno - 2].strip() if lineno >= 2 else ""
    return prev.startswith("#") and len(prev.lstrip("# ").split()) >= 3


def _fmt(v) -> str:
    return str(int(v)) if isinstance(v, float) and v == int(v) and abs(v) >= 1 else str(v)


def detect_python(rel: str, src: str) -> list[Decision]:
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return []
    lines = src.splitlines()
    out: list[Decision] = []
    seen: set[tuple[str, int]] = set()

    def add(kind: str, node, summary: str, facts: dict, concepts: list[str]) -> None:
        start = getattr(node, "lineno", 1)
        end = getattr(node, "end_lineno", start) or start
        label = str(facts.get("name", ""))
        key = (kind, start, label)
        if key in seen:
            return
        seen.add(key)
        snippet = redact("\n".join(lines[start - 1:min(end, start + 4)]))
        out.append(Decision(f"{rel}:{start}:{kind}" + (f":{label}" if label else ""), rel, start, end, snippet, kind, summary, facts, concepts,
                            "ast", None, 0.0, _has_comment(lines, start)))

    # anything inside an f-string or a logging/print call is a message, not logic
    in_message: set[int] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.JoinedStr) or (isinstance(n, ast.Call) and _name(n.func) in LOG_CALLS):
            in_message.update(id(sub) for sub in ast.walk(n))

    heavy_alias: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in HEAVY:
                    heavy_alias[a.asname or a.name.split(".")[0]] = a.name.split(".")[0]
    heavy_uses: dict[str, list[ast.AST]] = {k: [] for k in heavy_alias}

    for node in ast.walk(tree):
        # ---- numbers in decisions: comparisons and slices -------------------------------------------------
        if isinstance(node, ast.Compare):
            if "status" in _dotted(node).lower():       # response.status_code >= 400 is a convention, not a choice
                continue
            for operand in [node.left, *node.comparators]:
                v = _num(operand)
                if v is not None and v not in SMALL and not _has_comment(lines, node.lineno):
                    add("magic_constant", node, f"the number {_fmt(v)} is used in a comparison",
                        {"value": _fmt(v), "where": "a comparison"}, ["thresholds", "trade-offs"])
        elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice) and id(node) not in in_message:
            for part in (node.slice.lower, node.slice.upper, node.slice.step):
                v = _num(part) if part is not None else None
                if v is not None and v not in SMALL and not _has_comment(lines, node.lineno):
                    add("magic_constant", node, f"the number {_fmt(v)} is used to slice",
                        {"value": _fmt(v), "where": "a slice"}, ["thresholds", "edge cases"])
        # ---- assignments of known hyperparameters ---------------------------------------------------------
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            v = _num(node.value) if node.value is not None else None
            for t in targets:
                tname = t.id if isinstance(t, ast.Name) else t.attr if isinstance(t, ast.Attribute) else ""
                if v is not None and tname.lower() in HYPER:
                    add("hyperparameter", node, f"{tname} is set to {_fmt(v)}", {"name": tname, "value": _fmt(v)},
                        ["tuning", "validation", "trade-offs"])
                if tname.lower().lstrip("_").endswith("cache") and isinstance(node.value, (ast.Dict, ast.Call)):
                    add("caching", node, f"{tname} is a cache", {"name": tname}, ["invalidation", "staleness", "memory"])
        # ---- classes: model architecture and schemas ------------------------------------------------------
        if isinstance(node, ast.ClassDef):
            bases = {_name(b) for b in node.bases}
            if any(_dotted(b).endswith(("nn.Module", "keras.Model")) for b in node.bases):
                layers = sum(1 for s in ast.walk(node) if isinstance(s, ast.Assign) and isinstance(s.value, ast.Call)
                             and any(isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self" for t in s.targets))
                add("model_arch", node, f"{node.name} is a neural network with {layers} layer{'s' if layers != 1 else ''} defined",
                    {"name": node.name, "layers": layers}, ["architecture", "depth", "regularisation"])
            if bases & SCHEMA_BASES:
                fields = sum(1 for s in node.body if isinstance(s, (ast.AnnAssign, ast.Assign)))
                if fields >= MIN_SCHEMA_FIELDS:
                    add("schema_design", node, f"{node.name} defines {fields} fields", {"name": node.name, "fields": fields},
                        ["normalisation", "indexes", "constraints"])
        # ---- functions: routes and cache decorators -------------------------------------------------------
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                d = dec.func if isinstance(dec, ast.Call) else dec
                dname = _name(d)
                if dname in CACHE_DECORATORS:
                    add("caching", node, f"{node.name} is cached with {dname}", {"name": node.name, "how": dname},
                        ["invalidation", "staleness", "memory"])
                if isinstance(dec, ast.Call) and dname in ROUTE_METHODS and dec.args and isinstance(dec.args[0], ast.Constant) \
                        and isinstance(dec.args[0].value, str):
                    methods = dname.upper() if dname != "route" else "".join(
                        ast.unparse(k.value) for k in dec.keywords if k.arg == "methods") or "GET"
                    add("api_contract", node, f"the route {dec.args[0].value} is handled by {node.name}",
                        {"route": dec.args[0].value, "methods": methods.replace("'", "").replace('"', "")},
                        ["validation", "status codes", "idempotency"])
        # ---- exception handling ----------------------------------------------------------------------------
        if isinstance(node, ast.ExceptHandler):
            swallowed = all(isinstance(s, (ast.Pass, ast.Continue)) or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
                            for s in node.body)
            if node.type is None:
                add("error_handling", node, "a bare except catches every error" + (" and ignores it" if swallowed else ""),
                    {"what": "a bare except", "swallowed": swallowed}, ["failure modes", "logging", "recovery"])
            elif swallowed:
                add("error_handling", node, f"an error of type {ast.unparse(node.type)} is caught and ignored",
                    {"what": f"a handler for {ast.unparse(node.type)}", "swallowed": True}, ["failure modes", "logging", "recovery"])
        # ---- calls -----------------------------------------------------------------------------------------
        if isinstance(node, ast.Call):
            fname, owner = _name(node.func), _owner(node.func)
            for kw in node.keywords:
                v = _num(kw.value)
                if kw.arg and kw.arg.lower() in HYPER and v is not None:
                    add("hyperparameter", node, f"{kw.arg} is set to {_fmt(v)}", {"name": kw.arg, "value": _fmt(v)},
                        ["tuning", "validation", "trade-offs"])
                elif kw.arg in WORKER_KWARGS and v is not None:
                    add("concurrency", node, f"{kw.arg} is set to {_fmt(v)}", {"what": f"{kw.arg} = {_fmt(v)}"},
                        ["parallelism", "contention", "scaling"])
                elif v is not None and kw.arg and v not in SMALL and kw.arg.lower() not in BENIGN_KWARGS and fname not in LOG_CALLS \
                        and not _has_comment(lines, node.lineno):
                    add("magic_constant", node, f"the number {_fmt(v)} is passed as {kw.arg}", {"value": _fmt(v), "where": f"the argument {kw.arg}"},
                        ["thresholds", "trade-offs"])
                if kw.arg == "random_state" and v is not None and fname != "train_test_split":
                    add("data_split", node, f"random_state is fixed at {_fmt(v)}", {"what": f"random_state = {_fmt(v)}"},
                        ["reproducibility", "leakage", "variance"])
                if kw.arg == "verify" and isinstance(kw.value, ast.Constant) and kw.value.value is False:
                    add("security", node, "certificate checking is turned off", {"what": "verify=False"}, ["threats", "mitigations"])
                if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                    add("security", node, "a command is run through the shell", {"what": "shell=True"}, ["injection", "validation"])
                if kw.arg == "debug" and isinstance(kw.value, ast.Constant) and kw.value.value is True and fname == "run":
                    add("security", node, "the app is started in debug mode", {"what": "debug=True"}, ["threats", "mitigations"])
            if fname == "train_test_split":
                ts = next((_num(k.value) for k in node.keywords if k.arg == "test_size"), None)
                add("data_split", node, "the data is split into train and test sets" + (f" with test_size {_fmt(ts)}" if ts is not None else "")
                    + (" and a fixed random_state" if any(k.arg == "random_state" for k in node.keywords) else ""),
                    {"what": "a train/test split", "test_size": None if ts is None else _fmt(ts),
                     "random_state": next((_fmt(_num(k.value)) for k in node.keywords if k.arg == "random_state" and _num(k.value) is not None), None)}, ["leakage", "reproducibility", "variance"])
            if fname in SEED_NAMES and node.args and _num(node.args[0]) is not None:
                add("data_split", node, f"a random seed is fixed at {_fmt(_num(node.args[0]))}",
                    {"what": f"a seed of {_fmt(_num(node.args[0]))}"}, ["reproducibility", "variance"])
            if fname == "Sequential" and node.args and isinstance(node.args[0], (ast.List, ast.Tuple)):
                n = len(node.args[0].elts)
                add("model_arch", node, f"a Sequential model stacks {n} layers", {"name": "a Sequential model", "layers": n},
                    ["architecture", "depth", "regularisation"])
            if fname in CONCURRENCY_CALLS:
                add("concurrency", node, f"{fname} is used", {"what": fname}, ["race conditions", "contention", "scaling"])
            if fname in ALGO_CALLS:
                add("algorithm_choice", node, f"{fname} is used for ordering or lookup", {"what": fname},
                    ["complexity", "alternatives", "data size"])
            if fname == "TTLCache" or fname == "LRUCache":
                add("caching", node, f"{fname} is created", {"name": fname}, ["invalidation", "staleness", "memory"])
            if isinstance(node.func, ast.Name) and node.func.id in ("eval", "exec"):
                add("security", node, f"{node.func.id}() runs text as code", {"what": f"{node.func.id}()"}, ["injection", "validation"])
            if fname in ("load", "loads") and owner in ("pickle", "cPickle", "dill"):
                add("security", node, "pickle is used to load data", {"what": "pickle.load"}, ["injection", "trust boundaries"])
            if fname == "load" and owner == "yaml" and not any(k.arg == "Loader" for k in node.keywords) and len(node.args) < 2:
                add("security", node, "yaml.load is called without a safe loader", {"what": "yaml.load"}, ["injection", "validation"])
            if fname == "system" and owner == "os":
                add("security", node, "a command is run with os.system", {"what": "os.system"}, ["injection", "validation"])
        if isinstance(node, ast.Name) and node.id in heavy_uses and isinstance(node.ctx, ast.Load):
            heavy_uses[node.id].append(node)

    for alias, uses in heavy_uses.items():
        if len(uses) == 1:
            lib = heavy_alias[alias]
            add("dependency_choice", uses[0], f"{lib} is imported but used once", {"name": lib},
                ["alternatives", "weight of dependencies"])
    return out


_HYPER_RX = re.compile(r"\b(" + "|".join(sorted(HYPER, key=len, reverse=True)) + r")\b\s*[:=]\s*(-?\d+(?:\.\d+)?)\b", re.I)
_EMPTY_CATCH = re.compile(r"catch\s*(\([^)]*\))?\s*\{\s*\}")
_EVAL = re.compile(r"(?<![\w.])eval\s*\(")


def detect_generic(rel: str, src: str) -> list[Decision]:
    """Reduced-depth detectors for languages we do not parse."""
    out: list[Decision] = []
    lines = src.splitlines()
    for n, line in enumerate(lines, 1):
        m = _HYPER_RX.search(line)
        if m and not line.lstrip().startswith(("//", "#", "*")):
            out.append(Decision(f"{rel}:{n}:hyperparameter", rel, n, n, redact(line.strip()), "hyperparameter",
                                f"{m.group(1)} is set to {m.group(2)}", {"name": m.group(1), "value": m.group(2)},
                                ["tuning", "validation", "trade-offs"], "regex", None, 0.0, "//" in line))
        if _EMPTY_CATCH.search(line):
            out.append(Decision(f"{rel}:{n}:error_handling", rel, n, n, redact(line.strip()), "error_handling",
                                "an error is caught and ignored", {"what": "an empty catch block", "swallowed": True},
                                ["failure modes", "logging", "recovery"], "regex", None, 0.0, False))
        if _EVAL.search(line) and not line.lstrip().startswith(("//", "#", "*")):
            out.append(Decision(f"{rel}:{n}:security", rel, n, n, redact(line.strip()), "security",
                                "eval() runs text as code", {"what": "eval()"}, ["injection", "validation"], "regex", None, 0.0, False))
    return out


SKIP_FILES = re.compile(r"(^|/)(seed|fixtures?|conftest|mock_?data|sample_?data)[^/]*$", re.I)


def detect_repo(root: Path, analysed: list[str]) -> list[Decision]:
    """All decisions in the analysed files. Test files are skipped: nobody is asked about their tests' constants."""
    out: list[Decision] = []
    for rel in analysed:
        ext = Path(rel).suffix.lower()
        if ext in (".ipynb", ".html", ".css", ".scss", ".sql", ".sh") or is_test_path(rel) or SKIP_FILES.search(rel) or ext not in CODE_EXT:
            continue
        try:
            src = (Path(root) / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if ext == ".py":
            out += detect_python(rel, src)
            continue
        from . import ts_detect                   # imported here: ts_detect imports this module
        lang = ts_detect.EXT_LANG.get(ext)
        if lang and ts_detect.available():
            out += ts_detect.detect(rel, src, lang)
        else:
            out += detect_generic(rel, src)
    return out
