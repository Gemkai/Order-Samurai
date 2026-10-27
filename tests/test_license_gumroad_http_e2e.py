"""Exercise both license parsers with canned HTTP responses; never use the network."""
from __future__ import annotations

import io
import json
import os
import socket
import stat
import subprocess
import sys
import urllib.error
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

SAMURAI_ROOT = Path(__file__).resolve().parents[1]
FAKE_KEY = "FAKE-TEST-KEY-0000-1111-2222"
FAKE_EMAIL = "buyer@example.com"
RECHECK_DELAY = timedelta(hours=licensing.RECHECK_INTERVAL_HOURS, seconds=1)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*_args, **_kwargs):
        # pytest.fail escapes the production code's broad `except Exception` blocks.
        pytest.fail("Real network access attempted", pytrace=False)

    monkeypatch.setattr(gumroad_mcp.urllib.request.OpenerDirector, "open", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    for method in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, method, blocked)


@pytest.fixture
def home(tmp_path, monkeypatch):
    samurai_home = tmp_path / "samurai"
    user_home = tmp_path / "home"
    samurai_home.mkdir()
    user_home.mkdir()
    monkeypatch.setenv("SAMURAI_HOME", str(samurai_home))
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setenv("SAMURAI_NO_UPDATE_CHECK", "1")
    return samurai_home


@pytest.fixture
def now(monkeypatch):
    current = datetime.now(timezone.utc)
    monkeypatch.setattr(licensing, "_now", lambda: current)
    return current


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
        self.calls.append((form, timeout))
        if (request.full_url != "https://api.gumroad.com/v2/licenses/verify"
                or request.get_method() != "POST"
                or form != {
                    "product_id": [gumroad_mcp.GUMROAD_PRODUCT_ID],
                    "license_key": [FAKE_KEY],
                    "increment_uses_count": [increment],
                }):
            pytest.fail("Unexpected Gumroad HTTP request")
        if isinstance(body, Exception):
            raise body
        return io.BytesIO(json.dumps(body).encode("utf-8"))


@pytest.fixture
def http(monkeypatch, no_network):
    fake = _HTTP()
    monkeypatch.setattr(gumroad_mcp.urllib.request, "urlopen", fake)
    yield fake
    assert not fake.replies, "Expected Gumroad HTTP requests were not made"


def _purchase(sold_at, **flags):
    return {"success": True, "uses": 1, "purchase": {
        "email": FAKE_EMAIL,
        "sale_timestamp": sold_at.isoformat(),
        "refunded": False,
        "chargebacked": False,
        "disputed": False,
        "dispute_won": False,
        **flags,
    }}


def _read(home):
    return json.loads((home / "license.json").read_text(encoding="utf-8"))


def _activate(http, home, sold_at):
    # Validation does not consume a seat; activation consumes exactly one.
    http.queue("false", _purchase(sold_at))
    http.queue("true", _purchase(sold_at))
    result = licensing.activate(FAKE_KEY, instance_name="http-e2e-host")
    assert result["ok"] is True, result
    assert len(http.calls) == 2
    ent = _read(home)
    assert ent["provider"] == "gumroad"
    assert ent["purchased_at"] == sold_at.isoformat()
    assert ent["customer_email"] == FAKE_EMAIL
    assert ent["simulated"] is False
    return ent


def _advance(monkeypatch, ent):
    # Activation stamps datetime.now directly; advance from the actual saved time.
    later = datetime.fromisoformat(ent["last_checked_at"]) + RECHECK_DELAY
    monkeypatch.setattr(licensing, "_now", lambda: later)
    return later


def test_valid_key_activates_masks_and_revalidates(home, now, http, monkeypatch):
    ent = _activate(http, home, now)
    assert stat.S_IMODE((home / "license.json").stat().st_mode) == 0o600
    assert ent["license_key"] == FAKE_KEY
    assert ent["status"] == "active" and ent["valid"] is True
    assert licensing.is_pro() is True
    summary = licensing.status()
    assert summary["tier"] == "pro"
    assert summary["license_key"] == "****2222"
    assert summary["customer_email"] == "b***@example.com"
    assert FAKE_KEY not in json.dumps(summary)
    assert FAKE_EMAIL not in json.dumps(summary)
    assert len(http.calls) == 2

    later = _advance(monkeypatch, ent)
    http.queue("false", _purchase(now))
    assert licensing.revalidate(now=later) == {"tier": "pro", "result": "active"}
    assert len(http.calls) == 3
    assert http.calls[-1][1] == licensing.REVALIDATE_TIMEOUT_S
    assert licensing.is_pro() is True
    assert _read(home)["last_verified_at"] == later.isoformat()


def test_invalid_key_http_404_stays_free(home, now, http):
    body = {"success": False,
            "message": "That license does not exist for the provided product."}
    http.queue("false", urllib.error.HTTPError(
        gumroad_mcp.GUMROAD_API_URL, 404, "Not Found", {},
        io.BytesIO(json.dumps(body).encode("utf-8")),
    ))
    result = licensing.activate(FAKE_KEY, instance_name="http-e2e-host")
    assert result["ok"] is False
    assert "license key not recognized by Gumroad" in result["message"]
    assert not (home / "license.json").exists()
    assert licensing.is_pro() is False
    assert len(http.calls) == 1


