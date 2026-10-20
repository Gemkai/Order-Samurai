"""Acceptance coverage for honest dashboard data on fresh installs."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _copy_install_tree(tmp_path: Path) -> Path:
    install_root = tmp_path / "installed-copy"
    install_root.mkdir()
    for directory in ("bin", "agentica_core", "config", "schema"):
        shutil.copytree(
            ROOT / directory,
            install_root / directory,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    shutil.copy2(ROOT / "refresh_dashboard.py", install_root / "refresh_dashboard.py")
    public = install_root / "dashboard-ui" / "public"
    public.mkdir(parents=True)
    shutil.copy2(ROOT / "dashboard-ui" / "public" / "wid_payload.json", public / "wid_payload.json")
    return install_root


def _installer_env(tmp_path: Path, install_root: Path) -> tuple[dict[str, str], Path, Path, Path]:
    home = tmp_path / "home"
    commands = tmp_path / "commands"
    logs = tmp_path / "empty-claude-logs"
    codex = tmp_path / "empty-codex-logs"
    for directory in (home, commands, logs, codex):
        directory.mkdir()

    # Dependency checks succeed without downloads; every product script still runs
    # through the real test interpreter.
    wrapper = commands / "selected-python"
    first_report_observation = tmp_path / "payload-seen-by-first-report.json"
    wrapper.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "if sys.argv[1:3] == ['-m', 'pip']:\n"
        "    raise SystemExit('unexpected package installation')\n"
        "if sys.argv[1:3] in (['-c', 'import jsonschema'], ['-c', 'import requests'], ['-c', 'import pip_audit']):\n"
        "    raise SystemExit(1 if os.environ.get('ACCEPTANCE_DEPS_FAIL') else 0)\n"
        "if len(sys.argv) > 1 and sys.argv[1].endswith('/bin/first_blood.py'):\n"
        "    payload = os.environ['ACCEPTANCE_PAYLOAD']\n"
        "    observed = {}\n"
        "    if os.path.exists(payload):\n"
        "        with open(payload, encoding='utf-8') as source:\n"
        "            observed = json.load(source)\n"
        "    with open(os.environ['ACCEPTANCE_OBSERVATION'], 'w', encoding='utf-8') as target:\n"
        "        json.dump({'timestamp': observed.get('timestamp')}, target)\n"
        "os.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    env = {
        "HOME": str(home),
        "CODEX_HOME": str(home / ".codex"),
        "SAMURAI_HOME": str(home / ".samurai"),
        "SAMURAI_ROOT": str(install_root),
        "ORDER_SAMURAI_ROOT": str(install_root),
        "SAMURAI_CODEX_APP_BIN": str(tmp_path / "absent-codex"),
        "SAMURAI_APPLICATIONS_DIR": str(tmp_path / "absent-apps"),
        "SAMURAI_NO_UPDATE_CHECK": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PATH": f"{commands}:/usr/bin:/bin",
        "PYTHON": str(wrapper),
        "ACCEPTANCE_PAYLOAD": str(install_root / "dashboard-ui" / "public" / "wid_payload.json"),
        "ACCEPTANCE_OBSERVATION": str(first_report_observation),
    }
    return env, logs, codex, first_report_observation


def _run_installer(tmp_path: Path, install_root: Path, **extra: str) -> tuple[subprocess.CompletedProcess[str], Path]:
    env, logs, codex, observation = _installer_env(tmp_path, install_root)
    env.update(extra)
    result = subprocess.run(
        [
            "/bin/bash",
            str(install_root / "bin" / "install.sh"),
            "--logs-dir",
            str(logs),
            "--codex-dir",
            str(codex),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result, observation


def test_fresh_install_generates_current_zero_record_payload_before_first_report(tmp_path):
    install_root = _copy_install_tree(tmp_path)
    payload_path = install_root / "dashboard-ui" / "public" / "wid_payload.json"
    shipped_demo = payload_path.read_bytes()
    started = datetime.now(timezone.utc)

    result, observation = _run_installer(tmp_path, install_root)

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    assert payload_path.read_bytes() != shipped_demo
    generated_at = datetime.fromisoformat(payload["timestamp"].replace("Z", "+00:00"))
    assert started <= generated_at <= datetime.now(timezone.utc)
    assert json.loads(observation.read_text(encoding="utf-8"))["timestamp"] == payload["timestamp"]
    assert payload["record_counts"]
    assert set(payload["record_counts"].values()) == {0}
    report = result.stdout[result.stdout.index("Order Samurai -- ") :]
    # No harness is installed here, so the message names both scanned harnesses. (Before
    # v2.4.2 the refresh created ~/.claude, which made this machine look like Claude Code.)
    assert report.startswith("Order Samurai -- No Claude Code or Codex session logs found")
    assert "--logs-dir or --codex-dir" in report
    assert result.stdout.count("Order Samurai -- ") == 1


def test_refresh_failure_is_nonfatal_and_removes_demo_from_live_payload_path(tmp_path):
    install_root = _copy_install_tree(tmp_path)
    public = install_root / "dashboard-ui" / "public"
    shipped_demo = (public / "wid_payload.json").read_bytes()
    (install_root / "refresh_dashboard.py").write_text("raise SystemExit(1)\n", encoding="utf-8")

    result, _ = _run_installer(tmp_path, install_root)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not (public / "wid_payload.json").exists()
    assert (public / "wid_payload.demo.json").read_bytes() == shipped_demo
    assert result.stdout.count("Order Samurai -- ") == 1
    assert "refresh" in (result.stdout + result.stderr).lower()


@pytest.mark.parametrize("git_marker", ["directory", "file"])
def test_developer_checkout_preserves_tracked_demo_payload(tmp_path, git_marker):
    install_root = _copy_install_tree(tmp_path)
    payload_path = install_root / "dashboard-ui" / "public" / "wid_payload.json"
    shipped_demo = payload_path.read_bytes()
    marker = install_root / ".git"
    if git_marker == "directory":
        marker.mkdir()
    else:
        marker.write_text("gitdir: /tmp/example-worktree\n", encoding="utf-8")

    result, _ = _run_installer(tmp_path, install_root)

    assert result.returncode == 0, result.stdout + result.stderr
    assert payload_path.read_bytes() == shipped_demo
    assert not (payload_path.parent / "wid_payload.demo.json").exists()


def test_dependency_failure_still_hides_the_demo_payload(tmp_path):
    """bin/install.sh exits early when report dependencies cannot be installed; the
    website installer carries on, so the demo must already be out of the live path."""
    install_root = _copy_install_tree(tmp_path)
    public = install_root / "dashboard-ui" / "public"
    shipped_demo = (public / "wid_payload.json").read_bytes()

    result, _ = _run_installer(tmp_path, install_root, ACCEPTANCE_DEPS_FAIL="1")

    assert result.returncode != 0, result.stdout + result.stderr
    assert not (public / "wid_payload.json").exists()
    assert (public / "wid_payload.demo.json").read_bytes() == shipped_demo
