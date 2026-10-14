"""Bundled licensing takes precedence over a planted parent package."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from _layout import governance_root

ROOT = Path(__file__).resolve().parents[1]
GOVERNANCE = governance_root(__file__)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    env = {
        "HOME": str(home), "SAMURAI_HOME": str(home / ".samurai"),
        "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    monkeypatch.setattr(os, "environ", env.copy())
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return env


def _clone(target, *, bundled=True):
    for name in ("bin", "execution"):
        shutil.copytree(ROOT / name, target / name, ignore=shutil.ignore_patterns("__pycache__"))
    if bundled:
        shutil.copytree(GOVERNANCE / "agentica_core", target / "agentica_core",
                        ignore=shutil.ignore_patterns("__pycache__"))
    return target


def _plant(parent, marker):
    package = parent / "agentica_core"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "licensing.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('imported')\n"
        "def status(): return {'tier': 'free'}\n"
        "def is_pro(): return False\n",
        encoding="utf-8",
    )


def test_cli_prefers_bundled_licensing(tmp_path, isolated):
    clone = _clone(tmp_path / "p" / "clone")
    marker = tmp_path / "marker"
    _plant(clone.parent, marker)
    result = subprocess.run(
        [sys.executable, "-I", str(clone / "bin" / "samurai"), "license"],
        cwd=tmp_path, env=isolated, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not marker.exists(), "CLI imported the planted parent licensing module"


def test_shell_gate_prefers_bundled_licensing(tmp_path, isolated):
    clone = _clone(tmp_path / "p" / "clone")
    marker = tmp_path / "marker"
    _plant(clone.parent, marker)
    state = Path(isolated["SAMURAI_HOME"])
    state.mkdir()
    (state / "license.json").write_text(json.dumps({
        "tier": "pro", "valid": True, "status": "active", "simulated": True,
        "refunded": False,
    }))
    result = subprocess.run(
        ["/bin/bash", "-c", 'source "$1"; is_pro', "test-gate",
         str(clone / "bin" / "lib_pro_gate.sh")],
        cwd=tmp_path, env=isolated, capture_output=True, text=True, timeout=30,
    )
    assert not marker.exists(), "Pro gate imported the planted parent licensing module"
    assert result.returncode == 0, result.stdout + result.stderr


def test_monorepo_sibling_licensing_remains_supported(tmp_path, isolated):
    clone = _clone(tmp_path / "m" / "OrderSamurai", bundled=False)
    shutil.copytree(GOVERNANCE / "agentica_core", clone.parent / "agentica_core",
                    ignore=shutil.ignore_patterns("__pycache__"))
    result = subprocess.run(
        [sys.executable, "-I", str(clone / "bin" / "samurai"), "license"],
        cwd=tmp_path, env=isolated, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Tier: FREE" in result.stdout
