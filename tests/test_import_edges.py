from gh_analyzer import modgraph, ranking


def _write(root, files):
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return sorted(files)


def test_python_edges_relative_and_absolute_imports(tmp_path):
    analysed = _write(tmp_path, {
        "a.py": "import pkg.b\n",
        "pkg/b.py": "X = 1\n",
        "pkg/c.py": "from . import b\n",
    })
    assert ranking.import_edges(tmp_path, analysed) == {("a.py", "pkg/b.py"), ("pkg/c.py", "pkg/b.py")}


def test_js_edges_resolve_only_local(tmp_path):
    analysed = _write(tmp_path, {
        "src/app.js": 'import x from "./util"\nimport "react"\n',
        "src/util.js": "export default 1\n",
    })
    assert modgraph.edges(tmp_path, analysed) == {("src/app.js", "src/util.js")}


def test_in_degree_unchanged(tmp_path):
    analysed = _write(tmp_path, {
        "a.py": "import b\nimport c\n",
        "b.py": "import c\n",
        "c.py": "",
        "web/app.js": 'import u from "./u"\n',
        "web/u.js": "",
    })
    edges = ranking.import_edges(tmp_path, analysed) | modgraph.edges(tmp_path, analysed)
    expected = {p: 0 for p in analysed}
    for _, imported in edges:
        expected[imported] += 1
    assert ranking.import_graph(tmp_path, analysed) == expected
    assert expected["c.py"] == 2 and expected["web/u.js"] == 1


def test_syntax_error_file_yields_no_edge(tmp_path):
    analysed = _write(tmp_path, {"bad.py": "import good\ndef (:\n", "good.py": ""})
    assert ranking.import_edges(tmp_path, analysed) == set()
