"""`samurai doctor` ends with the support/feedback address, pass or fail.

Owner decision 2026-10-07: one address, support@agentica-llc.biz, for support and
feedback. Doctor is where a buyer looks when something is wrong, so it prints the
address as its very last line. The line never changes the exit code.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FEEDBACK_LINE = "Feedback or problems: support@agentica-llc.biz"


def _machine(tmp_path: Path) -> dict:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    root = tmp_path / "core"
    shutil.copytree(ROOT / "bin", root / "bin", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "agentica_core", root / "agentica_core",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (root / "state").mkdir()
    shutil.copy2(ROOT / "state/kill_chain_taxonomy.json", root / "state/kill_chain_taxonomy.json")
    path = tmp_path / "path"
    path.mkdir()
    (path / "python3").symlink_to(sys.executable)
    return {
        "root": root, "home": home,
        "env": {
            "HOME": str(home), "PATH": str(path), "CODEX_HOME": str(home / ".codex"),
            "SAMURAI_CODEX_APP_BIN": str(tmp_path / "absent-app/codex"),
            "SAMURAI_APPLICATIONS_DIR": str(tmp_path / "Applications"),
            "SAMURAI_HOME": str(home / ".samurai"), "SAMURAI_ROOT": str(root),
            "SAMURAI_HARNESS": "claude", "SAMURAI_NO_PROMPT": "1", "PYTHONDONTWRITEBYTECODE": "1",
        },
    }


def _run(machine: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(machine["root"] / "bin/samurai"), *args],
                          env=machine["env"], cwd=machine["home"], capture_output=True,
                          text=True, timeout=60)


def _lines(result: subprocess.CompletedProcess) -> list[str]:
    return [line for line in result.stdout.splitlines() if line.strip()]


def test_failing_doctor_ends_with_the_feedback_line_and_still_exits_1(tmp_path):
    machine = _machine(tmp_path)  # nothing installed: doctor must fail
    result = _run(machine, "doctor")
    assert result.returncode == 1, result.stdout + result.stderr
    lines = _lines(result)
    assert lines[-1] == FEEDBACK_LINE, result.stdout
    assert lines[-2].startswith("Summary: "), result.stdout


def test_passing_doctor_ends_with_the_feedback_line_and_still_exits_0(tmp_path):
    machine = _machine(tmp_path)
    installed = _run(machine, "install")
    assert installed.returncode == 0, installed.stdout + installed.stderr
    result = _run(machine, "doctor")
    assert result.returncode == 0, result.stdout + result.stderr
    lines = _lines(result)
    assert lines[-1] == FEEDBACK_LINE, result.stdout
    assert lines[-2].startswith("Summary: "), result.stdout
