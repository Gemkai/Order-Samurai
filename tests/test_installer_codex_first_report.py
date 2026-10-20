"""Exercise the shell installer's real first-report handoff without package downloads."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_installer_scans_explicit_codex_logs_and_preserves_extractable_report(tmp_path, monkeypatch):
    home, commands = tmp_path / "home", tmp_path / "commands"
    home.mkdir()
    commands.mkdir()
    codex = commands / "codex"
    codex.write_text("#!/bin/sh\nexit 0\n")
    codex.chmod(0o755)
    wrapper = commands / "report-python"
    wrapper.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "if sys.argv[1:3] == ['-m', 'pip']:\n"
        "    raise SystemExit('unexpected package installation')\n"
        "if sys.argv[1:3] in (['-c', 'import requests'], ['-c', 'import pip_audit']):\n"
        "    raise SystemExit(0)\n"
        "os.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n"
    )
    wrapper.chmod(0o755)
    sessions = tmp_path / "chosen sessions"
    sessions.mkdir()
    (sessions / "rollout-smoke.jsonl").write_text("\n".join(map(json.dumps, [
        {"type": "session_meta", "payload": {"id": "smoke", "cwd": "/shop"}},
        {"type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": {"input_tokens": 123, "cached_input_tokens": 12, "output_tokens": 34}}}},
    ])) + "\n")
    env = {
        "HOME": str(home), "CODEX_HOME": str(home / "default-codex"),
        "SAMURAI_HOME": str(home / ".samurai"), "SAMURAI_ROOT": str(ROOT),
        "SAMURAI_CODEX_APP_BIN": str(tmp_path / "absent-codex"),
        "SAMURAI_APPLICATIONS_DIR": str(tmp_path / "absent-apps"),
        "SAMURAI_NO_UPDATE_CHECK": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "PATH": f"{commands}:/usr/bin:/bin", "PYTHON": str(wrapper),
    }
    for name in tuple(os.environ):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    result = subprocess.run(["/bin/bash", str(ROOT / "bin" / "install.sh"),
                             "--codex-dir", str(sessions)], env=env, cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Claude Code" not in result.stdout
    assert "Codex" in result.stdout
    report = result.stdout[result.stdout.index("Order Samurai -- "):]
    assert "unavailable" in report.lower()
    assert "$" not in report
    assert "123" in report and "34" in report
    assert "sessions scanned:" in report.lower()
