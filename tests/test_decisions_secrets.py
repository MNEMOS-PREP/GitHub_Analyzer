import pytest

from gh_analyzer import decisions, ranking, secrets
from gh_analyzer.decisions import detect_generic, detect_python


def kinds(src):
    return [(d.kind, d.line_start) for d in detect_python("m.py", src)]


def of_kind(src, kind):
    return [d for d in detect_python("m.py", src) if d.kind == kind]


# ── magic constants ───────────────────────────────────────────────────────

def test_a_bare_number_in_a_comparison_is_a_decision():
    src = "def f(p):\n    if p > 0.7:\n        return 1\n"
    d = of_kind(src, "magic_constant")
    assert len(d) == 1 and d[0].line_start == 2 and d[0].facts["value"] == "0.7"


@pytest.mark.parametrize("src", [
    "if x > 1: pass\n", "if x == 0: pass\n", "if x != -1: pass\n", "if x % 2 == 0: pass\n",     # trivial numbers
    "THRESHOLD = 0.7\nif p > THRESHOLD: pass\n",                                                # a named constant
    "if p > 0.7:  # chosen on the validation set\n    pass\n",                                   # explained on the line
    "# the cutoff comes from the validation curve\nif p > 0.7:\n    pass\n",                      # explained above
    "s = 'if p > 0.7'\n",                                                                        # inside a string
])
def test_things_that_are_not_magic_constants_are_left_alone(src):
    assert of_kind(src, "magic_constant") == []


def test_slices_and_keyword_arguments_count():
    assert of_kind("y = xs[:37]\n", "magic_constant")
    assert of_kind("clf = Thing(window=45)\n", "magic_constant")
    assert of_kind("plt.figure(figsize=45)\n", "magic_constant") == []


# ── hyperparameters, splits, models ───────────────────────────────────────

def test_hyperparameters_as_kwargs_and_assignments():
    src = "model.fit(x, y, epochs=30, batch_size=64)\nlr = 0.001\nBATCH_SIZE = 32\nname = 'lr'\n"
    found = {d.facts["name"]: d.facts["value"] for d in of_kind(src, "hyperparameter")}
    assert found == {"epochs": "30", "batch_size": "64", "lr": "0.001", "BATCH_SIZE": "32"}


def test_data_split_and_seed():
    src = "a = train_test_split(X, y, test_size=0.2, random_state=42)\nnp.random.seed(7)\ntorch.manual_seed(3)\n"
    d = of_kind(src, "data_split")
    assert len(d) >= 3 and any(x.facts.get("test_size") == "0.2" for x in d)


def test_model_architecture_pytorch_keras_and_not_django():
    torch_src = "class Net(nn.Module):\n    def __init__(self):\n        super().__init__()\n        self.a = nn.Linear(3, 4)\n        self.b = nn.Linear(4, 1)\n"
    keras_src = "m = Sequential([Dense(8), Dense(4), Dense(1)])\n"
    django_src = "class Post(models.Model):\n" + "".join(f"    f{i} = models.CharField()\n" for i in range(6))
    assert of_kind(torch_src, "model_arch")[0].facts["layers"] == 2
    assert of_kind(keras_src, "model_arch")[0].facts["layers"] == 3
    assert of_kind(django_src, "model_arch") == []
    assert of_kind(django_src, "schema_design")[0].facts["fields"] == 6


# ── error handling, security, the rest ────────────────────────────────────

def test_bare_except_and_swallowed_errors():
    src = "try:\n    a()\nexcept:\n    pass\ntry:\n    b()\nexcept ValueError:\n    pass\ntry:\n    c()\nexcept KeyError as e:\n    log(e)\n"
    d = of_kind(src, "error_handling")
    assert [x.line_start for x in d] == [3, 7]


def test_security_smells():
    src = ("requests.get(u, verify=False)\nsubprocess.run(c, shell=True)\neval(user)\napp.run(debug=True)\n"
           "pickle.load(f)\nyaml.load(f)\nyaml.load(f, Loader=Safe)\nos.system(c)\n")
    assert len(of_kind(src, "security")) == 7


