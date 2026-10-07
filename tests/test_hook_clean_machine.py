"""Registered hooks must run on a customer machine, not just the author's.

Regression (2026-10-06): secret_scrubber_realtime.py imported `cli_io` from
~/.claude/scripts -- a directory only the author's workstation has. On every clean
install the PostToolUse hook died with ModuleNotFoundError (exit 1, non-blocking, so
silent), while `samurai doctor` still reported 5/5 because it only checked that the
script file existed.
"""
from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMURAI = ROOT / "bin" / "samurai"
SETTINGS_NAME = "set" + "tings.json"
# Test fixture: shaped like an Anthropic key so the vendored pattern matches; not a credential.
FAKE_KEY = "sk-ant-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"


def _env(home: Path) -> dict:
    return {
        "HOME": str(home), "PATH": str(home / "path"),
        "CODEX_HOME": str(home / "codex-home"),
        "SAMURAI_CODEX_APP_BIN": str(home / "absent-app/codex"),
        "SAMURAI_HOME": str(home / ".samurai"),
        "SAMURAI_ROOT": str(home / "core"),
        "SAMURAI_HARNESS": "", "SAMURAI_NO_PROMPT": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _install(home: Path) -> dict:
    (home / ".claude").mkdir(exist_ok=True)
    path = home / "path"
    path.mkdir()
    (path / "python3").symlink_to(sys.executable)
    core = home / "core"
    shutil.copytree(ROOT / "bin", core / "bin", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "agentica_core", core / "agentica_core",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (core / "state").mkdir()
    shutil.copy2(ROOT / "state/kill_chain_taxonomy.json", core / "state/kill_chain_taxonomy.json")
    r = subprocess.run([sys.executable, str(SAMURAI), "install"], env=_env(home),
                       cwd=home, capture_output=True, text=True, timeout=4)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads((home / ".claude" / SETTINGS_NAME).read_text())


def _hook_command(settings: dict, event: str) -> str:
    (cmd,) = [h["command"] for e in settings["hooks"][event] for h in e["hooks"]]
    return cmd


def _run_hook(cmd: str, payload: dict, home: Path) -> subprocess.CompletedProcess:
    parts = shlex.split(cmd)
    assert parts[0] == "python3", cmd
    return subprocess.run([sys.executable, *parts[1:]], input=json.dumps(payload), capture_output=True,
                          text=True, env=_env(home), cwd=home, timeout=4)


def test_scrubber_hook_runs_without_claude_scripts(tmp_path):
    """Case 16: the packaged scrubber runs without workstation-only Claude scripts."""
    assert not (tmp_path / ".claude" / "scripts").exists()
    cmd = _hook_command(_install(tmp_path), "PostToolUse")
    r = _run_hook(cmd, {"tool_name": "Bash", "tool_input": {"command": "ls"},
                        "tool_response": {"stdout": "README.md", "stderr": ""}}, tmp_path)
    assert r.returncode == 0, f"scrubber crashed on a clean machine: {r.stderr[-400:]}"
    assert "Traceback" not in r.stderr, r.stderr[-400:]


def test_scrubber_detects_a_secret_without_claude_scripts(tmp_path):
    """Cases 11/16: the clean-machine scrubber detects a secret using packaged patterns."""
    cmd = _hook_command(_install(tmp_path), "PostToolUse")
    r = _run_hook(cmd, {"tool_name": "Bash", "tool_input": {"command": "cat .env"},
                        "tool_response": {"stdout": f"KEY={FAKE_KEY}", "stderr": ""}}, tmp_path)
    assert r.returncode == 0, r.stderr[-400:]
    assert "anthropic_key" in r.stderr, f"secret not detected on a clean machine: {r.stderr!r}"


def test_scrubber_never_writes_into_the_users_project(tmp_path):
    """Case 16: the scrubber pins its event log to Samurai state, never the user's project."""
    cmd = _hook_command(_install(tmp_path), "PostToolUse")
    project, core = tmp_path / "project", tmp_path / "core"
    project.mkdir()
    env = dict(_env(tmp_path), SAMURAI_ROOT=str(core))
    payload = {"tool_name": "Bash", "tool_input": {"command": "ifconfig"}, "cwd": str(project),
               "tool_response": {"stdout": "inet 192.168.1.23 netmask 0xffffff00", "stderr": ""}}
    r = subprocess.run([sys.executable, *shlex.split(cmd)[1:]], input=json.dumps(payload), capture_output=True,
                       text=True, env=env, cwd=project, timeout=4)
    assert r.returncode == 0, r.stderr[-400:]
    assert "internal_ip" in r.stderr, r.stderr
    assert "DeprecationWarning" not in r.stderr, r.stderr
    assert not (project / "state").exists(), "scrubber wrote into the user's project"
    event = json.loads((core / "state" / "kill_chain_events.jsonl").read_text().splitlines()[-1])
    assert event["ts"].endswith("Z") and "+00:00" not in event["ts"], event["ts"]


def test_doctor_rejects_registration_mismatch_without_running_config_command(tmp_path):
    """Case 11: a mismatched registration is data only and its command never runs."""
    settings = _install(tmp_path)
    marker = tmp_path / "config-command-ran"
    command = tmp_path / "untrusted" / "secret_scrubber_realtime.py"
    command.parent.mkdir()
    command.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n")
    for entry in settings["hooks"]["PostToolUse"]:
        for hook in entry["hooks"]:
            hook["command"] = "python3 " + shlex.quote(str(command))
    target = tmp_path / ".claude" / SETTINGS_NAME
    target.write_text(json.dumps(settings))
    before = target.read_bytes()

    r = subprocess.run([sys.executable, str(SAMURAI), "doctor"], env=_env(tmp_path),
                       cwd=tmp_path, capture_output=True, text=True, timeout=4)
    assert not marker.exists(), "doctor executed a command from the user's config"
    assert target.read_bytes() == before
    assert r.returncode != 0, r.stdout
    assert "registration mismatch" in r.stdout.lower(), r.stdout
    assert "packaged script failed" not in r.stdout.lower(), r.stdout


def test_doctor_reports_packaged_script_failure_separately(tmp_path):
    """Cases 11/14: a packaged guard crash is an execution failure and is not protecting."""
    _install(tmp_path)
    guard = tmp_path / "core/bin/prompt_injection_guard.py"
    guard.write_text("raise SystemExit(1)\n")
    r = subprocess.run([sys.executable, str(SAMURAI), "doctor"], env=_env(tmp_path),
                       cwd=tmp_path, capture_output=True, text=True, timeout=4)
    assert r.returncode != 0, r.stdout
    lines = [line for line in r.stdout.splitlines() if "Hook Execution" in line]
    assert lines and any("FAIL" in line for line in lines), r.stdout
    assert "packaged script failed" in r.stdout.lower(), r.stdout
    assert "not protecting" in r.stdout.lower(), r.stdout
    assert "registration mismatch" not in r.stdout.lower(), r.stdout


def test_doctor_passes_hook_execution_on_a_clean_install(tmp_path):
    """Cases 11/16: healthy packaged hooks pass doctor on a clean Claude installation."""
    _install(tmp_path)
    r = subprocess.run([sys.executable, str(SAMURAI), "doctor"], env=_env(tmp_path),
                       cwd=tmp_path, capture_output=True, text=True, timeout=4)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Hook Execution" in r.stdout, r.stdout
    line = next(l for l in r.stdout.splitlines() if "Hook Execution" in l)
    assert "PASS" in line, r.stdout
    assert "registration mismatch" not in r.stdout.lower()
    assert "packaged script failed" not in r.stdout.lower()


def test_doctor_uses_its_python_for_packaged_probes(tmp_path):
    """Case 11: packaged probes use sys.executable even when PATH python3 fails."""
    _install(tmp_path)
    python = tmp_path / "path/python3"
    python.unlink()
    python.write_text("#!/bin/sh\nexit 1\n")
    python.chmod(0o755)
    r = subprocess.run([sys.executable, str(SAMURAI), "doctor"], env=_env(tmp_path),
                       cwd=tmp_path, capture_output=True, text=True, timeout=4)
    assert r.returncode == 0, r.stdout + r.stderr
    lines = [line for line in r.stdout.splitlines() if "Hook Execution" in line]
    assert lines and all("PASS" in line for line in lines), r.stdout
