"""A license key the seller disabled in Gumroad must revoke Pro.

Gumroad answers /v2/licenses/verify for a disabled key with HTTP 404 and
{"success": false, "message": "This license key has been disabled."} (observed live,
2026-10-04). A plain 404 means "key unknown" and stays inconclusive; the disabled
answer is the seller's explicit revocation (e.g. after a partial refund, which the
verify API does not report). No network: urlopen is faked.
"""
from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.request
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
import execution.lemonsqueezy_mcp as lemonsqueezy_mcp  # noqa: E402

FAKE_KEY = "FAKE-TEST-KEY-0000-1111-2222"
NOW = datetime(2026, 10, 4, 18, 0, tzinfo=timezone.utc)


def _http_404(message: str):
    body = json.dumps({"success": False, "message": message}).encode()

    def fake_urlopen(req, *a, **k):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(body))
    return fake_urlopen


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMURAI_HOME", str(tmp_path))
    return tmp_path


def _write_pro(home: Path) -> None:
    (home / "license.json").write_text(json.dumps({
        "tier": "pro", "provider": "gumroad", "valid": True, "status": "active",
        "refunded": False, "license_key": FAKE_KEY, "instance_id": "gum_abc123",
        "activated_at": (NOW - timedelta(hours=2)).isoformat(),
        "purchased_at": (NOW - timedelta(hours=2)).isoformat(),
        "last_checked_at": (NOW - timedelta(hours=2)).isoformat(),
        "settled": False,
    }))


def test_gumroad_reports_a_disabled_key_as_disabled_not_unknown(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _http_404("This license key has been disabled."))
    val = gumroad_mcp.validate_license_key(FAKE_KEY)
    assert val["valid"] is False
    assert val.get("disabled") is True
    assert not val.get("not_found")


def test_gumroad_unknown_key_is_still_not_found(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen",
                        _http_404("That license does not exist for the provided product."))
    val = gumroad_mcp.validate_license_key(FAKE_KEY)
    assert val.get("not_found") is True
    assert not val.get("disabled")


def test_revalidate_revokes_a_disabled_key(home, monkeypatch):
    _write_pro(home)
    monkeypatch.setattr(urllib.request, "urlopen", _http_404("This license key has been disabled."))
    res = licensing.revalidate(force=True, now=NOW)
    assert res == {"tier": "free", "result": "revoked"}
    ent = json.loads((home / "license.json").read_text())
    assert ent["status"] != "active" and ent["valid"] is False
    assert licensing.is_pro() is False


def test_activate_refuses_a_disabled_key_without_trying_the_fallback(home, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _http_404("This license key has been disabled."))

    def never(*a, **k):
        raise AssertionError("a disabled Gumroad key must not be sent to Lemon Squeezy")
    monkeypatch.setattr(lemonsqueezy_mcp, "validate_license_key", never)
    res = licensing.activate(FAKE_KEY, instance_name="test-host")
    assert res["ok"] is False
    assert "disabled" in res["message"].lower()
    assert FAKE_KEY not in res["message"]
    assert not (home / "license.json").exists()
