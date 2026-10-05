import pytest

from gh_analyzer import contribution, inventory, snapshot

from conftest import lines, run

A = ("Asha", "asha@example.com")
B = ("Ben", "ben@example.com")


def analyse(repo, me):
    log = snapshot.export_log(repo)
    inv = inventory.scan(repo)
    return contribution.analyse(log, inv.analysed, repo, me)


def spread(n, author_pattern, start_day=1):
    """n commits on n different days; author_pattern is a list cycled over commits; each adds one new file."""
    return [{"author": author_pattern[i % len(author_pattern)], "date": f"2026-02-{start_day + i:02d}T10:00:00",
             "files": {f"m{i}.py": lines(10, f"v{i}_")}, "msg": f"add module number {i}"} for i in range(n)]


# ── the range ─────────────────────────────────────────────────────────────

def test_group_project_range_brackets_the_true_share(tmp_repo):
    # Asha writes 4 of every 5 modules: 80% of the code
    repo = tmp_repo(spread(15, [A, A, A, A, B]))
    c = analyse(repo, ["asha@example.com"])
    assert c.history_usable
    assert c.share_low <= 0.8 <= c.share_high
    assert c.share_high - c.share_low >= 0.0999                     # a range, widened both ways, never a point
    assert c.other_contributors == 1 and c.candidate_commits == 12


def test_the_range_is_widened_by_five_points_each_way(tmp_repo):
    repo = tmp_repo(spread(15, [A, B]))                              # both estimates about equal to 0.5
    c = analyse(repo, ["asha@example.com"])
    assert c.additions_share == pytest.approx(c.blame_share, abs=0.03)
    assert c.share_low == pytest.approx(min(c.additions_share, c.blame_share) - 0.05, abs=0.01)
    assert c.share_high == pytest.approx(max(c.additions_share, c.blame_share) + 0.05, abs=0.01)


def test_a_rewrite_makes_the_two_estimates_disagree_and_says_so(tmp_repo):
    repo = tmp_repo([
        {"author": A, "date": "2026-02-01T10:00:00", "files": {"core.py": lines(100, "a")}},
        {"author": B, "date": "2026-02-02T10:00:00", "files": {"core.py": lines(100, "b")}},      # replaces every line
        *spread(6, [A, B], 3),
    ])
    c = analyse(repo, ["asha@example.com"])
    assert abs(c.additions_share - c.blame_share) > 0.30 or any("unclear" in n for n in c.notes)
    assert any("unclear" in n for n in c.notes)
    assert c.share_high - c.share_low > 0.3


def test_the_sentence_is_always_a_range_with_a_method_and_never_a_verdict(tmp_repo):
    repo = tmp_repo(spread(12, [A, A, A, B]))
    text = contribution.describe(analyse(repo, ["asha@example.com"]))
    assert "roughly" in text and "–" in text and "based on commits and current file ownership" in text
    assert "1 other contributor" in text and "other contributors" not in text
    for word in ("plagiar", "copied", "fake", "authentic", "original", "suspicious"):
        assert word not in text.lower()


# ── squashed history ──────────────────────────────────────────────────────

def test_one_big_commit_gives_no_number_at_all(tmp_repo):
    repo = tmp_repo([{"author": A, "files": {f"f{i}.py": lines(30) for i in range(20)}, "msg": "initial commit"}])
    c = analyse(repo, ["asha@example.com"])
    assert not c.history_usable and c.share_low is None and c.share_high is None and c.additions_share is None
    assert "can't tell" in contribution.describe(c).lower() and "suspicious" not in contribution.describe(c).lower()
    assert c.cadence == "unknown"


def test_two_commits_is_also_unusable(tmp_repo):
    repo = tmp_repo([{"author": A, "files": {"a.py": lines(5)}}, {"author": A, "files": {"b.py": lines(5)}, "date": "2026-03-01T10:00:00"}])
    assert not analyse(repo, ["asha@example.com"]).history_usable


def test_everything_committed_within_an_hour_is_unusable(tmp_repo):
    commits = [{"author": A, "date": f"2026-02-01T10:{i:02d}:00", "files": {f"m{i}.py": lines(10)}} for i in range(1, 6)]
    commits[0]["files"] = {"big.py": lines(400)}
    repo = tmp_repo(commits)
    c = analyse(repo, ["asha@example.com"])
    assert not c.history_usable and "hour" in c.reason_if_unusable


# ── identity first ────────────────────────────────────────────────────────

