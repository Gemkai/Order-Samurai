"""refresh_dashboard.py runs during every web install, BEFORE `samurai install` detects
harnesses. It must not create ~/.claude on a machine without Claude Code: a created
~/.claude reads as a Claude install, so a Codex-only buyer got Claude hooks and
"Restart Claude Code" (v2.4.1)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _refresh(tmp_path: Path) -> tuple[Path, subprocess.CompletedProcess[str]]:
    root = tmp_path / "installed-copy"
    root.mkdir()
    for directory in ("bin", "agentica_core", "config", "schema"):
        shutil.copytree(ROOT / directory, root / directory, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(ROOT / "refresh_dashboard.py", root / "refresh_dashboard.py")
    (root / "dashboard-ui" / "public").mkdir(parents=True)
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "SAMURAI_NO_UPDATE_CHECK": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable, "refresh_dashboard.py"], cwd=root, env=env,
                            capture_output=True, text=True, timeout=120)
    return home, result


def test_refresh_on_a_machine_without_claude_does_not_create_claude_home(tmp_path):
    home, result = _refresh(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (home / ".claude").exists(), sorted(str(p) for p in (home / ".claude").rglob("*"))
