"""Decision detectors for JavaScript, TypeScript, Java and Go, read with tree-sitter.

Same contract as `decisions.detect_python`: a Decision with a real file:line, found by walking a syntax tree, so a
number inside a comparison is told apart from one inside a string or a log call. Nothing here ranks or judges.

tree-sitter is optional. If the grammars are not installed, `available()` is False and `decisions.detect_repo` falls
back to the short regex list (and the output says it read those files with reduced depth).
"""

from __future__ import annotations

import re

from .decisions import HYPER, KINDS, MIN_SCHEMA_FIELDS, SMALL, Decision
from .secrets import redact

try:
    from tree_sitter import Language, Parser
    import tree_sitter_go
    import tree_sitter_java
    import tree_sitter_javascript
    import tree_sitter_typescript
    _LANGS = {
        "js": Language(tree_sitter_javascript.language()),
        "ts": Language(tree_sitter_typescript.language_typescript()),
        "tsx": Language(tree_sitter_typescript.language_tsx()),
        "java": Language(tree_sitter_java.language()),
        "go": Language(tree_sitter_go.language()),
    }
    _OK = True
except Exception:                                    # not installed, or an ABI mismatch: fall back to regex
    _LANGS, _OK = {}, False

EXT_LANG = {".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js", ".ts": "ts", ".tsx": "tsx", ".java": "java", ".go": "go"}
LABEL = {"js": "JavaScript", "ts": "TypeScript", "tsx": "TypeScript", "java": "Java", "go": "Go"}

NUMBER = {"number", "decimal_integer_literal", "decimal_floating_point_literal", "hex_integer_literal", "int_literal",
          "float_literal", "octal_integer_literal", "binary_integer_literal"}
COMPARE_OPS = {"<", ">", "<=", ">=", "==", "===", "!=", "!=="}
LOG_NAMES = {"log", "info", "warn", "warning", "error", "debug", "trace", "println", "print", "printf", "printf",
             "fatal", "fatalf", "panic", "errorf", "sprintf", "infof", "warnf", "debugf", "format", "write"}
LOG_OWNERS = {"console", "logger", "log", "logging", "slf4j", "system.out", "system.err", "fmt", "t"}
SLICE_CALLS = {"slice", "substring", "substr", "splice", "limit", "skip", "take", "subList", "truncate"}
CONCURRENCY_NEW = {"Worker", "Thread", "ReentrantLock", "Semaphore", "CountDownLatch", "ConcurrentHashMap", "WaitGroup"}
CONCURRENCY_CALLS = {"newFixedThreadPool", "newCachedThreadPool", "newWorkStealingPool", "newScheduledThreadPool",
                     "newVirtualThreadPerTaskExecutor", "supplyAsync", "runAsync", "parallelStream", "Promise.all",
                     "Promise.allSettled", "Promise.race", "setInterval"}
CACHE_CALLS = {"useMemo", "memoize", "memo", "lru", "LRU", "NewCache", "unstable_cache"}
CACHE_ANNOTATIONS = {"Cacheable", "CacheEvict", "CachePut", "EnableCaching"}
ROUTE_VERBS = {"get", "post", "put", "delete", "patch", "all", "use", "GET", "POST", "PUT", "DELETE", "PATCH", "Handle",
               "HandleFunc", "Get", "Post", "Put", "Delete", "Patch"}
ROUTE_ANNOTATIONS = {"GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT", "DeleteMapping": "DELETE",
                     "PatchMapping": "PATCH", "RequestMapping": "ANY"}
ROUTE_OWNERS = re.compile(r"(^|\.)(app|router|server|routes?|r|e|mux|engine|group|v\d|http|fastify|express)\b", re.I)
HANDLER_NODES = {"arrow_function", "function_expression", "function", "identifier", "member_expression", "func_literal",
                 "selector_expression"}
CLOCK_NAMES = {"h", "hr", "hrs", "hour", "hours", "min", "mins", "minute", "minutes", "sec", "secs", "second", "seconds",
               "day", "days", "month", "weekday", "year", "getHours", "getMinutes", "getDay"}