def test_routes_caching_concurrency_algorithms_dependencies():
    src = ("@app.route('/predict', methods=['POST'])\ndef predict(): pass\n"
           "@lru_cache(maxsize=128)\ndef g(): pass\n"
           "t = Thread(target=f)\nPool(n_jobs=4)\n"
           "heapq.heappush(h, 1)\nsorted(xs, key=len)\n"
           "import pandas as pd\nx = pd.read_csv('a')\n")
    got = {d.kind for d in detect_python("m.py", src)}
    assert {"api_contract", "caching", "concurrency", "algorithm_choice", "dependency_choice"} <= got
    route = of_kind(src, "api_contract")[0]
    assert route.facts["route"] == "/predict" and "POST" in route.facts["methods"]


def test_a_library_used_many_times_is_not_a_dependency_decision():
    assert of_kind("import numpy as np\na = np.zeros(3)\nb = np.ones(3)\n", "dependency_choice") == []


def test_unparseable_python_gives_nothing_instead_of_crashing():
    assert detect_python("m.py", "def (:\n") == []


def test_other_languages_get_reduced_depth_detectors_and_say_so():
    src = "const lr = 0.01;\ntry { run() } catch (e) {}\nresult = eval(input);\n// lr = 0.5\n"
    d = detect_generic("a.js", src)
    assert {x.kind for x in d} == {"hyperparameter", "error_handling", "security"}
    assert all(x.detector == "regex" for x in d)


