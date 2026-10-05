"""JavaScript, TypeScript, Java and Go are read with tree-sitter; every decision cites a real line."""

import pytest

from gh_analyzer import decisions, modgraph, ts_detect

pytestmark = pytest.mark.skipif(not ts_detect.available(), reason="tree-sitter grammars not installed")

JS = """const express = require('express');
const app = express();
const cache = new Map();
const MAX_RETRIES = 7;
const config = { learning_rate: 0.01, batchSize: 64, rejectUnauthorized: false };
app.get('/api/users', async (req, res) => {
  try {
    if (req.body.items.length > 50) { console.log("too many " + 50); }
    const top = items.slice(0, 20);
    await Promise.all(tasks);
  } catch (e) {}
  res.send(eval(req.query.x));
});
"""
JAVA = """@RestController
class B {
  @GetMapping("/items")
  @Cacheable("items")
  public List<Item> list() {
    ExecutorService ex = Executors.newFixedThreadPool(8);
    PriorityQueue<Integer> pq = new PriorityQueue<>();
    try { run(); } catch (Exception e) { }
    if (count > 100) { return null; }
    synchronized (this) { x++; }
  }
}
"""
GO = """package main
func main() {
	r := gin.Default()
	r.GET("/ping", h)
	go work()
	cfg := &tls.Config{InsecureSkipVerify: true}
	epochs := 30
	if err != nil {
	}
	if n > 250 { }
	x, _ := strconv.Atoi("1")
}
"""


def found(src, lang):
    return {(d.kind, d.line_start) for d in ts_detect.detect("f", src, lang)}


def test_javascript_decisions():
    f = found(JS, "js")
    assert ("hyperparameter", 5) in f and ("caching", 3) in f and ("api_contract", 6) in f
    assert ("magic_constant", 8) in f and ("magic_constant", 9) in f
    assert ("error_handling", 11) in f and ("security", 12) in f and ("concurrency", 10) in f


def test_a_number_in_a_log_call_or_string_is_not_a_decision():
    src = 'console.log("retrying", 50);\nconst s = `limit is ${n > 99}`;\nlogger.info("x", 777);\n'
    assert not any(k == "magic_constant" for k, _ in found(src, "js"))


def test_status_comparisons_and_commented_numbers_are_ignored():
    src = "if (res.status >= 400) {}\nif (n > 30) {} // 30 is the page size\n"
    assert not found(src, "js")


def test_java_decisions():
    f = found(JAVA, "java")
    assert {("api_contract", 3), ("caching", 4), ("concurrency", 6), ("algorithm_choice", 7), ("error_handling", 8),
            ("magic_constant", 9), ("concurrency", 10)} <= f


def test_go_decisions():
    f = found(GO, "go")
    assert {("api_contract", 4), ("concurrency", 5), ("security", 6), ("hyperparameter", 7), ("error_handling", 8),
            ("magic_constant", 10), ("error_handling", 11)} <= f


def test_typescript_and_tsx_parse():
    assert ("hyperparameter", 1) in found("const lr: number = 0.1;\n", "ts")
    assert ("security", 1) in found("const a = <div dangerouslySetInnerHTML={{__html: x}} />;\n", "tsx")


def test_every_decision_cites_a_real_line():
    for src, lang in ((JS, "js"), (JAVA, "java"), (GO, "go")):
        n = len(src.splitlines())
        for d in ts_detect.detect("f", src, lang):
            assert 1 <= d.line_start <= d.line_end <= n and d.snippet and d.detector == "tree-sitter"


def test_broken_source_returns_nothing_not_an_error():
    assert isinstance(ts_detect.detect("f", "const = = (((", "js"), list)


def test_detect_repo_uses_tree_sitter_for_these_languages(tmp_path):
    (tmp_path / "a.js").write_text(JS, encoding="utf-8")
    ds = decisions.detect_repo(tmp_path, ["a.js"])
    assert ds and all(d.detector == "tree-sitter" for d in ds)


def test_import_graph_for_js_java_go(tmp_path):
    def w(rel, text):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    w("src/a.ts", "import {x} from './util/h';\nconst y = require('./util');\n")
    w("src/b.ts", "import x from './util/h.ts';\nimport React from 'react';\n")
    w("src/util/h.ts", "export const x = 1;\n")
    w("src/util/index.ts", "export {};\n")
    w("j/com/acme/Repo.java", "package com.acme;\npublic class Repo {}\n")
    w("j/com/acme/Svc.java", "package com.acme;\nimport java.util.List;\npublic class Svc { Repo r; }\n")
    w("j/com/acme/web/Ctl.java", "package com.acme.web;\nimport com.acme.Repo;\nimport com.acme.Svc;\nclass Ctl {}\n")
    w("db/db.go", "package db\nfunc Open() {}\n")
    w("cmd/main.go", 'package main\nimport (\n  "fmt"\n  "example.com/app/db"\n)\n')
    files = ["src/a.ts", "src/b.ts", "src/util/h.ts", "src/util/index.ts", "j/com/acme/Repo.java", "j/com/acme/Svc.java",
             "j/com/acme/web/Ctl.java", "db/db.go", "cmd/main.go"]
    g = modgraph.in_degree(tmp_path, files)
    assert g["src/util/h.ts"] == 2 and g["src/util/index.ts"] == 1 and g["src/a.ts"] == 0
    assert g["j/com/acme/Repo.java"] == 2 and g["j/com/acme/Svc.java"] == 1
    assert g["db/db.go"] == 1 and g["cmd/main.go"] == 0


def test_importing_a_stylesheet_or_json_file_does_not_break_the_graph(tmp_path):
    (tmp_path / "a.ts").write_text("import './theme.css';\nimport data from './data.json';\n", encoding="utf-8")
    (tmp_path / "theme.css").write_text("a {}\n", encoding="utf-8")
    (tmp_path / "data.json").write_text("{}\n", encoding="utf-8")
    assert modgraph.in_degree(tmp_path, ["a.ts", "theme.css", "data.json"]) == {"a.ts": 0}


def test_a_client_call_is_not_a_route_but_a_server_handler_is():
    client = "const r = await api.get(`/v1/companies?q=${q}`);\nconst s = await api.post('/v1/x', payload);\n"
    server = "app.get('/v1/x', (req, res) => res.send(1));\nrouter.post('/y', handler);\n"
    assert not any(k == "api_contract" for k, _ in found(client, "ts"))
    assert sum(k == "api_contract" for k, _ in found(server, "js")) == 2


def test_clock_hours_and_iso_date_slices_are_not_decisions():
    src = "if (h < 12) return 'a';\nif (hour >= 17) return 'b';\nconst d = x.slice(0, 10);\n"
    assert not found(src, "ts")