ISO_SLICES = {10, 19}                     # "2026-10-05" and "2026-10-05T10:00:00": the date idiom, not a choice
SCHEMA_ANNOTATIONS = {"Entity", "Table", "Document", "MappedSuperclass"}
ALGO_NEW = {"PriorityQueue", "TreeMap", "TreeSet", "LinkedHashMap", "ArrayDeque", "BitSet"}
ALGO_CALLS = {"binarySearch", "Push", "Pop", "Fix", "Slice", "SliceStable", "Search", "SearchInts", "sortBy", "orderBy"}
SECURITY_CALLS = {"eval": "eval() runs text as code", "Function": "new Function() runs text as code",
                  "exec": "a command is run through the shell", "execSync": "a command is run through the shell",
                  "readObject": "Java deserialisation is used on input"}
SECURITY_PAIRS = {"rejectUnauthorized": ("false", "certificate checking is turned off"),
                  "InsecureSkipVerify": ("true", "certificate checking is turned off"),
                  "dangerouslySetInnerHTML": (None, "raw HTML is injected into the page"),
                  "strictSSL": ("false", "certificate checking is turned off")}
HYPER_L = {h.lower() for h in HYPER} | {h.lower().replace("_", "") for h in HYPER} | \
    {"learningrate", "batchsize", "numepochs", "nestimators", "maxdepth", "weightdecay", "topk", "topp", "numlayers",
     "hiddensize", "embeddingdim", "nneighbors", "maxiter"}
CONST_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")


def available() -> bool:
    return _OK


def _txt(n) -> str:
    return n.text.decode("utf-8", "replace") if n is not None else ""


def _num_of(n) -> float | None:
    """The numeric value of a literal node (with an optional leading minus), else None."""
    if n is None:
        return None
    if n.type in ("unary_expression", "unary_op_expression") and _txt(n).startswith("-") and n.named_children:
        v = _num_of(n.named_children[-1])
        return None if v is None else -v
    if n.type == "parenthesized_expression" and n.named_children:
        return _num_of(n.named_children[0])
    if n.type not in NUMBER:
        return None
    t = _txt(n).replace("_", "").rstrip("fFdDlL")
    try:
        return float(int(t, 16)) if t.lower().startswith("0x") else float(t)
    except ValueError:
        return None


def _fmt(v: float) -> str:
    return str(int(v)) if v == int(v) and abs(v) >= 1 else str(v)


def _has_comment(lines: list[str], lineno: int) -> bool:
    cur = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
    stripped = re.sub(r"(['\"`]).*?\1", "", cur)
    if re.search(r"//\s*\S|/\*", stripped):
        return True
    prev = lines[lineno - 2].strip() if lineno >= 2 else ""
    return prev.startswith(("//", "*", "/*")) and len(prev.lstrip("/* ").split()) >= 3


def _callee(call) -> tuple[str, str]:
    """(owner text, name) of a call_expression / method_invocation / object_creation_expression."""
    t = call.type
    if t == "method_invocation":
        return _txt(call.child_by_field_name("object")), _txt(call.child_by_field_name("name"))
    if t in ("object_creation_expression", "new_expression"):
        ty = call.child_by_field_name("type") or call.child_by_field_name("constructor")
        full = _txt(ty)
        full = full.split("<")[0]
        return ".".join(full.split(".")[:-1]), full.split(".")[-1]
    fn = call.child_by_field_name("function")
    if fn is None:
        return "", ""
    if fn.type in ("member_expression", "selector_expression"):
        prop = fn.child_by_field_name("property") or fn.child_by_field_name("field")
        obj = fn.child_by_field_name("object") or fn.child_by_field_name("operand")
        return _txt(obj), _txt(prop)
    return "", _txt(fn)


def _args(call):
    a = call.child_by_field_name("arguments")
    return list(a.named_children) if a is not None else []


def _in_message(node) -> bool:
    """True when the node sits inside a log/print call or a template string: a message, not logic."""
    p = node
    while p is not None:
        if p.type in ("template_string", "interpreted_string_literal"):
            return True
        if p.type in ("call_expression", "method_invocation"):
            owner, name = _callee(p)
            if name.lower() in LOG_NAMES or owner.lower() in LOG_OWNERS:
                return True
        p = p.parent
    return False


def _string_of(n) -> str | None:
    if n is not None and n.type in ("string", "string_literal", "interpreted_string_literal", "raw_string_literal",
                                    "template_string"):
        return _txt(n).strip("\"'`")
    return None


def _walk(node):
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


