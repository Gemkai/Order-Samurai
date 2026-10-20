"""Acceptance tests for the read-only ``samurai version`` command."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SAMURAI = ROOT / "bin" / "samurai"


def _run(tmp_path: Path, *args: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    samurai_home = tmp_path / ".samurai"
    samurai_home.mkdir(exist_ok=True)
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "SAMURAI_HOME": str(samurai_home),
        # If a test accidentally misses the opt-out/fresh-cache contract, fail quickly
        # against a closed local port instead of reaching the published build record.
        "OS_CORE_BASE_URL": "http://127.0.0.1:9",
        **(extra_env or {}),
    }
    return subprocess.run(
        [sys.executable, str(SAMURAI), "version", *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )


def _installed_version() -> str:
    match = re.search(r'^SAMURAI_VERSION = "([^"]+)"', SAMURAI.read_text(encoding="utf-8"), re.MULTILINE)
    assert match, "bin/samurai must keep SAMURAI_VERSION as its version source"
    return match.group(1)


def test_version_json_honours_network_opt_out_and_returns_current_only(tmp_path: Path) -> None:
    result = _run(tmp_path, "--json", extra_env={"SAMURAI_NO_UPDATE_CHECK": "1"})

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "current_version": _installed_version(),
        "latest_version": None,
        "update_available": False,
    }
    assert result.stderr == ""


def test_version_json_uses_fresh_cache_without_network(tmp_path: Path) -> None:
    samurai_home = tmp_path / ".samurai"
    samurai_home.mkdir()
    (samurai_home / "update_check.json").write_text(
        json.dumps({"checked_at": time.time(), "latest_version": "999.0.0"}),
        encoding="utf-8",
    )

    result = _run(tmp_path, "--json", extra_env={"SAMURAI_NO_UPDATE_CHECK": "0"})

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "current_version": _installed_version(),
        "latest_version": "999.0.0",
        "update_available": True,
    }


def test_version_plain_output_is_stable_and_human_readable(tmp_path: Path) -> None:
    result = _run(tmp_path, extra_env={"SAMURAI_NO_UPDATE_CHECK": "1"})

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"Order Samurai v{_installed_version()}\n"
    assert result.stderr == ""
