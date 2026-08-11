"""Upgrade / rollback tests for the Order Samurai installer.

Exercises the SITE installer (order-samurai-landing/install.sh -- downloads +
checksum-verifies + extracts order-samurai-core.zip) together with bin/samurai
(hook registration). The site installer is read-only input here; it is never
modified by this test.

The zip is served from a LOCAL http.server on a random high port, with
OS_BASE_URL pointed at it. This is load-bearing: install.sh defaults
OS_BASE_URL to the production site, so an unset OS_BASE_URL here would
silently hit the live build instead of the one under test.

Skips (does not fail) when dist/order-samurai-core.zip hasn't been built --
run `bash bin/build_core_zip.sh` first to produce it.
"""
from __future__ import annotations

import functools
import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _layout import governance_root  # noqa: E402

ROOT = governance_root(__file__)

DIST_DIR = ROOT / "dist"
DIST_ZIP = DIST_DIR / "order-samurai-core.zip"
DIST_SHA = DIST_DIR / "order-samurai-core.zip.sha256"
SITE_INSTALL_SH = Path(
    "/Users/jemakaiblyden/Desktop/Solutions/order-samurai-landing/install.sh"
)


def _require_fixtures():
    if not DIST_ZIP.exists() or not DIST_SHA.exists():
        pytest.skip(
            f"local {DIST_ZIP.name} (or its .sha256 sidecar) is not built -- "
            f"run 'bash bin/build_core_zip.sh' first; skipping installer upgrade test"
        )
    if not SITE_INSTALL_SH.exists():
        pytest.skip(f"site installer not found at {SITE_INSTALL_SH}; skipping")


@pytest.fixture
def local_zip_server():
    """Serve dist/ over HTTP on a random high port. Yields the base URL."""
    _require_fixtures()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(DIST_DIR))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _run_site_installer(home: Path, base_url: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["HOME"] = str(home)
    # Load-bearing: without this, install.sh's default OS_BASE_URL points at
    # the production site and this test would silently exercise the LIVE build.
    env["OS_BASE_URL"] = base_url
    return subprocess.run(
        ["bash", str(SITE_INSTALL_SH)],
        capture_output=True, text=True, env=env,
    )


# --------------------------------------------------------------------------- #
# 1. Installing over an existing install backs up the previous core, and the
#    backup is restorable.
# --------------------------------------------------------------------------- #

def test_reinstall_backs_up_existing_core_and_is_restorable(tmp_path, local_zip_server):
    home = tmp_path / "home"
    home.mkdir()
    core_dir = home / ".samurai" / "core"

    # First install.
    res1 = _run_site_installer(home, local_zip_server)
    assert res1.returncode == 0, res1.stderr
    assert core_dir.exists()
    samurai_bin = core_dir / "bin" / "samurai"
    assert samurai_bin.exists()
    original_bytes = samurai_bin.read_bytes()

    # Second install over the existing one (upgrade path).
    res2 = _run_site_installer(home, local_zip_server)
    assert res2.returncode == 0, res2.stderr
    assert "Backed up existing install" in res2.stdout

    backups = sorted((home / ".samurai").glob("core.bak-*"))
    assert len(backups) == 1, f"expected exactly one core.bak-<timestamp>, found {backups}"
    backup_dir = backups[0]

    # The new core is present and functional post-upgrade.
    assert core_dir.exists()
    assert (core_dir / "bin" / "samurai").exists()

    # The backup preserves the previous core and is restorable.
    backed_up_bin = backup_dir / "bin" / "samurai"
    assert backed_up_bin.exists()
    assert backed_up_bin.read_bytes() == original_bytes

    # Simulate a rollback: swap the new core out for the backed-up one.
    import shutil
    rollback_dir = home / ".samurai" / "core.rolled-back"
    shutil.copytree(backup_dir, rollback_dir)
    assert (rollback_dir / "bin" / "samurai").read_bytes() == original_bytes


# --------------------------------------------------------------------------- #
# 2. Re-running `samurai install` is idempotent -- no duplicate hook entries.
# --------------------------------------------------------------------------- #

def test_repeated_samurai_install_is_idempotent(tmp_path, local_zip_server):
    home = tmp_path / "home"
    home.mkdir()

    res = _run_site_installer(home, local_zip_server)
    assert res.returncode == 0, res.stderr

    samurai_bin = home / ".samurai" / "core" / "bin" / "samurai"
    assert samurai_bin.exists()

    env = dict(os.environ)
    env["HOME"] = str(home)

    for _ in range(2):
        res_install = subprocess.run(
            [sys.executable, str(samurai_bin), "install"],
            capture_output=True, text=True, env=env,
        )
        assert res_install.returncode == 0, res_install.stderr

    settings_path = home / ".claude" / "settings.json"
    data = json.loads(settings_path.read_text())
    pre_entries = data.get("hooks", {}).get("PreToolUse", []) or []
    guard_entries = [
        e for e in pre_entries
        if isinstance(e, dict) and any(
            isinstance(h, dict) and "prompt_injection_guard" in str(h.get("command", ""))
            for h in (e.get("hooks") or [])
        )
    ]
    assert len(guard_entries) == 1, f"expected exactly one guard entry after two installs, found {len(guard_entries)}"


# --------------------------------------------------------------------------- #
# 3. A path containing spaces works end to end.
# --------------------------------------------------------------------------- #

def test_install_with_space_in_home_path(tmp_path, local_zip_server):
    home = tmp_path / "My Home Dir"
    home.mkdir()

    res = _run_site_installer(home, local_zip_server)
    assert res.returncode == 0, res.stderr

    samurai_bin = home / ".samurai" / "core" / "bin" / "samurai"
    assert samurai_bin.exists()

    env = dict(os.environ)
    env["HOME"] = str(home)
    res_install = subprocess.run(
        [sys.executable, str(samurai_bin), "install"],
        capture_output=True, text=True, env=env,
    )
    assert res_install.returncode == 0, res_install.stderr
    assert (home / ".claude" / "settings.json").exists()
