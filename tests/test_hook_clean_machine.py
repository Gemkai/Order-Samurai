"""Registered hooks must run on a customer machine, not just the author's.

Regression (2026-10-06): secret_scrubber_realtime.py imported `cli_io` from
~/.claude/scripts -- a directory only the author's workstation has. On every clean
install the PostToolUse hook died with ModuleNotFoundError (exit 1, non-blocking, so
silent), while `samurai doctor` still reported 5/5 because it only checked that the
script file existed.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SAMURAI = ROOT / "bin" / "samurai"
# Test fixture: shaped like an Anthropic key so the vendored pattern matches; not a credential.
FAKE_KEY = "sk-ant-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"


def _env(home: Path) -> dict:
    env = dict(os.environ, HOME=str(home), SAMURAI_HOME=str(home / ".samurai"),
               SAMURAI_NO_PROMPT="1")
    for key in ("SAMURAI_LICENSE_KEY", "SAMURAI_ROOT", "ORDER_SAMURAI_ROOT", "PYTHONPATH"):
        env.pop(key, None)
    return env


def _install(home: Path) -> dict:
    r = subprocess.run([sys.executable, str(SAMURAI), "install"], env=_env(home),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads((home / ".claude" / "settings.json").read_text())


def _hook_command(settings: dict, event: str) -> str:
    (cmd,) = [h["command"] for e in settings["hooks"][event] for h in e["hooks"]]
    return cmd


def _run_hook(cmd: str, payload: dict, home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(shlex.split(cmd), input=json.dumps(payload), capture_output=True,
                          text=True, env=_env(home), timeout=30)


def test_scrubber_hook_runs_without_claude_scripts(tmp_path):
    assert not (tmp_path / ".claude" / "scripts").exists()
    cmd = _hook_command(_install(tmp_path), "PostToolUse")
    r = _run_hook(cmd, {"tool_name": "Bash", "tool_input": {"command": "ls"},
                        "tool_response": {"stdout": "README.md", "stderr": ""}}, tmp_path)
    assert r.returncode == 0, f"scrubber crashed on a clean machine: {r.stderr[-400:]}"
    assert "Traceback" not in r.stderr, r.stderr[-400:]


def test_scrubber_detects_a_secret_without_claude_scripts(tmp_path):
    cmd = _hook_command(_install(tmp_path), "PostToolUse")
    r = _run_hook(cmd, {"tool_name": "Bash", "tool_input": {"command": "cat .env"},
                        "tool_response": {"stdout": f"KEY={FAKE_KEY}", "stderr": ""}}, tmp_path)
    assert r.returncode == 0, r.stderr[-400:]
    assert "anthropic_key" in r.stderr, f"secret not detected on a clean machine: {r.stderr!r}"


def test_scrubber_never_writes_into_the_users_project(tmp_path):
    """The exfil event log is pinned to Order Samurai's own state/, never the session cwd:
    a cwd-relative write dropped untracked state/ folders into customers' repos."""
    cmd = _hook_command(_install(tmp_path), "PostToolUse")
    project, core = tmp_path / "project", tmp_path / "core"
    project.mkdir()
    env = dict(_env(tmp_path), SAMURAI_ROOT=str(core))
    payload = {"tool_name": "Bash", "tool_input": {"command": "ifconfig"}, "cwd": str(project),
               "tool_response": {"stdout": "inet 192.168.1.23 netmask 0xffffff00", "stderr": ""}}
    r = subprocess.run(shlex.split(cmd), input=json.dumps(payload), capture_output=True,
                       text=True, env=env, timeout=30)
    assert r.returncode == 0, r.stderr[-400:]
    assert "internal_ip" in r.stderr, r.stderr
    assert "DeprecationWarning" not in r.stderr, r.stderr
    assert not (project / "state").exists(), "scrubber wrote into the user's project"
    event = json.loads((core / "state" / "kill_chain_events.jsonl").read_text().splitlines()[-1])
    assert event["ts"].endswith("Z") and "+00:00" not in event["ts"], event["ts"]


