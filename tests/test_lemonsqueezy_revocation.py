"""Explicit LS revocations revoke; transport/unknown-key answers preserve Pro."""
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

URL = "https://api.lemonsqueezy.com/v1/licenses/validate"
DISABLED = {"valid": False, "error": "This license key is disabled.",
            "license_key": {"status": "disabled"}}
REFUNDED = {"valid": False, "license_key": {"status": "refunded"}}


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
    now = datetime.now(timezone.utc)
    license_file = state / "license.json"
    license_file.write_text(json.dumps({
        "tier": "pro", "provider": "lemonsqueezy", "valid": True, "status": "active",
        "refunded": False, "license_key": "DUMMY-KEY-0000", "instance_id": "dummy-instance",
        "purchased_at": (now - timedelta(days=3)).isoformat(),
        "last_checked_at": (now - timedelta(days=2)).isoformat(), "settled": False,
    }))
    return licensing, license_file


def _mock_answer(monkeypatch, code, body):
    def urlopen(request, **kwargs):
        if request.full_url != URL:
            pytest.fail(f"unexpected URL: {request.full_url}")
        if code is None:
            raise urllib.error.URLError("connection refused")
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        if code >= 400:
            raise urllib.error.HTTPError(URL, code, "provider response", {}, io.BytesIO(data))
        return io.BytesIO(data)

    mocked = Mock(side_effect=urlopen)
    monkeypatch.setattr(urllib.request, "urlopen", mocked)
    return mocked


@pytest.mark.parametrize("code,body", [
    pytest.param(200, DISABLED, id="disabled-200"),
    pytest.param(400, DISABLED, id="disabled-400"),
    pytest.param(200, REFUNDED, id="refunded-200"),
    pytest.param(400, REFUNDED, id="refunded-400"),
])
def test_explicit_revocation_removes_pro(licensing_env, monkeypatch, code, body):
    licensing, license_file = licensing_env
    mocked = _mock_answer(monkeypatch, code, body)
    result = licensing.revalidate(force=True)
    pro = licensing.is_pro()
    stored = json.loads(license_file.read_text())
    mocked.assert_called_once()
    assert result["result"] == "revoked", result
    assert pro is False
    assert stored["status"] != "active", stored


@pytest.mark.parametrize("code,body", [
    pytest.param(None, None, id="connection-refused"),
    pytest.param(400, b"<html>Bad Request</html>", id="non-json-400"),
    pytest.param(404, {"valid": False, "error": "license_key not found.", "license_key": None},
                 id="unknown-key-404"),
])
def test_inconclusive_answer_preserves_pro(licensing_env, monkeypatch, code, body):
    licensing, license_file = licensing_env
    mocked = _mock_answer(monkeypatch, code, body)
    result = licensing.revalidate(force=True)
    pro = licensing.is_pro()
    stored = json.loads(license_file.read_text())
    mocked.assert_called_once()
    assert result["result"] != "revoked", result
    if code is None:
        assert result["result"] == "unreachable", result
    assert pro is True
    assert stored["status"] == "active", stored
