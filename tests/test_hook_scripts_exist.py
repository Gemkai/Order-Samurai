"""`samurai install` must register hook commands that point at scripts that exist.

Regression (2026-10-04): install registered `<root>/hooks/prompt_injection_guard.py`,
but the scripts ship in `<root>/bin/`. Python exits 2 on a missing script, and Claude
Code treats a PreToolUse exit 2 as "block", so every guarded tool call was blocked.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMURAI = ROOT / "bin" / "samurai"


def _install(tmp_path: Path) -> dict:
    env = dict(os.environ, HOME=str(tmp_path), SAMURAI_HOME=str(tmp_path / ".samurai"),
               SAMURAI_NO_PROMPT="1")
    env.pop("SAMURAI_LICENSE_KEY", None)
    r = subprocess.run([sys.executable, str(SAMURAI), "install"], env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads((tmp_path / ".claude" / "settings.json").read_text())


def _commands(settings: dict):
    for event, entries in settings["hooks"].items():
        for entry in entries:
            for hook in entry["hooks"]:
                yield event, hook["command"]


def test_every_registered_hook_script_exists(tmp_path):
    cmds = list(_commands(_install(tmp_path)))
    assert {e for e, _ in cmds} >= {"PreToolUse", "PostToolUse"}
    for event, cmd in cmds:
        script = shlex.split(cmd)[-1]
        assert Path(script).is_file(), f"{event} hook points at a missing script: {cmd}"


def test_guard_hook_allows_a_benign_tool_call(tmp_path):
    settings = _install(tmp_path)
    (cmd,) = [c for e, c in _commands(settings) if e == "PreToolUse"]
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})
    env = dict(os.environ, HOME=str(tmp_path), SAMURAI_HOME=str(tmp_path / ".samurai"))
    r = subprocess.run(cmd, shell=True, input=payload, capture_output=True, text=True,
                       env=env, timeout=30)
    assert r.returncode == 0, f"benign `ls` blocked (exit {r.returncode}): {r.stderr[:300]}"


def test_doctor_fails_when_a_registered_hook_script_is_missing(tmp_path):
    settings = _install(tmp_path)
    for entries in settings["hooks"].values():
        for entry in entries:
            for hook in entry["hooks"]:
                hook["command"] = "python3 /nonexistent/prompt_injection_guard.py"
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps(settings))
    env = dict(os.environ, HOME=str(tmp_path), SAMURAI_HOME=str(tmp_path / ".samurai"))
    r = subprocess.run([sys.executable, str(SAMURAI), "doctor"], env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode != 0
    assert "missing" in r.stdout.lower()


def test_hooks_work_from_a_path_with_spaces(tmp_path):
    """The owner's own clone lives in 'Order Samurai(product)'."""
    import shutil
    root = tmp_path / "Order Samurai(product)"
    shutil.copytree(ROOT / "bin", root / "bin")
    for d in ("agentica_core", "state", "execution", "config"):
        if (ROOT / d).exists():
            shutil.copytree(ROOT / d, root / d, ignore=shutil.ignore_patterns("__pycache__"))
    env = dict(os.environ, HOME=str(tmp_path), SAMURAI_HOME=str(tmp_path / ".samurai"),
               SAMURAI_NO_PROMPT="1")
    r = subprocess.run([sys.executable, str(root / "bin" / "samurai"), "install"], env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    (cmd,) = [c for e, c in _commands(settings) if e == "PreToolUse"]
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})
    r = subprocess.run(cmd, shell=True, input=payload, capture_output=True, text=True,
                       env=env, timeout=30)
    assert r.returncode == 0, f"{cmd!r} -> exit {r.returncode}: {r.stderr[:200]}"
