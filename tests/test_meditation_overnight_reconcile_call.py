"""Regression test for meditation_overnight.sh's reconcile_state.py call site.

Found via a 10-agent code-review test of compound-engineering:ce-code-review
(ce-reliability-reviewer + ce-correctness-reviewer): the original uncommitted
version of this call was `python3 "$MAIN_DIR/bin/reconcile_state.py" ||
echo "[meditation] Deterministic reconciliation pass completed with warnings."`
-- a total crash (missing script, ImportError, syntax error -- not just "ran
with warnings") read identically to a real soft-warning run in this log,
because the actual exit code was discarded. There was also no timeout, unlike
every other non-fatal step in this script, so a hang here would have stalled
the whole 6-hour cycle before it even started.

These tests exercise the REAL script (not a reimplementation) via the same
harness pattern test_meditation_overnight_merge_guard.py already established,
with a controllable stub `bin/reconcile_state.py` placed in the synthetic
repo so each scenario's exit behavior is deterministic.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
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
    (repo / "state").mkdir()
    (repo / "state" / "budget_ledger.json").write_text(json.dumps({
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "spent_usd": 0, "daily_limit_usd": 5,
    }))
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
    """Stub `claude` on PATH so the script's `command -v claude` check passes."""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    claude_stub = fake_bin / "claude"
    claude_stub.write_text("#!/usr/bin/env bash\necho '{}'\nexit 0\n")
    claude_stub.chmod(claude_stub.stat().st_mode | stat.S_IEXEC)
    return fake_bin


def _stub_reconcile_state(repo: Path, exit_code: int) -> None:
    """A minimal stand-in for bin/reconcile_state.py that exits with a
    controlled code — this is the "$MAIN_DIR/bin/reconcile_state.py" the
    script's REPO_DIR/MAIN_DIR resolution actually finds, since REPO_DIR
    points at this synthetic repo, not the real Order Samurai tree.

    Committed immediately, matching P1 #4's own fix (the real
    reconcile_state.py must ship committed, not sit untracked) — an
    uncommitted stub here would trip the UNRELATED merge-guard (tested in
    test_meditation_overnight_merge_guard.py) as a live session's stray work,
    which is not what these tests are exercising."""
    bin_dir = repo / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "reconcile_state.py"
    script.write_text(f"#!/usr/bin/env python3\nimport sys\nsys.exit({exit_code})\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    subprocess.run(["git", "add", "bin/reconcile_state.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "stub reconcile_state.py"], cwd=repo, check=True)


def _run_script(repo: Path, tmp_path: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PATH"] = f"{_fake_claude_bin(tmp_path)}:{env['PATH']}"
    env["REPO_DIR"] = str(repo)
    env["MEDITATION_DRYRUN"] = "1"
    env["MAX_CYCLES"] = "1"
    env["MEDITATION_WORKTREE"] = "0"
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_a_clean_reconcile_run_emits_no_warning_line(tmp_path):
    repo = _init_repo(tmp_path)
    _stub_reconcile_state(repo, exit_code=0)

    result = _run_script(repo, tmp_path)

    output = result.stdout + result.stderr
    assert "[meditation] reconcile_state exited" not in output


def test_a_failing_reconcile_run_reports_its_real_exit_code_not_a_generic_message(tmp_path):
    """The core regression proof: the OLD code's generic 'completed with
    warnings' message is gone, replaced by the actual captured exit code."""
    repo = _init_repo(tmp_path)
    _stub_reconcile_state(repo, exit_code=7)

    result = _run_script(repo, tmp_path)

    output = result.stdout + result.stderr
    assert "[meditation] reconcile_state exited 7" in output
    assert "completed with warnings" not in output, (
        "the old generic message must be gone -- a real exit code is required now"
    )


def test_a_missing_reconcile_state_script_is_reported_not_silently_swallowed(tmp_path):
    """The sharpest case: bin/reconcile_state.py does not exist at all (a
    total-crash scenario, not a soft warning). The old code's blanket
    'completed with warnings' phrasing would have read identically to a real
    partial-success run; this must be visibly different and must not silently
    pass as if nothing happened."""
    repo = _init_repo(tmp_path)
    # deliberately do NOT call _stub_reconcile_state -- the file is absent.

    result = _run_script(repo, tmp_path)

    output = result.stdout + result.stderr
    assert "[meditation] reconcile_state exited" in output
    # python3 exits 2 on "can't open file" -- assert SOME nonzero code was
    # captured and surfaced, not asserting the exact number (that's a Python
    # interpreter implementation detail, not this fix's contract).
    assert "exited 0" not in output


def test_the_reconcile_call_never_aborts_the_whole_cycle(tmp_path):
    """set -euo pipefail is active in this script; a naive `cmd` without the
    `|| capture` idiom would abort the entire run on a nonzero exit. The
    script must reach past this call and exit 0 (dry-run, one cycle) even
    when reconcile_state.py fails."""
    repo = _init_repo(tmp_path)
    _stub_reconcile_state(repo, exit_code=1)

    result = _run_script(repo, tmp_path)

    assert result.returncode == 0, (
        "a failing reconcile_state.py must not abort the whole meditation cycle: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
