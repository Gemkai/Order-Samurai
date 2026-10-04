"""Regression tests for the security-review findings on the refund-window re-check
(agentica_core/licensing.py revalidate()). Written after review, not test-first:
see docs/handoffs/HANDOFF-order-samurai-refund-revalidation-20261003.md.

Mocks the provider boundary; no network.
"""
from __future__ import annotations

import builtins
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _layout import governance_root  # noqa: E402

ROOT = governance_root(__file__)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import agentica_core.licensing as licensing  # noqa: E402
import execution.gumroad_mcp as gumroad_mcp  # noqa: E402

FAKE_KEY = "FAKE-TEST-KEY-0000-1111-2222"
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
GOOD = {"valid": True, "status": "active", "refunded": False}
REFUNDED = {"valid": False, "status": "refunded", "refunded": True}
OFFLINE = {"valid": False, "error": "Could not reach Gumroad (timed out)."}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMURAI_HOME", str(tmp_path))
    return tmp_path


def _write(home: Path, **overrides) -> None:
    ent = {
        "tier": "pro", "provider": "gumroad", "valid": True, "status": "active",
        "refunded": False, "license_key": FAKE_KEY, "instance_id": "gum_abc123",
        "activated_at": (NOW - timedelta(days=1)).isoformat(),
        "purchased_at": (NOW - timedelta(days=1)).isoformat(),
        "last_checked_at": (NOW - timedelta(days=2)).isoformat(),
        "settled": False,
    }
    ent.update(overrides)
    (home / "license.json").write_text(json.dumps(ent))


def _read(home: Path) -> dict:
    return json.loads((home / "license.json").read_text())


def _provider_that(home: Path, answer: dict, during):
    """A provider fake that mutates license.json mid-call (another process acting)."""
    def fake(*_a, **_k):
        during(home)
        return answer
    return fake


@pytest.mark.parametrize("answer", [GOOD, OFFLINE], ids=["good", "offline"])
def test_stale_result_never_resurrects_a_concurrent_revocation(home, monkeypatch, answer):
    _write(home)
    revoke = lambda h: _write(h, status="refunded", refunded=True, valid=False)  # noqa: E731
    monkeypatch.setattr(gumroad_mcp, "validate_license_key", _provider_that(home, answer, revoke))
    licensing.revalidate(force=True, now=NOW)
    assert _read(home)["status"] == "refunded"
    assert licensing.is_pro() is False


def test_stale_result_never_recreates_a_deactivated_license(home, monkeypatch):
    _write(home)
    monkeypatch.setattr(gumroad_mcp, "validate_license_key",
                        _provider_that(home, GOOD, lambda h: (h / "license.json").unlink()))
    licensing.revalidate(force=True, now=NOW)
    assert not (home / "license.json").exists()


def test_stale_result_never_overwrites_a_newly_activated_key(home, monkeypatch):
    _write(home)
    swap = lambda h: _write(h, license_key="OTHER-KEY-9999", instance_id="gum_new")  # noqa: E731
    monkeypatch.setattr(gumroad_mcp, "validate_license_key", _provider_that(home, REFUNDED, swap))
    licensing.revalidate(force=True, now=NOW)
    ent = _read(home)
    assert ent["license_key"] == "OTHER-KEY-9999" and ent["status"] == "active"


def test_future_last_checked_at_does_not_suppress_the_check(home, monkeypatch):
    _write(home, last_checked_at=(NOW + timedelta(days=30)).isoformat())
    calls = []
    monkeypatch.setattr(gumroad_mcp, "validate_license_key",
                        lambda *a, **k: calls.append(1) or REFUNDED)
    assert licensing.revalidate(now=NOW)["result"] == "revoked"
    assert calls == [1]


def test_provider_import_failure_is_reported_as_error_not_offline(home, monkeypatch):
    _write(home)
    real_import = builtins.__import__

    def broken(name, *a, **k):
        if name.startswith("execution.gumroad_mcp"):
            raise ImportError("simulated broken install")
        return real_import(name, *a, **k)

    monkeypatch.delitem(sys.modules, "execution.gumroad_mcp", raising=False)
    monkeypatch.setattr(builtins, "__import__", broken)
    res = licensing.revalidate(force=True, now=NOW)
    assert res == {"tier": "pro", "result": "error"}


def test_rewrite_keeps_0600_and_leaves_no_tmp_file(home, monkeypatch):
    _write(home)
    monkeypatch.setattr(gumroad_mcp, "validate_license_key", lambda *a, **k: GOOD)
    licensing.revalidate(force=True, now=NOW)
    if os.name == "posix":
        assert (home / "license.json").stat().st_mode & 0o777 == 0o600
    assert not [p for p in home.iterdir() if p.name.endswith(".tmp")]
