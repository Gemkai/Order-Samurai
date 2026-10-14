"""The shell Pro gate must ignore Python modules planted in the caller's CWD."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    samurai_home = home / ".samurai"
    samurai_home.mkdir(parents=True)
    (samurai_home / "license.json").write_text(
        json.dumps({
            "tier": "pro", "valid": True, "status": "active",
            "simulated": True, "refunded": False,
        }),
        encoding="utf-8",
    )
    env = {
        "HOME": str(home),
        "SAMURAI_HOME": str(samurai_home),
        "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin",
    }
    monkeypatch.setattr(os, "environ", env.copy())
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return env


@pytest.mark.parametrize("plant", ["empty", "json", "agentica_core"])
def test_pro_gate_ignores_caller_modules(tmp_path, isolated, plant):
    caller = tmp_path / "caller"
    caller.mkdir()
    marker = tmp_path / "marker"
    write_marker = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('imported', encoding='utf-8')\n"
    )
    if plant == "json":
        (caller / "json.py").write_text(
            write_marker + "raise SystemExit(0)\n", encoding="utf-8",
        )
    elif plant == "agentica_core":
        package = caller / "agentica_core"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "licensing.py").write_text(
            write_marker + "def is_pro():\n    return True\n", encoding="utf-8",
        )

    result = subprocess.run(
        ["/bin/bash", "-c", 'source "$1"; is_pro', "gate",
         str(ROOT / "bin/lib_pro_gate.sh")],
        cwd=caller, env=isolated, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not marker.exists(), f"gate imported caller's {plant} module"
