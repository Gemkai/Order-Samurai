"""Per-key activation cap: one Gumroad key cannot unlock Pro on unlimited machines.

Canned urllib responses only (same fake as test_license_gumroad_http_e2e.py); never
uses the network."""
from __future__ import annotations

import io
import json
import socket
import sys
import urllib.parse
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
CAP = gumroad_mcp.MAX_ACTIVATIONS


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*_args, **_kwargs):
        pytest.fail("Real network access attempted", pytrace=False)

    monkeypatch.setattr(gumroad_mcp.urllib.request.OpenerDirector, "open", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    for method in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, method, blocked)


@pytest.fixture
def home(tmp_path, monkeypatch):
    samurai_home = tmp_path / "samurai"
    samurai_home.mkdir()
    monkeypatch.setenv("SAMURAI_HOME", str(samurai_home))
    monkeypatch.setenv("SAMURAI_NO_UPDATE_CHECK", "1")
    return samurai_home


class _HTTP:
    def __init__(self):
        self.replies = []
        self.calls = []

    def queue(self, increment, body):
        self.replies.append((increment, body))

    def __call__(self, request, *, timeout):
        if not self.replies:
            pytest.fail("Unexpected urlopen call: no canned response remains")
        increment, body = self.replies.pop(0)
        form = urllib.parse.parse_qs(request.data.decode("utf-8"))
        self.calls.append(form["increment_uses_count"][0])
        if (request.full_url != "https://api.gumroad.com/v2/licenses/verify"
                or form["license_key"] != [FAKE_KEY]
                or form["increment_uses_count"] != [increment]):
            pytest.fail("Unexpected Gumroad HTTP request")
        return io.BytesIO(json.dumps(body).encode("utf-8"))


@pytest.fixture
def http(monkeypatch, no_network):
    fake = _HTTP()
    monkeypatch.setattr(gumroad_mcp.urllib.request, "urlopen", fake)
    yield fake
    assert not fake.replies, "Expected Gumroad HTTP requests were not made"


def _sale(uses):
    sold = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    return {"success": True, "uses": uses, "purchase": {
        "email": "buyer@example.com", "sale_timestamp": sold, "refunded": False}}


def test_cap_is_a_small_positive_number():
    assert isinstance(CAP, int) and 1 <= CAP <= 10


def test_activation_up_to_the_cap_succeeds(http, home):
    http.queue("false", _sale(CAP - 1))
    http.queue("true", _sale(CAP))
    result = licensing.activate(FAKE_KEY, instance_name="third-machine")
    assert result["ok"] is True, result
    assert (home / "license.json").exists()


def test_key_already_at_cap_is_refused_without_consuming_a_seat(http, home):
    http.queue("false", _sale(CAP))
    result = licensing.activate(FAKE_KEY, instance_name="fourth-machine")
    assert result["ok"] is False
    assert http.calls == ["false"]  # no increment_uses_count=true call was made
    assert not (home / "license.json").exists()
    msg = result["message"].lower()
    assert "activation limit" in msg and "support@" in msg and "deactivate" in msg


def test_post_increment_count_over_cap_is_refused(http, home):
    # Two machines racing: the pre-check passes, Gumroad's post-increment count does not.
    http.queue("false", _sale(CAP - 1))
    http.queue("true", _sale(CAP + 1))
    result = licensing.activate(FAKE_KEY, instance_name="racing-machine")
    assert result["ok"] is False
    assert "activation limit" in result["message"].lower()
    assert not (home / "license.json").exists()


def test_mcp_activate_tool_enforces_the_cap(http):
    http.queue("true", _sale(CAP + 1))
    act = gumroad_mcp.activate_license_key(FAKE_KEY, "mcp-host")
    assert act["activated"] is False
    assert act.get("seat_limit") is True


def test_revalidation_never_revokes_because_of_the_count(http, home, monkeypatch):
    http.queue("false", _sale(1))
    http.queue("true", _sale(1))
    assert licensing.activate(FAKE_KEY, instance_name="first-machine")["ok"] is True
    later = datetime.now(timezone.utc) + timedelta(days=2)
    monkeypatch.setattr(licensing, "_now", lambda: later)
    http.queue("false", _sale(CAP + 50))
    result = licensing.revalidate(force=True)
    assert result["tier"] == "pro", result
    assert licensing.is_pro() is True


def test_deactivate_is_local_and_says_the_seat_is_not_released(http, home):
    http.queue("false", _sale(1))
    http.queue("true", _sale(1))
    assert licensing.activate(FAKE_KEY, instance_name="old-machine")["ok"] is True
    result = licensing.deactivate()  # no canned reply queued: any HTTP call fails the test
    assert result["ok"] is True
    assert not (home / "license.json").exists()
    assert "support@" in result["message"]