@pytest.mark.parametrize("flag", ["refunded", "chargebacked", "disputed"])
def test_revoked_purchase_inside_window_loses_pro(home, now, http, monkeypatch, flag):
    ent = _activate(http, home, now)
    later = _advance(monkeypatch, ent)
    assert later - now < timedelta(days=21)
    http.queue("false", _purchase(now, **{flag: True}))
    assert licensing.is_pro() is False
    revoked = _read(home)
    assert revoked["status"] == "refunded"
    assert revoked["valid"] is False
    assert revoked["refunded"] is True
    assert revoked["last_checked_at"] == later.isoformat()
    assert len(http.calls) == 3
    assert licensing.is_pro() is False


def test_dispute_won_inside_window_keeps_pro(home, now, http, monkeypatch):
    ent = _activate(http, home, now)
    later = _advance(monkeypatch, ent)
    assert later - now < timedelta(days=21)
    http.queue("false", _purchase(now, disputed=True, dispute_won=True))
    assert licensing.is_pro() is True
    active = _read(home)
    assert active["status"] == "active" and active["valid"] is True
    assert active["refunded"] is False
    assert active["settled"] is False
    assert active["last_verified_at"] == later.isoformat()
    assert len(http.calls) == 3


def test_offline_after_window_then_settled_until_forced(home, now, http, monkeypatch):
    sold_at = now - timedelta(days=30)
    ent = _activate(http, home, sold_at)
    later = _advance(monkeypatch, ent)
    http.queue("false", urllib.error.URLError("offline"))
    assert licensing.revalidate(now=later) == {"tier": "pro", "result": "unreachable"}
    assert licensing.is_pro() is True
    offline = _read(home)
    assert offline["settled"] is False
    assert offline["last_checked_at"] == later.isoformat()
    assert offline["last_verified_at"] == ent["last_verified_at"]
    assert len(http.calls) == 3

    later = _advance(monkeypatch, offline)
    http.queue("false", _purchase(sold_at))
    assert licensing.revalidate(now=later) == {"tier": "pro", "result": "settled"}
    settled = _read(home)
    assert settled["settled"] is True
    assert settled["last_verified_at"] == later.isoformat()
    assert len(http.calls) == 4

    later += timedelta(days=365)
    monkeypatch.setattr(licensing, "_now", lambda: later)
    http.queue("false", _purchase(sold_at, refunded=True))
    assert licensing.is_pro() is True
    assert licensing.revalidate(now=later) == {"tier": "pro", "result": "skipped"}
    assert len(http.calls) == 4  # The queued refund must remain unread until forced.
    assert licensing.revalidate(force=True, now=later) == {"tier": "free", "result": "revoked"}
    assert len(http.calls) == 5
    assert _read(home)["status"] == "refunded"
    assert _read(home)["valid"] is False
    assert licensing.is_pro() is False


def test_offline_inside_window_keeps_pro_and_records_attempt(home, now, http, monkeypatch):
    ent = _activate(http, home, now)
    later = _advance(monkeypatch, ent)
    assert later - now < timedelta(days=21)
    http.queue("false", urllib.error.URLError("offline"))
    assert licensing.is_pro() is True
    offline = _read(home)
    assert offline["last_checked_at"] == later.isoformat()
    assert offline["last_verified_at"] == ent["last_verified_at"]
    assert offline["status"] == "active" and offline["valid"] is True
    assert offline["settled"] is False
    assert licensing.is_pro() is True
    assert licensing.revalidate(now=later) == {"tier": "pro", "result": "skipped"}
    assert len(http.calls) == 3


def test_free_tier_gate_and_cli_help(home, http):
    assert not (home / "license.json").exists()
    env = dict(os.environ)
    env.pop("BASH_ENV", None)
    gate = subprocess.run(
        ["bash", "-c", 'source "$1"; require_pro X', "license-http-e2e",
         str(SAMURAI_ROOT / "bin" / "lib_pro_gate.sh")],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert gate.returncode == 2, gate.stdout + gate.stderr
    assert "X is an Order Samurai Pro feature" in gate.stderr
    assert "Free tier includes" in gate.stderr
    assert "samurai activate <license-key>" in gate.stderr

    # Monkeypatches do not cross processes. Run the real CLI with a socket audit
    # guard; -I also excludes user startup hooks and inherited PYTHONPATH.
    guarded_cli = """
import runpy
import sys
def block_network(event, args):
    if event.startswith('socket.'):
        raise SystemExit('Real network access attempted in samurai CLI')
sys.addaudithook(block_network)
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
"""
    cli = subprocess.run(
        [sys.executable, "-I", "-c", guarded_cli,
         str(SAMURAI_ROOT / "bin" / "samurai"), "--help"],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert cli.returncode == 0, cli.stdout + cli.stderr
    assert "Order Samurai Governance CLI" in cli.stdout
    assert "license" in cli.stdout
    assert not (home / "license.json").exists()
    assert not http.calls
