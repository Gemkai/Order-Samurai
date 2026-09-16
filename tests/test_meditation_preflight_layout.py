"""The installed preflight must work in monorepo and exported layouts."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "agentica_core" if (ROOT / "agentica_core").is_dir() else ROOT.parent / "agentica_core"


@pytest.mark.parametrize("layout", ["monorepo", "standalone"])
@pytest.mark.parametrize("condition", ["current", "stale", "missing", "paused"])
def test_preflight_installed_layout(tmp_path: Path, layout: str, condition: str) -> None:
    package_root = tmp_path / "installation"
    runner_root = package_root / "Order Samurai" if layout == "monorepo" else package_root
    (runner_root / "bin").mkdir(parents=True)
    shutil.copytree(CORE, package_root / "agentica_core", ignore=shutil.ignore_patterns("tests", "__pycache__"))
    script = runner_root / "bin" / "meditation_preflight.py"
    shutil.copy2(ROOT / "bin" / script.name, script)
    state = runner_root / "state"
    state.mkdir()
    if condition != "missing":
        ledger = {"date": datetime.now(timezone.utc).date().isoformat(), "spent_usd": 1, "daily_limit_usd": 5}
        if condition == "stale":
            ledger["date"] = "2000-01-01"
        (state / "budget_ledger.json").write_text(json.dumps(ledger))
    if condition == "paused":
        (state / "MEDITATION_STOP").write_text("operator pause")
    before = {p.name: p.read_bytes() for p in state.iterdir()}
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    result = subprocess.run([sys.executable, str(script), "--state-dir", str(state)], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == (0 if condition == "current" else 2), result.stderr
    assert "Traceback" not in result.stderr
    assert {p.name: p.read_bytes() for p in state.iterdir()} == before