def test_without_identities_nothing_is_said_about_you_and_candidates_are_listed(tmp_repo):
    repo = tmp_repo(spread(10, [A, B, B]))
    c = analyse(repo, None)
    assert c.share_low is None and any("which commits are yours" in n for n in c.notes)
    cands = contribution.identity_candidates(snapshot.export_log(repo))
    assert [x["email"] for x in cands] == ["ben@example.com", "asha@example.com"] and cands[0]["commits"] == 6


def test_identities_that_match_nothing_say_so(tmp_repo):
    repo = tmp_repo(spread(10, [A, B]))
    c = analyse(repo, ["nobody@example.com"])
    assert c.candidate_commits == 0 and c.share_low is None and any("None of the commits match" in n for n in c.notes)


def test_a_name_can_confirm_identity_as_well_as_an_email(tmp_repo):
    repo = tmp_repo(spread(10, [A, B]))
    assert analyse(repo, ["Asha"]).candidate_commits == 5


# ── shallow history ───────────────────────────────────────────────────────

def test_a_recent_contributor_is_not_credited_with_code_older_than_the_history(tmp_repo, tmp_path):
    src = tmp_repo([
        {"author": A, "date": "2026-01-01T10:00:00", "files": {"core.py": lines(300)}},
        {"author": A, "date": "2026-01-10T10:00:00", "files": {"more.py": lines(5)}},
        {"author": B, "date": "2026-01-20T10:00:00", "files": {"b1.py": lines(5)}},
        {"author": B, "date": "2026-01-25T10:00:00", "files": {"b2.py": lines(5)}},
        {"author": B, "date": "2026-02-01T10:00:00", "files": {"b3.py": lines(5)}},
    ])
    clone = tmp_path / "shallow"
    run(tmp_path, "clone", "-q", "--depth", "4", src.as_uri(), str(clone))
    log = snapshot.export_log(clone)
    inv = inventory.scan(clone)
    c = contribution.analyse(log, inv.analysed, clone, ["ben@example.com"])
    # Ben wrote 15 of 325 lines in truth (about 5%). Naive handling would call him ~100% of the window.
    assert c.share_low <= 0.05 <= c.share_high
    assert c.share_high - c.share_low > 0.5                        # the honest answer is "I cannot tell"
    assert any("before the history" in n for n in c.notes)


# ── cadence ───────────────────────────────────────────────────────────────

def cadence(commits):
    return contribution.classify_cadence(commits)[0]


def commits_on(repo_commits, tmp_repo):
    return snapshot.export_log(tmp_repo(repo_commits))


def test_cadence_steady(tmp_repo):
    commits = [{"date": f"2026-02-{d:02d}T10:00:00", "files": {f"f{d}.py": lines(5)}} for d in (1, 3, 6, 9, 12, 15, 18, 21, 24)]
    assert cadence(commits_on(commits, tmp_repo)) == "steady"


def test_cadence_single_dump_is_a_description_not_an_accusation(tmp_repo):
    commits = [{"date": f"2026-02-01T{h:02d}:00:00", "files": {f"f{h}.py": lines(20)}} for h in range(8, 16)]
    assert cadence(commits_on(commits, tmp_repo)) == "single_dump"


def test_cadence_bursty_is_checked_before_steady(tmp_repo):
    days = [1, 2, 3, 4, 20, 21, 22, 23, 24, 25]                     # two bursts, 16 days apart, 10 distinct days
    commits = [{"date": f"2026-02-{d:02d}T10:00:00", "files": {f"f{d}.py": lines(5)}} for d in days]
    assert cadence(commits_on(commits, tmp_repo)) == "bursty"


def test_cadence_unknown_with_fewer_than_five_commits(tmp_repo):
    commits = [{"date": f"2026-02-{d:02d}T10:00:00", "files": {f"f{d}.py": lines(5)}} for d in (1, 9, 18)]
    assert cadence(commits_on(commits, tmp_repo)) == "unknown"


def test_commit_message_quality_counts_lazy_messages(tmp_repo):
    commits = [{"date": f"2026-02-{d:02d}T10:00:00", "files": {f"f{d}.py": lines(5)}, "msg": m}
               for d, m in zip(range(1, 7), ["update", "fix", "add login form validation", "wip", "handle empty input safely", "changes"])]
    q = contribution.message_quality(commits_on(commits, tmp_repo))
    assert q == pytest.approx(2 / 6, abs=0.01)


def test_primary_areas_follow_where_the_work_is(tmp_repo):
    commits = []
    for i in range(10):
        commits.append({"author": A, "date": f"2026-02-{i + 1:02d}T10:00:00",
                        "files": {f"model/m{i}.py": lines(30), f"ui/u{i}.py": lines(3)}})
    c = analyse(tmp_repo(commits), ["asha@example.com"])
    assert c.primary_areas == ["model"]
