"""Reference-material matching: fewer questions about copied-in code, and nothing about it ever reaches the student."""

import json
import re

from conftest import make_repo

from gh_analyzer import pipeline, reference

TUTORIAL = '''import torch
import torch.nn as nn

class Net(nn.Module):
    def __init__(self, n_in, n_out):
        super().__init__()
        self.fc1 = nn.Linear(n_in, 128)
        self.fc2 = nn.Linear(128, 64)
        self.fc3 = nn.Linear(64, n_out)
        self.drop = nn.Dropout(0.5)

    def forward(self, x):
        x = torch.relu(self.fc1(x))
        x = self.drop(x)
        x = torch.relu(self.fc2(x))
        return self.fc3(x)

def train(model, loader, epochs=15):
    lr = 0.003
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    for epoch in range(epochs):
        for xb, yb in loader:
            opt.zero_grad()
            loss = nn.functional.cross_entropy(model(xb), yb)
            loss.backward()
            opt.step()
'''
RENAMED = (TUTORIAL.replace("Net", "Classifier").replace("fc", "layer").replace("n_in", "inputs")
           .replace("n_out", "outputs").replace("epochs", "rounds").replace("loader", "batches"))
OWN = '''def schedule(jobs, workers):
    ready = [j for j in jobs if j.deps_done()]
    ready.sort(key=lambda j: j.priority)
    batch_size = 37
    chosen = ready[:batch_size]
    for j in chosen:
        j.assign(workers[hash(j.name) % len(workers)])
    if len(chosen) > 19:
        workers.rebalance(threshold=0.83)
    return chosen
'''


def corpus_from(tmp_path, files):
    d = tmp_path / "ref"
    d.mkdir()
    for n, t in files.items():
        (d / n).write_text(t, encoding="utf-8")
    return reference.build(str(d))


def test_a_renamed_copy_still_matches(tmp_path):
    c = corpus_from(tmp_path, {"tut.py": TUTORIAL})
    sig = reference.signature(reference.tokens(RENAMED))
    assert sig and c.best(sig) >= reference.THRESHOLD


def test_unrelated_code_does_not_match(tmp_path):
    c = corpus_from(tmp_path, {"tut.py": TUTORIAL})
    sig = reference.signature(reference.tokens(OWN))
    assert sig is None or c.best(sig) < reference.THRESHOLD


def test_tiny_snippets_are_too_short_to_say_anything():
    assert reference.signature(reference.tokens("x = 1\ny = 2\n")) is None


def test_the_corpus_round_trips_through_a_file(tmp_path):
    c = corpus_from(tmp_path, {"tut.py": TUTORIAL})
    c.save(str(tmp_path / "c.json"))
    again = reference.Corpus.load(str(tmp_path / "c.json"))
    sig = reference.signature(reference.tokens(TUTORIAL))
    assert again.best(sig) == 1.0 and len(again.entries) == len(c.entries)


def _repo(tmp_repo):
    return tmp_repo([{"files": {"model.py": RENAMED, "sched.py": OWN}}], "proj")


def test_copied_in_code_is_not_asked_about_but_the_students_own_code_is(tmp_repo, tmp_path):
    repo = _repo(tmp_repo)
    corpus_from(tmp_path, {"tut.py": TUTORIAL}).save(str(tmp_path / "c.json"))
    plain = pipeline.analyse_repo(str(repo))
    withref = pipeline.analyse_repo(str(repo), reference_corpus=str(tmp_path / "c.json"))
    paths_plain = {d["path"] for d in plain["student"]["decisions"]}
    paths_ref = {d["path"] for d in withref["student"]["decisions"]}
    assert "model.py" in paths_plain, "the fixture must produce a question about the copied file"
    assert "model.py" not in paths_ref and "sched.py" in paths_ref
    assert withref["private"]["audit"]["decisions_set_aside_for_reference"] >= 1


def test_without_a_corpus_nothing_changes(tmp_repo):
    repo = _repo(tmp_repo)
    r = pipeline.analyse_repo(str(repo))
    assert r["private"]["audit"]["decisions_set_aside_for_reference"] == 0


def test_the_match_is_never_in_anything_a_student_can_reach(tmp_repo, tmp_path):
    repo = _repo(tmp_repo)
    corpus_from(tmp_path, {"tut.py": TUTORIAL}).save(str(tmp_path / "c.json"))
    r = pipeline.analyse_repo(str(repo), reference_corpus=str(tmp_path / "c.json"))
    text = json.dumps(r["student"]) + pipeline.render_markdown(r)
    assert not re.search(r"reference|tutorial|match|overlap|copied|similar", text, re.I)


def test_a_match_can_only_lower_confidence(tmp_repo, tmp_path):
    repo = _repo(tmp_repo)
    c = corpus_from(tmp_path, {"tut.py": TUTORIAL})
    from gh_analyzer import decisions
    ds = decisions.detect_repo(repo, ["model.py", "sched.py"])
    for d in ds:
        d.authored_confidence = 0.3
    reference.cap_confidence(ds, repo, c)
    assert all(d.authored_confidence <= 0.3 for d in ds)
