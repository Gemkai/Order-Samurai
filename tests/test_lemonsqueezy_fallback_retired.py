"""New activations use Gumroad only; existing LS entitlements still revalidate."""
from __future__ import annotations

import io
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

from _layout import governance_root

GUMROAD = "https://api.gumroad.com/v2/licenses/verify"
LS_VALIDATE = "https://api.lemonsqueezy.com/v1/licenses/validate"
LS_ACTIVATE = "https://api.lemonsqueezy.com/v1/licenses/activate"
VALID = {
    "valid": True, "license_key": {"status": "active"},
    "meta": {"store_id": 999999, "product_id": 1, "customer_email": "dummy@example.invalid"},
}


@pytest.fixture
def licensing_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    state = home / ".samurai"
    state.mkdir(parents=True)
    monkeypatch.setattr(os, "environ", {"HOME": str(home), "SAMURAI_HOME": str(state)})
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.syspath_prepend(str(governance_root(__file__)))
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("unmocked URL"))
    from agentica_core import licensing
    return licensing, state


def _response(body):
    return io.BytesIO(json.dumps(body).encode("utf-8"))


def test_unknown_gumroad_key_never_activates_through_lemonsqueezy(licensing_env, monkeypatch):
    licensing, state = licensing_env
    urls = []

    def urlopen(request, **kwargs):
        url = request.full_url
        urls.append(url)
        if url == GUMROAD:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, _response({
                "success": False, "message": "That license does not exist for the provided product.",
            }))
        if url == LS_VALIDATE:
            return _response(VALID)
        if url == LS_ACTIVATE:
            return _response({"activated": True, "instance": {"id": "dummy-instance"}})
        pytest.fail(f"unexpected URL: {url}")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    result = licensing.activate("DUMMY-FOREIGN-KEY", instance_name="test-host")
    pro = licensing.is_pro()
    assert GUMROAD in urls, "activation did not verify with Gumroad"
    assert result["ok"] is False, result
    assert pro is False
    assert not (state / "license.json").exists()
    assert not any("lemonsqueezy" in url for url in urls), urls
    assert "invalid" in result["message"].lower(), result
    assert "network" not in result["message"].lower(), result


def test_existing_lemonsqueezy_entitlement_still_revalidates(licensing_env, monkeypatch):
    licensing, state = licensing_env
    now = datetime.now(timezone.utc)
    (state / "license.json").write_text(json.dumps({
        "tier": "pro", "provider": "lemonsqueezy", "valid": True, "status": "active",
        "refunded": False, "license_key": "DUMMY-KEY-0000", "instance_id": "dummy-instance",
        "purchased_at": (now - timedelta(days=3)).isoformat(),
        "last_checked_at": (now - timedelta(days=2)).isoformat(), "settled": False,
    }))

    def urlopen(request, **kwargs):
        if request.full_url != LS_VALIDATE:
            pytest.fail(f"unexpected URL: {request.full_url}")
        return _response(VALID)

    mocked = Mock(side_effect=urlopen)
    monkeypatch.setattr(urllib.request, "urlopen", mocked)
    result = licensing.revalidate(force=True)
    assert result["result"] == "active", result
    assert result["tier"] == "pro", result
    assert licensing.is_pro() is True
    mocked.assert_called_once()