def test_doctor_fails_when_a_registered_hook_crashes(tmp_path):
    settings = _install(tmp_path)
    broken = tmp_path / "broken" / "secret_scrubber_realtime.py"
    broken.parent.mkdir()
    broken.write_text("import module_that_is_not_installed\n")
    for entry in settings["hooks"]["PostToolUse"]:
        for hook in entry["hooks"]:
            hook["command"] = f"python3 {broken}"
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps(settings))

    r = subprocess.run([sys.executable, str(SAMURAI), "doctor"], env=_env(tmp_path),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode != 0, r.stdout
    assert "secret_scrubber_realtime" in r.stdout and "exited" in r.stdout.lower(), r.stdout


def test_doctor_passes_hook_execution_on_a_clean_install(tmp_path):
    _install(tmp_path)
    r = subprocess.run([sys.executable, str(SAMURAI), "doctor"], env=_env(tmp_path),
                       capture_output=True, text=True, timeout=120)
    assert "Hook Execution" in r.stdout, r.stdout
    line = next(l for l in r.stdout.splitlines() if "Hook Execution" in l)
    assert "PASS" in line, r.stdout


SCRUBBER = ROOT / "bin" / "secret_scrubber_realtime.py"


def _bare_env(home: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["HOME"] = str(home)
    for key in ("SAMURAI_ROOT", "ORDER_SAMURAI_ROOT"):
        env.pop(key, None)
    return env


def _run_bare(home: Path, payload: dict) -> subprocess.CompletedProcess:
    # -S: no site-packages, so anything beyond the stdlib and the shipped tree fails.
    return subprocess.run([sys.executable, "-S", str(SCRUBBER)], input=json.dumps(payload),
                          capture_output=True, text=True, env=_bare_env(home),
                          cwd=str(home), timeout=30)


def test_scrubber_runs_on_a_bare_interpreter(tmp_path):
    r = _run_bare(tmp_path, {"tool_name": "Bash", "tool_input": {"command": "cat .env"},
                             "tool_response": {"stdout": f"KEY={FAKE_KEY}", "stderr": ""}})
    assert r.returncode == 0, r.stderr[-400:]
    assert "anthropic_key" in r.stderr, r.stderr


def test_scrubber_does_not_load_stdio_helper_from_claude_scripts(tmp_path):
    """The hook ships to customers; a developer-only helper must not change how it runs."""
    scripts = tmp_path / ".claude" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "cli_io.py").write_text(
        "def configure_utf8_stdio():\n    raise RuntimeError('developer helper loaded')\n")
    r = _run_bare(tmp_path, {"tool_name": "Bash", "tool_input": {"command": "ls"},
                             "tool_response": {"stdout": "README.md", "stderr": ""}})
    assert r.returncode == 0, r.stderr[-400:]
    assert "developer helper loaded" not in r.stderr, r.stderr[-400:]


@pytest.mark.parametrize("module", ["secret_scrubber", "notify_critical"])
def test_scrubber_does_not_import_developer_modules(tmp_path, module):
    """Patterns and alerts come from the shipped tree only, never ~/.claude/scripts."""
    scripts = tmp_path / ".claude" / "scripts"
    scripts.mkdir(parents=True)
    marker = tmp_path / f"{module}.imported"
    (scripts / f"{module}.py").write_text(f"open({str(marker)!r}, 'w').close()\n")
    r = _run_bare(tmp_path, {"tool_name": "Bash", "tool_input": {"command": "cat .env"},
                             "tool_response": {"stdout": f"KEY={FAKE_KEY}", "stderr": ""}})
    assert r.returncode == 0, r.stderr[-400:]
    assert "anthropic_key" in r.stderr, r.stderr
    assert not marker.exists(), f"hook imported ~/.claude/scripts/{module}.py"


def test_scrubber_detects_secrets_when_samurai_root_is_a_state_dir(tmp_path):
    """SAMURAI_ROOT relocates state; the shipped secret patterns still load from the hook's tree."""
    env = dict(_bare_env(tmp_path), SAMURAI_ROOT=str(tmp_path / "state-only"))
    payload = {"tool_name": "Bash", "tool_input": {"command": "cat .env"},
               "tool_response": {"stdout": f"KEY={FAKE_KEY}", "stderr": ""}}
    r = subprocess.run([sys.executable, "-S", str(SCRUBBER)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=30)
    assert r.returncode == 0, r.stderr[-400:]
    assert "anthropic_key" in r.stderr, r.stderr
