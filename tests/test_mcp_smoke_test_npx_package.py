"""Regression coverage for bin/mcp_smoke_test.py's npx-package presence check."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]      # Order Samurai
GOV_ROOT = Path(__file__).resolve().parents[2]       # Governance (for agentica_core)
for _p in (REPO_ROOT, GOV_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from bin import mcp_smoke_test  # type: ignore[import-not-found]


def _isolate_fs(monkeypatch, tmp_path):
    # Neither the fake HOME nor the fake cwd contains a node_modules dir,
    # so a real "package present" match can only come from a correct lookup.
    fake_home = tmp_path / "home"
    fake_cwd = tmp_path / "cwd"
    fake_home.mkdir()
    fake_cwd.mkdir()
    monkeypatch.setattr(mcp_smoke_test, "HOME", fake_home)
    monkeypatch.chdir(fake_cwd)


def test_scoped_package_without_version_is_not_reported_present_when_missing(monkeypatch, tmp_path):
    _isolate_fs(monkeypatch, tmp_path)
    # An unrelated npm project's node_modules exists (as it commonly would on
    # a real machine), but the scoped package itself was never installed.
    # An unversioned scoped package (e.g. "@modelcontextprotocol/server-filesystem")
    # must not collapse to an empty package name that matches on the mere
    # existence of *any* node_modules dir.
    (Path.cwd() / "node_modules").mkdir()
    assert mcp_smoke_test._npx_package_present("@modelcontextprotocol/server-filesystem") is False


def test_scoped_package_without_version_is_present_when_actually_installed(monkeypatch, tmp_path):
    _isolate_fs(monkeypatch, tmp_path)
    installed = Path.cwd() / "node_modules" / "@modelcontextprotocol" / "server-filesystem"
    installed.mkdir(parents=True)
    assert mcp_smoke_test._npx_package_present("@modelcontextprotocol/server-filesystem") is True


def test_scoped_package_with_version_still_strips_the_version(monkeypatch, tmp_path):
    _isolate_fs(monkeypatch, tmp_path)
    installed = Path.cwd() / "node_modules" / "@modelcontextprotocol" / "server-filesystem"
    installed.mkdir(parents=True)
    assert mcp_smoke_test._npx_package_present("@modelcontextprotocol/server-filesystem@1.2.3") is True