def detect(rel: str, src: str, lang: str) -> list[Decision]:
    if not _OK or lang not in _LANGS:
        return []
    try:
        tree = Parser(_LANGS[lang]).parse(src.encode("utf-8"))
    except Exception:
        return []
    lines = src.splitlines()
    out: list[Decision] = []
    seen: set[tuple[str, int, str]] = set()

    def add(kind: str, node, summary: str, facts: dict, concepts: list[str]) -> None:
        assert kind in KINDS
        start, end = node.start_point[0] + 1, node.end_point[0] + 1
        label = str(facts.get("name", ""))
        if (kind, start, label) in seen:
            return
        seen.add((kind, start, label))
        snippet = redact("\n".join(lines[start - 1:min(end, start + 4)]))
        out.append(Decision(f"{rel}:{start}:{kind}" + (f":{label}" if label else ""), rel, start, end, snippet, kind,
                            summary, facts, concepts, "tree-sitter", None, 0.0, _has_comment(lines, start)))

    for n in _walk(tree.root_node):
        t = n.type
        line = n.start_point[0] + 1

        # ---- numbers in comparisons --------------------------------------------------------------------------
        if t == "binary_expression":
            op = _txt(n.child_by_field_name("operator"))
            if op in COMPARE_OPS and "status" not in _txt(n).lower():
                for side, other in ((n.child_by_field_name("left"), n.child_by_field_name("right")),
                                    (n.child_by_field_name("right"), n.child_by_field_name("left"))):
                    v = _num_of(side)
                    if _txt(other).split(".")[-1].rstrip("()") in CLOCK_NAMES:      # a greeting by the hour is not a threshold
                        continue
                    if v is not None and v not in SMALL and not _has_comment(lines, line) and not _in_message(n):
                        add("magic_constant", n, f"the number {_fmt(v)} is used in a comparison",
                            {"value": _fmt(v), "where": "a comparison"}, ["thresholds", "trade-offs"])

        # ---- assignments of known hyperparameters, and named constants ---------------------------------------
        name = value = None
        if t in ("variable_declarator", "var_spec", "const_spec"):
            name, value = _txt(n.child_by_field_name("name")), n.child_by_field_name("value")
        elif t == "assignment_expression":
            left = n.child_by_field_name("left")
            name = _txt(left.child_by_field_name("property")) if left is not None and left.type == "member_expression" \
                else _txt(left)
            value = n.child_by_field_name("right")
        elif t == "pair":
            name, value = _txt(n.child_by_field_name("key")), n.child_by_field_name("value")
        elif t in ("short_var_declaration", "assignment_statement"):
            lhs, rhs = n.child_by_field_name("left"), n.child_by_field_name("right")
            if lhs is not None and rhs is not None and lhs.named_children and rhs.named_children:
                name, value = _txt(lhs.named_children[0]), rhs.named_children[0]
        elif t == "keyed_element" and len(n.named_children) >= 2:
            name, value = _txt(n.named_children[0]), n.named_children[-1]
            if value.type == "literal_element" and value.named_children:
                value = value.named_children[0]
        if name and value is not None:
            key = name.replace("_", "").lower()
            v = _num_of(value)
            if v is not None and (key in HYPER_L or name.lower() in HYPER_L):
                add("hyperparameter", n, f"{name} is set to {_fmt(v)}", {"name": name, "value": _fmt(v)},
                    ["tuning", "validation", "trade-offs"])
            elif v is not None and CONST_NAME.match(name) and v not in SMALL and not _has_comment(lines, line):
                add("magic_constant", n, f"{name} is set to {_fmt(v)}", {"name": name, "value": _fmt(v)},
                    ["thresholds", "trade-offs"])
            if key.endswith("cache") and value.type in ("object", "new_expression", "object_creation_expression",
                                                        "composite_literal", "call_expression"):
                add("caching", n, f"{name} is a cache", {"name": name}, ["invalidation", "staleness", "memory"])
            vl = _txt(value)
            for sec, (bad, why) in SECURITY_PAIRS.items():
                if name == sec and (bad is None or vl == bad):
                    add("security", n, why, {"what": f"{sec}: {vl}"}, ["threats", "mitigations"])
            if name.endswith("innerHTML"):
                add("security", n, "raw HTML is injected into the page", {"what": "innerHTML"}, ["injection", "validation"])
        if t == "jsx_attribute" and _txt(n.named_children[0] if n.named_children else None) == "dangerouslySetInnerHTML":
            add("security", n, "raw HTML is injected into the page", {"what": "dangerouslySetInnerHTML"},
                ["injection", "validation"])

        # ---- error handling ----------------------------------------------------------------------------------
        if t == "catch_clause":
            body = n.child_by_field_name("body")
            if body is not None and not body.named_children:
                add("error_handling", n, "an error is caught and ignored", {"what": "an empty catch block", "swallowed": True},
                    ["failure modes", "logging", "recovery"])
        elif t == "if_statement" and lang == "go":
            cond, cons = n.child_by_field_name("condition"), n.child_by_field_name("consequence")
            if cond is not None and re.fullmatch(r"\s*err\s*!=\s*nil\s*", _txt(cond)) and cons is not None \
                    and not cons.named_children:
                add("error_handling", n, "an error is checked and ignored", {"what": "an empty if err != nil", "swallowed": True},
                    ["failure modes", "logging", "recovery"])
        elif t == "short_var_declaration" and lang == "go":
            lhs = n.child_by_field_name("left")
            if lhs is not None and re.search(r",\s*_$", _txt(lhs)) and _txt(n.child_by_field_name("right")).count("(") \
                    and re.search(r"\b(Open|Read|Write|Unmarshal|Marshal|Parse\w*|Atoi|Exec|Query\w*|Decode|Encode)\b", _txt(n)):
                add("error_handling", n, "the error from a call is discarded", {"what": "an error assigned to _", "swallowed": True},
                    ["failure modes", "logging", "recovery"])

        # ---- calls and constructions -------------------------------------------------------------------------
        if t in ("call_expression", "method_invocation", "object_creation_expression", "new_expression"):
            owner, fname = _callee(n)
            args = _args(n)
            full = f"{owner}.{fname}" if owner else fname
            if fname in SLICE_CALLS and not _in_message(n):
                for a in args:
                    v = _num_of(a)
                    if v is not None and v not in SMALL and v not in ISO_SLICES and not _has_comment(lines, line):
                        add("magic_constant", n, f"the number {_fmt(v)} is used to slice", {"value": _fmt(v), "where": "a slice"},
                            ["thresholds", "edge cases"])
                        break
            if fname in CONCURRENCY_NEW and t in ("object_creation_expression", "new_expression") or \
                    fname in CONCURRENCY_CALLS or full in CONCURRENCY_CALLS:
                n_threads = next((_fmt(_num_of(a)) for a in args if _num_of(a) is not None), None)
                add("concurrency", n, f"{full} is used" + (f" with {n_threads}" if n_threads else ""), {"what": full},
                    ["race conditions", "contention", "scaling"])
            if lang == "go" and (fname in ("Lock", "RLock") or (fname == "make" and args and _txt(args[0]).startswith("chan"))):
                add("concurrency", n, "a lock or channel guards shared state" if fname != "make" else "a channel is created",
                    {"what": full}, ["race conditions", "contention", "scaling"])
            if fname in CACHE_CALLS:
                add("caching", n, f"{fname} is used to cache a result", {"name": fname}, ["invalidation", "staleness", "memory"])
            if (fname in ALGO_NEW and t == "object_creation_expression") or (
                    fname in ALGO_CALLS and owner in ("sort", "heap", "Collections", "Arrays", "slices", "_", "lodash")):
                add("algorithm_choice", n, f"{fname} is used for ordering or lookup", {"what": fname},
                    ["complexity", "alternatives", "data size"])
            if fname in SECURITY_CALLS and not (fname == "exec" and owner.lower().endswith(("regex", "re", "pattern"))) \
                    and not (fname == "Function" and t == "call_expression"):
                add("security", n, SECURITY_CALLS[fname], {"what": f"{fname}()"}, ["injection", "validation"])
            if lang == "go" and full == "exec.Command" and args and _string_of(args[0]) in ("sh", "bash", "cmd"):
                add("security", n, "a command is run through the shell", {"what": "exec.Command(sh)"}, ["injection", "validation"])
            if lang == "java" and full.endswith("getRuntime().exec"):
                add("security", n, "a command is run through the shell", {"what": "Runtime.exec"}, ["injection", "validation"])
            # routes: app.get('/x', ...), http.HandleFunc("/x", ...), r.GET("/x", ...)
            if fname in ROUTE_VERBS and args and (ROUTE_OWNERS.search(owner) or lang == "go" and owner):
                route = _string_of(args[0]) if args[0].type != "template_string" else None
                has_handler = any(a.type in HANDLER_NODES for a in args[1:])      # a client call has a body or options, not a handler
                if route is not None and route.startswith("/") and has_handler:
                    add("api_contract", n, f"the route {route} is handled in code", {"route": route, "methods": fname.upper()},
                        ["validation", "status codes", "idempotency"])

        # ---- Java annotations: routes, caches, entities ------------------------------------------------------
        if t in ("annotation", "marker_annotation"):
            an = _txt(n.child_by_field_name("name"))
            if an in ROUTE_ANNOTATIONS:
                lit = next((_string_of(c) for c in _walk(n) if c.type == "string_literal"), None)
                add("api_contract", n, f"the route {lit or '(class level)'} is declared with @{an}",
                    {"route": lit or "/", "methods": ROUTE_ANNOTATIONS[an]}, ["validation", "status codes", "idempotency"])
            if an in CACHE_ANNOTATIONS:
                add("caching", n, f"@{an} caches a result", {"name": an}, ["invalidation", "staleness", "memory"])
            if an == "Transactional":
                add("concurrency", n, "@Transactional sets a transaction boundary", {"what": "@Transactional"},
                    ["isolation", "rollback", "contention"])
        if t == "synchronized_statement":
            add("concurrency", n, "a synchronized block guards shared state", {"what": "synchronized"},
                ["race conditions", "contention", "scaling"])
        if t == "go_statement":
            add("concurrency", n, "a goroutine is started", {"what": "go"}, ["race conditions", "contention", "scaling"])

        # ---- schema design: Java entities, Go structs with tags, Mongoose-style schemas ----------------------
        if t == "class_declaration" and lang == "java":
            mods = next((c for c in n.children if c.type == "modifiers"), None)
            ann = {_txt(a.child_by_field_name("name")) for a in (mods.named_children if mods else [])
                   if a.type in ("annotation", "marker_annotation")}
            body = n.child_by_field_name("body")
            nfields = sum(1 for c in (body.named_children if body else []) if c.type == "field_declaration")
            if ann & SCHEMA_ANNOTATIONS and nfields >= MIN_SCHEMA_FIELDS:
                cname = _txt(n.child_by_field_name("name"))
                add("schema_design", n, f"{cname} defines {nfields} fields", {"name": cname, "fields": nfields},
                    ["normalisation", "indexes", "constraints"])
        if t == "type_spec" and lang == "go":
            st = n.child_by_field_name("type")
            if st is not None and st.type == "struct_type":
                tagged = [f for f in _walk(st) if f.type == "field_declaration" and re.search(r"`[^`]*(db|gorm|bson|json):", _txt(f))]
                if len(tagged) >= MIN_SCHEMA_FIELDS:
                    cname = _txt(n.child_by_field_name("name"))
                    add("schema_design", n, f"{cname} defines {len(tagged)} stored fields", {"name": cname, "fields": len(tagged)},
                        ["normalisation", "indexes", "constraints"])
        if t == "new_expression" and _txt(n.child_by_field_name("constructor")).endswith("Schema") and _args(n) \
                and _args(n)[0].type == "object":
            nfields = len([c for c in _args(n)[0].named_children if c.type == "pair"])
            if nfields >= MIN_SCHEMA_FIELDS:
                add("schema_design", n, f"a schema defines {nfields} fields", {"name": "Schema", "fields": nfields},
                    ["normalisation", "indexes", "constraints"])

        # ---- a TensorFlow.js model ---------------------------------------------------------------------------
        if t == "call_expression" and _callee(n) == ("tf", "sequential"):
            layers = sum(1 for c in _walk(n) if c.type == "call_expression" and _callee(c)[1] == "dense"
                         or c.type == "call_expression" and _callee(c)[0] == "tf.layers")
            if layers:
                add("model_arch", n, f"a tf.js model stacks {layers} layers", {"name": "a tf.js model", "layers": layers},
                    ["architecture", "depth", "regularisation"])
    return out
