"""shared-checkout-health doctor family (coverage review R2.1). All git/gh calls are injected."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from execution import shared_checkout_health as sch  # noqa: E402

NOW = datetime(2026, 9, 2, 22, 0, tzinfo=timezone.utc)


def _ts(days_ago: float) -> str:
    return str(int((NOW - timedelta(days=days_ago)).timestamp()))


def _runner(table: dict[tuple, tuple[int, str]], calls: list | None = None):
    def run(argv):
        if calls is not None:
            calls.append(list(argv))
        for key, val in table.items():
            if tuple(argv[:len(key)]) == key:
                return val
        return (1, "")
    return run


def _repo(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    return tmp_path


def _git_key(repo: Path, *args: str) -> tuple:
    return ("git", "-C", str(repo), *args)


# ── unpushed ──────────────────────────────────────────────────────────────────

def test_unpushed_commits_warn(tmp_path):
    repo = _repo(tmp_path)
    rows = sch.unpushed_checks(repo, _runner({_git_key(repo, "rev-list", "--count", "origin/work..HEAD"): (0, "25\n")}), "origin/work")
    assert rows[0]["status"] == "WARN" and "25 commit(s) ahead of origin/work" in rows[0]["detail"]


def test_nothing_unpushed_is_ok(tmp_path):
    repo = _repo(tmp_path)
    rows = sch.unpushed_checks(repo, _runner({_git_key(repo, "rev-list"): (0, "0\n")}), "origin/work")
    assert rows[0]["status"] == "OK"


# ── stranded branches ─────────────────────────────────────────────────────────

def _branches_table(repo: Path, *, cherry: dict[str, str], gh: dict[str, tuple[int, str]] | None = None):
    table = {
        _git_key(repo, "for-each-ref"): (0, f"work\t{_ts(0)}\nfeat/old\t{_ts(5)}\nfeat/fresh\t{_ts(0.5)}\n"
                                            f"feat/checked\t{_ts(9)}\nfeat/merged\t{_ts(9)}\nfeat/pr\t{_ts(9)}\n"),
        _git_key(repo, "worktree", "list"): (0, "worktree /x\nHEAD abc\nbranch refs/heads/work\n\n"
                                                "worktree /y\nHEAD def\nbranch refs/heads/feat/checked\n\n"),
    }
    for name, out in cherry.items():
        table[_git_key(repo, "cherry", "origin/work", name)] = (0, out)
    for name, res in (gh or {}).items():
        table[("gh", "pr", "list", "--head", name)] = res
    return table


def test_stranded_branch_by_content_with_no_worktree_and_no_pr_warns(tmp_path):
    repo = _repo(tmp_path)
    table = _branches_table(repo,
                            cherry={"feat/old": "+ aaa\n+ bbb\n", "feat/merged": "- ccc\n", "feat/pr": "+ ddd\n"},
                            gh={"feat/old": (0, "[]\n"), "feat/pr": (0, '[{"number": 12}]\n')})
    rows = sch.stranded_branch_checks(repo, _runner(table), NOW, "origin/work")
    assert [r["label"] for r in rows] == ["shared-checkout-health.stranded.feat/old"]
    assert "2 commit(s) not in origin/work by content, no worktree, 5d old, no open PR" in rows[0]["detail"]
    # feat/fresh (too young), feat/checked (in a worktree), feat/merged (squash-equivalent
    # content already upstream: only '-' lines), feat/pr (open PR) are all excluded.


def test_gh_unavailable_reports_unmeasured_never_no_pr(tmp_path):
    repo = _repo(tmp_path)
    table = _branches_table(repo, cherry={"feat/old": "+ aaa\n", "feat/merged": "", "feat/pr": "+ ddd\n"})
    calls: list = []
    rows = sch.stranded_branch_checks(repo, _runner(table, calls), NOW, "origin/work")
    assert len(rows) == 2
    assert all("PR status unmeasured" in r["detail"] for r in rows)
    # gh was tried once, found dead, and not retried per branch.
    assert sum(1 for c in calls if c[0] == "gh") == 1


def test_no_stranded_branches_is_ok(tmp_path):
    repo = _repo(tmp_path)
    table = _branches_table(repo, cherry={"feat/old": "", "feat/merged": "", "feat/pr": ""})
    rows = sch.stranded_branch_checks(repo, _runner(table), NOW, "origin/work")
    assert rows == [{"status": "OK", "label": "shared-checkout-health.stranded",
                     "detail": "no stranded branches (0 candidate(s) examined)"}]


def test_branch_listing_failure_cannot_measure(tmp_path):
    repo = _repo(tmp_path)
    rows = sch.stranded_branch_checks(repo, _runner({}), NOW, "origin/work")
    assert rows[0]["status"] == "WARN" and "cannot measure" in rows[0]["detail"]


# ── stashes ───────────────────────────────────────────────────────────────────

def test_old_stash_warns_and_young_stash_does_not(tmp_path):
    repo = _repo(tmp_path)
    out = f"{_ts(2)}\tstash@{{0}}\tOn work: pre-hard-reset backup 2026-08-31\n{_ts(0.2)}\tstash@{{1}}\tOn work: wip\n"
    rows = sch.stash_checks(repo, _runner({_git_key(repo, "stash", "list"): (0, out)}), NOW)
    assert [r["label"] for r in rows] == ["shared-checkout-health.stash.stash@{0}"]
    assert "2d old: On work: pre-hard-reset backup" in rows[0]["detail"]


def test_no_old_stash_is_ok(tmp_path):
    repo = _repo(tmp_path)
    rows = sch.stash_checks(repo, _runner({_git_key(repo, "stash", "list"): (0, "")}), NOW)
    assert rows[0]["status"] == "OK"


# ── dirty tracked state ───────────────────────────────────────────────────────

def test_modified_tracked_state_file_fails_and_artifacts_warn(tmp_path):
    repo = _repo(tmp_path)
    out = (' M Governance/Order Samurai/state/hitl_queue.json\0'
           ' M Governance/Order Samurai/artifacts/inventory.json\0'
           'MM Governance/Order Samurai/execution/doctor.py\0'
           ' M Governance/README.md\0')
    rows = sch.dirty_tracked_checks(repo, _runner({_git_key(repo, "status"): (0, out)}))
    assert [(r["status"], r["label"]) for r in rows] == [
        ("FAIL", "shared-checkout-health.state-dirty"),
        ("WARN", "shared-checkout-health.artifacts-dirty"),
        ("WARN", "shared-checkout-health.materialized"),
    ]
    assert "hitl_queue.json is git-tracked and modified" in rows[0]["detail"]
    assert "doctor.py: index and working tree both differ" in rows[2]["detail"]


def test_clean_tree_is_ok(tmp_path):
    repo = _repo(tmp_path)
    rows = sch.dirty_tracked_checks(repo, _runner({_git_key(repo, "status"): (0, "")}))
    assert rows[0]["status"] == "OK"


def test_status_failure_cannot_measure(tmp_path):
    repo = _repo(tmp_path)
    rows = sch.dirty_tracked_checks(repo, _runner({}))
    assert rows[0]["status"] == "WARN" and "cannot measure" in rows[0]["detail"]


def test_staged_state_is_warning_not_machine_writer_failure(tmp_path):
    repo = _repo(tmp_path)
    out = ("M  Governance/Order Samurai/state/human-note.md\0"
           "A  Governance/Order Samurai/state/new-note.md\0")
    rows = sch.dirty_tracked_checks(repo, _runner({_git_key(repo, "status"): (0, out)}))
    assert all(row["status"] == "WARN" for row in rows)
    assert all("not evidence of a background writer" in row["detail"] for row in rows)


def test_staged_rename_into_state_parses_both_nul_delimited_paths(tmp_path):
    repo = _repo(tmp_path)
    out = ("R  Governance/Order Samurai/state/moved.json\0"
           "elsewhere/original.json\0")
    rows = sch.dirty_tracked_checks(repo, _runner({_git_key(repo, "status"): (0, out)}))
    assert rows[0]["status"] == "WARN"
    assert "state/moved.json" in rows[0]["detail"]


def test_rename_within_state_reports_only_the_destination_as_modified(tmp_path):
    """`RM` within state/: the worktree letter describes the destination alone.

    Order-differentiating — reading the two NUL fields the other way round would
    blame `old.json` (which no longer exists) for the modification and demote the
    live `new.json` to a staged-state warning.
    """
    repo = _repo(tmp_path)
    out = ("RM Governance/Order Samurai/state/new.json\0"
           "Governance/Order Samurai/state/old.json\0")
    rows = sch.dirty_tracked_checks(repo, _runner({_git_key(repo, "status"): (0, out)}))
    assert [(r["status"], r["label"]) for r in rows] == [
        ("FAIL", "shared-checkout-health.state-dirty"),
        ("WARN", "shared-checkout-health.tracked-state"),
    ]
    assert "state/new.json is git-tracked and modified" in rows[0]["detail"]
    assert "state/old.json has git status 'RM'" in rows[1]["detail"]


def test_rename_out_of_state_does_not_blame_the_vanished_source(tmp_path):
    """A staged rename out of state/ plus an unstaged edit of the destination.

    `git mv` deletes the source from the working tree, so the trailing `M` cannot
    be a background writer mutating it — state-dirty against the source would
    accuse a file that is no longer on disk.
    """
    repo = _repo(tmp_path)
    out = ("RM elsewhere/moved.json\0"
           "Governance/Order Samurai/state/moved.json\0")
    rows = sch.dirty_tracked_checks(repo, _runner({_git_key(repo, "status"): (0, out)}))
    assert [(r["status"], r["label"]) for r in rows] == [
        ("WARN", "shared-checkout-health.tracked-state"),
    ]
    assert "state/moved.json has git status 'RM'" in rows[0]["detail"]


def test_adjacent_rename_records_consume_their_own_source_fields(tmp_path):
    """Byte-for-byte layout emitted by `git status --porcelain=v1 -z` (git 2.54).

    Two `RM` records back to back — one renamed out of state/, one renamed in —
    so a source field mis-consumed by the first record would derail the second.
    """
    repo = _repo(tmp_path)
    out = ("RM elsewhere/moved.json\0"
           "Governance/Order Samurai/state/moved.json\0"
           "RM Governance/Order Samurai/state/into.json\0"
           "elsewhere/into.json\0")
    rows = sch.dirty_tracked_checks(repo, _runner({_git_key(repo, "status"): (0, out)}))
    assert [(r["status"], r["label"]) for r in rows] == [
        ("WARN", "shared-checkout-health.tracked-state"),
        ("FAIL", "shared-checkout-health.state-dirty"),
    ]
    assert "state/moved.json has git status 'RM'" in rows[0]["detail"]
    assert "state/into.json is git-tracked and modified" in rows[1]["detail"]


def test_rename_of_artifacts_destination_warns_without_state_failure(tmp_path):
    repo = _repo(tmp_path)
    out = ("RM Governance/Order Samurai/artifacts/inventory.json\0"
           "Governance/Order Samurai/state/inventory.json\0")
    rows = sch.dirty_tracked_checks(repo, _runner({_git_key(repo, "status"): (0, out)}))
    assert [(r["status"], r["label"]) for r in rows] == [
        ("WARN", "shared-checkout-health.artifacts-dirty"),
        ("WARN", "shared-checkout-health.tracked-state"),
    ]


# ── composition ───────────────────────────────────────────────────────────────

def test_not_a_checkout_cannot_measure(tmp_path):
    rows = sch.run_checks(main_checkout=tmp_path, run=_runner({}), now=NOW)
    assert rows == [{"status": "WARN", "label": "shared-checkout-health.main-checkout",
                     "detail": f"{tmp_path} is not a git checkout; cannot measure"}]


def test_run_checks_composes_the_four_probes(tmp_path):
    repo = _repo(tmp_path)
    rows = sch.run_checks(main_checkout=repo, run=_runner({}), now=NOW)
    assert [r["label"].split(".")[1] for r in rows] == ["unpushed", "stranded", "stash", "state-dirty"]
    assert all(r["status"] == "WARN" for r in rows)   # nothing answered → everything unmeasured
