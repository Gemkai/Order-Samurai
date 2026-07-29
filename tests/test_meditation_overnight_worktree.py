"""Regression test for MEDITATION_WORKTREE=1 isolation in meditation_overnight.sh.

The clobber incidents (2026-07-09) came from the job switching branches / auto-stashing
the MAIN working tree. In worktree mode the cycle must run in a dedicated worktree and
leave the main tree — including a live session's uncommitted work — completely untouched.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "bin" / "meditation_overnight.sh"
pytestmark = pytest.mark.skipif(not SCRIPT.exists(), reason="meditation_overnight.sh not found")


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@t.com"], ["config", "user.name", "T"]):
        subprocess.run(["git", *args], cwd=repo, check=True)
    (repo / "prompts").mkdir()
    (repo / "prompts" / "meditation_cycle.md").write_text("test prompt\n")
    (repo / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def _fake_claude(tmp_path: Path) -> Path:
    b = tmp_path / "fakebin"
    b.mkdir()
    stub = b / "claude"
    stub.write_text("#!/usr/bin/env bash\necho '{\"result\":\"ok\",\"total_cost_usd\":0}'\nexit 0\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return b


def test_worktree_mode_leaves_main_tree_and_uncommitted_work_untouched(tmp_path):
    repo = _init_repo(tmp_path)
    # a live session's uncommitted work sits in the main tree
    wip = repo / "SESSION_WORK.md"
    wip.write_text("uncommitted work that must survive\n")

    branch_before = subprocess.run(
        ["git", "branch", "--show-current"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    env = dict(os.environ)
    env["PATH"] = f"{_fake_claude(tmp_path)}:{env['PATH']}"
    env["REPO_DIR"] = str(repo)
    env["MEDITATION_WORKTREE"] = "1"
    env["MEDITATION_WT_DIR"] = str(tmp_path / "wt")
    env["MEDITATION_DRYRUN"] = "1"
    env["MAX_CYCLES"] = "1"
    result = subprocess.run(
        ["bash", str(SCRIPT)], cwd=repo, env=env, capture_output=True, text=True, timeout=60
    )

    assert result.returncode == 0, result.stdout + result.stderr
    # main tree still on its original branch (never switched to ronin/overnight)
    branch_after = subprocess.run(
        ["git", "branch", "--show-current"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert branch_after == branch_before
    # the uncommitted work is byte-identical and not stashed
    assert wip.read_text() == "uncommitted work that must survive\n"
    stashes = subprocess.run(["git", "stash", "list"], cwd=repo, capture_output=True, text=True, check=True)
    assert stashes.stdout.strip() == ""
    # the worktree was cleaned up on exit
    worktrees = subprocess.run(["git", "worktree", "list"], cwd=repo, capture_output=True, text=True, check=True)
    assert str(tmp_path / "wt") not in worktrees.stdout


def _state_writing_claude(tmp_path: Path) -> Path:
    """Stub `claude` that simulates the cycle writing canonical state — to the MAIN tree via the
    exported MEDITATION_STATE_DIR, exactly as the re-rooted helper scripts and prompt now do."""
    b = tmp_path / "fakebin_state"
    b.mkdir()
    stub = b / "claude"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'echo "cycle wrote this" > "$MEDITATION_STATE_DIR/cycle_marker.txt"\n'
        "echo '{\"result\":\"ok\",\"total_cost_usd\":0}'\nexit 0\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return b


def test_worktree_mode_writes_canonical_state_into_the_main_tree(tmp_path):
    """Option C core: the cycle runs in the worktree but its state writes (via MEDITATION_STATE_DIR)
    land in the MAIN tree, where the dashboard-refresh job and hero metrics read them — not in the
    disposable worktree where they would be discarded. Also confirms a live tracked edit in the main
    tree survives byte-identical and the main branch is never switched (the 2026-07-09 incident)."""
    repo = _init_repo(tmp_path)
    # a live session's edit to a TRACKED file must survive
    tracked = repo / "README.md"
    tracked.write_text("edited-by-live-session\n")

    branch_before = subprocess.run(
        ["git", "branch", "--show-current"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    env = dict(os.environ)
    env["PATH"] = f"{_state_writing_claude(tmp_path)}:{env['PATH']}"
    env["REPO_DIR"] = str(repo)
    env["MEDITATION_WORKTREE"] = "1"
    env["MEDITATION_WT_DIR"] = str(tmp_path / "wt")
    env["MEDITATION_DRYRUN"] = "1"
    env["MAX_CYCLES"] = "1"
    result = subprocess.run(
        ["bash", str(SCRIPT)], cwd=repo, env=env, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stdout + result.stderr

    # the state write landed in the MAIN tree, NOT the (removed) worktree
    assert (repo / "state" / "cycle_marker.txt").read_text() == "cycle wrote this\n"
    assert not (tmp_path / "wt" / "state" / "cycle_marker.txt").exists()
    # the live tracked edit survived and the main branch was never switched
    assert tracked.read_text() == "edited-by-live-session\n"
    branch_after = subprocess.run(
        ["git", "branch", "--show-current"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert branch_after == branch_before
