"""Black-box acceptance tests for repository metrics in copied package layouts."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


REAL_GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(REAL_GIT is None, reason="git is required")

STALE_UNAVAILABLE = {
    "val": None,
    "error": "source unavailable: not a git checkout",
    "calibrated": False,
}
LANE_UNAVAILABLE = {
    "val": None,
    "error": "source unavailable: factory ledger missing",
    "calibrated": False,
}


def test_monorepo_layout_counts_live_branches(tmp_path):
    env = _clean_env(tmp_path)
    root = tmp_path / "mono"
    _make_repo(root, env)
    _copy_package(root / "Governance")
    (root / "Governance" / "Order Samurai").mkdir()
    _write_ledger(root)

    metrics, git_log = _read_metrics(tmp_path, root / "Governance", env)

    assert metrics["stale"] == {"val": 2, "calibrated": True}, git_log
    assert metrics["lane"] == {"val": 1, "calibrated": True}
    assert git_log.strip(), "The live metric must invoke git"


def test_export_layout_is_simulated_and_runs_no_git_against_the_parent(tmp_path):
    env = _clean_env(tmp_path)
    buyer, install = _make_export(tmp_path, env)

    metrics, git_log = _read_metrics(tmp_path, install, env)

    assert metrics["stale"] == STALE_UNAVAILABLE, git_log
    assert metrics["lane"] == LANE_UNAVAILABLE
    assert str(buyer) not in git_log, git_log


def test_export_layout_with_monorepo_lookalike_parent_is_still_simulated(tmp_path):
    env = _clean_env(tmp_path)
    buyer, install = _make_export(tmp_path, env)
    (buyer / "Governance" / "Order Samurai").mkdir(parents=True)
    _write_ledger(buyer)

    metrics, git_log = _read_metrics(tmp_path, install, env)

    assert metrics == {"stale": STALE_UNAVAILABLE, "lane": LANE_UNAVAILABLE}, git_log
    assert str(buyer) not in git_log, git_log


def test_export_installed_in_a_folder_named_governance_is_still_simulated(tmp_path):
    env = _clean_env(tmp_path)
    buyer = tmp_path / "buyer"
    _make_repo(buyer, env)
    install = buyer / "Governance"
    _copy_package(install)
    (install / ".export-withdrawn").touch()
    _write_ledger(buyer)

    metrics, git_log = _read_metrics(tmp_path, install, env)

    assert metrics == {"stale": STALE_UNAVAILABLE, "lane": LANE_UNAVAILABLE}, git_log
    assert str(buyer) not in git_log, git_log


def test_export_layout_with_env_override_to_install_is_simulated(tmp_path):
    env = _clean_env(tmp_path)
    buyer, install = _make_export(tmp_path, env)
    env["ORDER_SAMURAI_ROOT"] = str(install)

    metrics, git_log = _read_metrics(tmp_path, install, env)

    assert metrics["stale"] == STALE_UNAVAILABLE, git_log
    assert metrics["lane"] == LANE_UNAVAILABLE
    assert str(buyer) not in git_log, git_log
    assert str(tmp_path) not in git_log, git_log


def _clean_env(tmp_path):
    # Inherited git routing must not escape the temporary repositories.
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
        and key not in {"ORDER_SAMURAI_ROOT", "PYTHONHOME"}
    }
    home = tmp_path / "home"
    home.mkdir()
    env.update(
        HOME=str(home),
        XDG_CONFIG_HOME=str(home / ".config"),
        CLAUDE_RUNTIME_ROOT=str(home / ".claude"),
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_CONFIG_SYSTEM="/dev/null",
        GIT_TERMINAL_PROMPT="0",
        GIT_AUTHOR_DATE="2026-01-01T00:00:00Z",
        GIT_COMMITTER_DATE="2026-01-01T00:00:00Z",
        GIT_AUTHOR_NAME="Fixture Author",
        GIT_COMMITTER_NAME="Fixture Author",
        GIT_AUTHOR_EMAIL="fixture@example.invalid",
        GIT_COMMITTER_EMAIL="fixture@example.invalid",
        PYTHONNOUSERSITE="1",
        PYTHONDONTWRITEBYTECODE="1",
    )
    return env


def _make_repo(root, env):
    root.mkdir()

    def git(*args):
        return subprocess.run(
            [REAL_GIT, *args], cwd=root, env=env, check=True,
            capture_output=True, text=True, timeout=30,
        )

    git("init", "-b", "work")
    git("commit", "--allow-empty", "-m", "Base")
    git("branch", "merged/c")
    for branch in ("feature/a", "feature/b", "backup/old"):
        git("checkout", "-b", branch, "work")
        git("commit", "--allow-empty", "-m", branch)
    git("checkout", "work")


def _copy_package(package_parent):
    shutil.copytree(
        Path(__file__).resolve().parents[1],
        package_parent / "agentica_core",
        ignore=shutil.ignore_patterns("tests", "__pycache__", "*.pyc"),
    )


def _make_export(tmp_path, env):
    buyer = tmp_path / "buyer"
    _make_repo(buyer, env)
    install = buyer / "install"
    _copy_package(install)
    (install / ".export-withdrawn").touch()
    return buyer, install


def _write_ledger(root):
    ledger = root / "Execution" / "factory" / "state" / "ledger.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        json.dumps({
            "event": "ticket_state", "task_id": "T1",
            "state": "awaiting-lane", "ts": "2026-01-01T00:00:00Z",
        }) + "\n",
        encoding="utf-8",
    )


def _read_metrics(tmp_path, package_parent, env):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "git"
    shim.write_text(
        '#!/bin/sh\n'
        '{ printf "%s" "$PWD"; printf " %s" "$@"; printf "\\n"; } >> "$GIT_SHIM_LOG"\n'
        'exec "$REAL_GIT" "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    log = tmp_path / "git.log"
    env = dict(
        env, REAL_GIT=REAL_GIT, GIT_SHIM_LOG=str(log),
        PATH=str(bin_dir) + os.pathsep + env.get("PATH", os.defpath),
    )
    result = subprocess.run(
        [sys.executable, "-c", (
            "import json, sys; "
            "sys.path.insert(0, sys.argv[1]); "
            "import agentica_core.aggregate as a; "
            'print(json.dumps({"stale": a.r_stale_branch_count([]), '
            '"lane": a.r_lane_pending_age([])}))'
        ), str(package_parent)],
        cwd=tmp_path, env=env, check=True, capture_output=True,
        text=True, timeout=60,
    )
    return json.loads(result.stdout), log.read_text(encoding="utf-8") if log.exists() else ""
