"""Rerunning the web installer (the update path the notice points to) keeps the buyer's
license, settings, populated runtime state and local env files.

install.sh extracts the zip into ~/.samurai/core with `unzip -o`, which overwrites
shipped code/policy files while preserving runtime seeds and unshipped files. license.json and settings.json live in
~/.samurai itself, outside the extract target; meditation.env and dojo.env are never
shipped (bin/build_core_zip.sh excludes .env files).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_install_checksum_retry import _run_web_installer, _serve, pytestmark  # noqa: E402,F401


def test_rerunning_the_installer_keeps_license_settings_and_env_files(tmp_path):
    server, _ = _serve(stale_zip_responses=0)
    base = f"http://127.0.0.1:{server.server_port}/dist"
    try:
        first = _run_web_installer(tmp_path, base)
        assert first.returncode == 0, (first.stdout + first.stderr)[-2000:]
        samurai = tmp_path / "home" / ".samurai"
        license_bytes = json.dumps({"tier": "pro", "key": "TEST-KEY"}).encode()
        (samurai / "license.json").write_bytes(license_bytes)
        settings = json.loads((samurai / "settings.json").read_text())
        settings["user_preference"] = "keep me"
        (samurai / "settings.json").write_text(json.dumps(settings))
        (samurai / "core" / "meditation.env").write_text("MEDITATION_BUDGET=5\n")
        (samurai / "core" / "dojo.env").write_text("DOJO_X=1\n")
        runtime = {
            "budget_ledger.json": {"date": datetime.now(timezone.utc).date().isoformat(),
                                   "spent_usd": 12.75, "daily_limit_usd": 10, "cycles": 8},
            "vibe_alignment.json": {"score": 82, "sample_count": 17},
            "calibration_coefficients.json": {"operations": {"calibrated": True}},
        }
        for name, value in runtime.items():
            (samurai / "core/state" / name).write_text(json.dumps(value))

        second = _run_web_installer(tmp_path, base)
    finally:
        server.shutdown()
    assert second.returncode == 0, (second.stdout + second.stderr)[-2000:]
    assert (samurai / "license.json").read_bytes() == license_bytes
    assert json.loads((samurai / "settings.json").read_text())["user_preference"] == "keep me"
    assert (samurai / "core" / "meditation.env").read_text() == "MEDITATION_BUDGET=5\n"
    assert (samurai / "core" / "dojo.env").read_text() == "DOJO_X=1\n"
    for name, value in runtime.items():
        assert json.loads((samurai / "core/state" / name).read_text()) == value
