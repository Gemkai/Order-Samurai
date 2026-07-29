"""Regression test for the meditation_overnight.sh merge-guard.

2026-07-09 incident: a PR #40 merge-conflict-resolution session had a dirty,
mid-merge working tree. meditation_overnight.sh's MEDITATION_AUTO_STASH path ran
`git stash push -u` against it, silently stashing the staged conflict resolution
and resetting the tree to pre-merge HEAD. Recovered via the stash, but it was a
scare. The script was hardened to check for .git/MERGE_HEAD (and the rebase/
cherry-pick equivalents) before the auto-stash block and abort loudly instead of
touching git state. This pins that guard so it can't silently regress.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "bin" / "meditation_overnight.sh"

pytestmark = pytest.mark.skipif(not SCRIPT.exists(), reason="meditation_overnight.sh not found")

MERGE_MARKERS = [
    (".git/MERGE_HEAD", "file"),
    (".git/rebase-merge", "dir"),
    (".git/rebase-apply", "dir"),
    (".git/CHERRY_PICK_HEAD", "file"),
]


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "prompts").mkdir()
    (repo / "prompts" / "meditation_cycle.md").write_text("test prompt\n")
    (repo / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def _fake_claude_bin(tmp_path: Path) -> Path:
    """Stub `claude` on PATH so the script's `command -v claude` check passes.

    The merge guard fires before the script ever tries to invoke `claude`, but
    the earlier `command -v claude` guard still requires something on PATH.
    """
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    claude_stub = fake_bin / "claude"
    claude_stub.write_text("#!/usr/bin/env bash\necho '{}'\nexit 0\n")
    claude_stub.chmod(claude_stub.stat().st_mode | stat.S_IEXEC)
    return fake_bin


def _run_script(repo: Path, tmp_path: Path, worktree: str = "0") -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PATH"] = f"{_fake_claude_bin(tmp_path)}:{env['PATH']}"
    env["REPO_DIR"] = str(repo)
    env["MEDITATION_AUTO_STASH"] = "1"  # replicate the incident: guard fires even with auto-stash on
    env["MEDITATION_DRYRUN"] = "1"
    env["MAX_CYCLES"] = "1"
    # These tests pin the LEGACY in-main-tree guard behavior, which now lives behind
    # MEDITATION_WORKTREE=0 (the script default flipped to worktree isolation). Set it
    # explicitly so the tests are independent of the shipped default.
    env["MEDITATION_WORKTREE"] = worktree
    if worktree == "1":
        env["MEDITATION_WT_DIR"] = str(tmp_path / "wt")
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.parametrize("marker_path,kind", MERGE_MARKERS)
def test_aborts_instead_of_stashing_when_git_operation_in_progress(tmp_path, marker_path, kind):
    """A dirty tree with an in-progress merge/rebase/cherry-pick aborts loudly
    and never runs git stash, regardless of MEDITATION_AUTO_STASH."""
    repo = _init_repo(tmp_path)
    (repo / "WIP.md").write_text("dirty\n")
    marker = repo / marker_path
    if kind == "dir":
        marker.mkdir()
    else:
        marker.write_text("deadbeef\n")

    result = _run_script(repo, tmp_path)

    assert result.returncode != 0
    assert "never auto-stash" in (result.stdout + result.stderr)
    stash_list = subprocess.run(
        ["git", "stash", "list"], cwd=repo, capture_output=True, text=True, check=True,
    )
    assert stash_list.stdout.strip() == "", "an in-progress merge must never be auto-stashed"


def test_aborts_when_uncommitted_work_exists_outside_state_and_artifacts(tmp_path):
    """A live interactive session's uncommitted work (anything outside state/ and
    artifacts/) aborts loudly and is never swept into an auto-stash, even with
    MEDITATION_AUTO_STASH=1. Pins the 2026-07-09 fix where `git stash push -u`
    swept a session's new untracked files (SHARED_NOTES.md et al.) into a stash."""
    repo = _init_repo(tmp_path)
    (repo / "SHARED_NOTES.md").write_text("a live session's brand-new untracked file\n")

    result = _run_script(repo, tmp_path)

    assert result.returncode != 0
    assert "not routine churn" in (result.stdout + result.stderr)
    stash_list = subprocess.run(
        ["git", "stash", "list"], cwd=repo, capture_output=True, text=True, check=True,
    )
    assert stash_list.stdout.strip() == "", "a live session's work must never be auto-stashed"


def test_routine_state_churn_does_not_trip_the_interactive_session_guard(tmp_path):
    """Dirty files confined to state/ are routine overnight churn — they must NOT
    trigger the interactive-session abort (that would make the job no-op every night)."""
    repo = _init_repo(tmp_path)
    (repo / "state").mkdir(exist_ok=True)
    (repo / "state" / "MEDITATION_STATE.json").write_text('{"churn": true}\n')

    result = _run_script(repo, tmp_path)

    assert "not routine churn" not in (result.stdout + result.stderr)


@pytest.mark.parametrize("marker_path,kind", MERGE_MARKERS)
def test_merge_guard_still_fires_in_worktree_mode(tmp_path, marker_path, kind):
    """The merge/rebase/cherry-pick guard is mode-independent: even under the now-default
    MEDITATION_WORKTREE=1 isolation, an in-progress git operation must abort loudly and never
    have the job proceed against it."""
    repo = _init_repo(tmp_path)
    (repo / "WIP.md").write_text("dirty\n")
    marker = repo / marker_path
    if kind == "dir":
        marker.mkdir()
    else:
        marker.write_text("deadbeef\n")

    result = _run_script(repo, tmp_path, worktree="1")

    assert result.returncode != 0
    assert "never auto-stash" in (result.stdout + result.stderr)


def test_worktree_mode_warns_but_does_not_abort_on_uncommitted_work(tmp_path):
    """The abort->warn downgrade (Option C step 5): in worktree isolation, a live session's
    uncommitted work outside state/ no longer aborts the run — it is noted and the cycle proceeds,
    because the worktree never touches the main tree. Pins that the 2026-07-09 abort became a warn."""
    repo = _init_repo(tmp_path)
    wip = repo / "SHARED_NOTES.md"
    wip.write_text("a live session's brand-new untracked file\n")
    branch_before = subprocess.run(
        ["git", "branch", "--show-current"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    result = _run_script(repo, tmp_path, worktree="1")

    assert result.returncode == 0, result.stdout + result.stderr
    # It warns (NOTE), but never with the legacy abort phrasing, and never stashes.
    assert "not routine churn" not in (result.stdout + result.stderr)
    assert wip.read_text() == "a live session's brand-new untracked file\n"
    stash_list = subprocess.run(
        ["git", "stash", "list"], cwd=repo, capture_output=True, text=True, check=True,
    )
    assert stash_list.stdout.strip() == "", "worktree mode must never stash the main tree"
    branch_after = subprocess.run(
        ["git", "branch", "--show-current"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert branch_after == branch_before, "worktree mode must not switch the main tree's branch"