def test_tests_and_notebooks_are_not_scanned_for_decisions(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("lr = 0.01\n")
    (tmp_path / "a.ipynb").write_text("{}")
    (tmp_path / "real.py").write_text("lr = 0.01\n")
    got = decisions.detect_repo(tmp_path, ["tests/test_a.py", "a.ipynb", "real.py"])
    assert [d.path for d in got] == ["real.py"]


# ── ranking ───────────────────────────────────────────────────────────────

def make(path, line, kind, comment=False):
    return decisions.Decision(f"{path}:{line}:{kind}", path, line, line, "x = 1", kind, "s", {}, [], "ast", None, 0.0, comment)


def test_interest_uses_the_planned_weights(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    d = make("a.py", 1, "hyperparameter")
    ranking.score([d], tmp_path, ["a.py"])
    assert d.authored_confidence == 0.5
    assert d.interest_score == pytest.approx(0.30 * 1.0 + 0.25 * 0.5 + 0.20 * 0 + 0.15 * 1 + 0.10 * 0)


def test_a_comment_lowers_interest_and_a_resume_link_raises_it(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    plain, explained, linked = make("a.py", 1, "magic_constant"), make("a.py", 2, "magic_constant", True), make("a.py", 3, "magic_constant")
    linked.snippet = "classifier = 1"
    ranking.score([plain, explained, linked], tmp_path, ["a.py"], resume_keywords=["classifier"])
    assert explained.interest_score < plain.interest_score < linked.interest_score


def test_decisions_the_student_did_not_write_are_never_asked_about(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n" * 10)
    mine, theirs = make("a.py", 1, "hyperparameter"), make("a.py", 5, "hyperparameter")
    blame = {"a.py": {**{n: True for n in range(1, 4)}, **{n: False for n in range(4, 8)}}}
    ranking.score([mine, theirs], tmp_path, ["a.py"], blame)
    assert mine.authored_confidence == 1.0 and theirs.authored_confidence == 0.0
    assert [d.id for d in ranking.select_top([mine, theirs])] == [mine.id]


def test_selection_caps_per_file_total_and_keeps_kinds_diverse(tmp_path):
    ds = [make("a.py", i, "hyperparameter") for i in range(1, 9)]             # eight from one file, all top-ranked
    ds += [make(f"f{i}.py", 1, k) for i, k in enumerate(["error_handling", "caching", "security", "api_contract"])]
    for i, d in enumerate(ds):
        d.authored_confidence = 1.0
        d.summary = f"finding {i}"
        d.interest_score = 0.9 if d.kind == "hyperparameter" else 0.5
    top = ranking.select_top(ds)
    assert sum(d.path == "a.py" for d in top) == 3
    assert len({d.kind for d in top}) >= 3 and len(top) <= 12


def test_import_graph_counts_who_imports_whom(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "core.py").write_text("x = 1\n")
    (tmp_path / "pkg" / "a.py").write_text("from . import core\n")
    (tmp_path / "pkg" / "b.py").write_text("from pkg.core import x\n")
    (tmp_path / "main.py").write_text("import pkg.a\nimport json\n")
    g = ranking.import_graph(tmp_path, ["pkg/__init__.py", "pkg/core.py", "pkg/a.py", "pkg/b.py", "main.py"])
    assert g["pkg/core.py"] == 2 and g["pkg/a.py"] == 1 and g["main.py"] == 0


# ── secrets ───────────────────────────────────────────────────────────────

AWS = "AKIA" + "IOSFODNN7EXAMPLE"
OPENAI = "sk-" + "proj-abcdefghijklmnopqrstuvwx1234"
GH = "ghp_" + "a" * 36


@pytest.mark.parametrize("line,kind", [
    (f'aws_key = "{AWS}"', "AWS access key"),
    (f'client = OpenAI(api_key="{OPENAI}")', "OpenAI-style API key"),
    (f'TOKEN = "{GH}"', "GitHub token"),
    ("-----BEGIN RSA PRIVATE KEY-----", "private key"),
    ('password = "Zx9!qL2mT7vB4nR8"', "hard-coded credential"),
])
def test_real_looking_secrets_are_found(line, kind):
    found = secrets.scan_text("config.py", line + "\n")
    assert [f.kind for f in found] == [kind]
    assert found[0].preview.endswith("…") and len(found[0].preview) <= 5


@pytest.mark.parametrize("line", [
    'version = "1.0.2"', 'api_key = os.environ["API_KEY"]', 'api_key = "your-api-key-here"', 'password = "changeme123456789"',
    'token = "aaaaaaaaaaaaaaaaaaaa"', 'secret = "${SECRET_FROM_VAULT_ENV}"', "x = 'sk-short'", 'name = "abcdefghijklmnopqrstuvwxyz"',
])
def test_ordinary_strings_and_placeholders_do_not_fire(line):
    assert secrets.scan_text("a.py", line + "\n") == []


def test_redact_removes_the_secret_everywhere_it_could_leak():
    text = f'k = "{AWS}"\nbearer = "{GH}"\nprint("hi")'
    out = secrets.redact(text)
    assert AWS not in out and GH not in out and 'print("hi")' in out


def test_a_secret_on_a_decision_line_never_reaches_the_snippet():
    src = f'client = Thing(api_key="{OPENAI}", epochs=30)\n'
    d = detect_python("m.py", src)
    assert d and all(OPENAI not in x.snippet for x in d)


def test_the_notice_is_kind_and_never_contains_the_secret():
    f = secrets.scan_text("config.py", f'k = "{AWS}"\n')[0]
    n = f.notice()
    assert "Rotate it" in n and "config.py:1" in n and AWS not in n


# ── rules added after reading real output (see docs/M03_ALGORITHMS.md, "what changed and why") ──

@pytest.mark.parametrize("src", [
    "x: int = Field(default=None, ge=1, le=5)\n",                  # validation bounds are not tuning choices
    "name: str = Field(min_length=1, max_length=200)\n",
    "log = warn if response.status_code >= 400 else info\n",       # HTTP status codes are a convention
    "ok = resp.status_code == 429\n",
    "rows = sorted(xs, key=lambda r: r.index)\n",                  # too common to be a decision
    "raise HTTPException(status_code=404, detail='x')\n",          # HTTP status codes passed as arguments
    "log.info('stored %s', digest[:12])\n",                        # a slice inside a log message
    "msg = f'failed: {reason[:80]}'\n",                            # a slice inside an f-string
    "logger.info('compiled', nodes=6, edges=8)\n",                 # fields of a log call
    "class Small(BaseModel):\n    a: int\n    b: int\n    c: int\n    d: int\n    e: int\n",   # five fields: a data carrier
])
def test_noise_found_in_real_repositories_is_not_reported(src):
    assert detect_python("m.py", src) == []


def test_seed_and_fixture_files_are_not_scanned(tmp_path):
    for name in ("seed.py", "fixtures.py", "sample_data.py"):
        (tmp_path / name).write_text("lr = 0.01\n")
    (tmp_path / "real.py").write_text("lr = 0.01\n")
    got = decisions.detect_repo(tmp_path, ["seed.py", "fixtures.py", "sample_data.py", "real.py"])
    assert [d.path for d in got] == ["real.py"]


def test_the_same_setting_repeated_in_many_places_is_asked_once(tmp_path):
    (tmp_path / "x.py").write_text("x = 1\n")
    ds = [make(f"f{i}.py", 1, "hyperparameter") for i in range(5)]
    for d in ds:
        d.summary, d.authored_confidence, d.interest_score = "temperature is set to 0.1", 1.0, 0.8
    assert len(ranking.select_top(ds)) == 1
