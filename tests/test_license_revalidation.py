"""Acceptance tests for refund-window license revalidation (agentica_core/licensing.py).

Design under test: re-verify with the provider only while inside a refund window
(REFUND_WINDOW_DAYS after purchase), at most once per RECHECK_INTERVAL_HOURS, then
"settle" and stay offline forever. Offline/unreachable never revokes; only an explicit
provider refund does. Mocks the provider boundary; no test makes a real network call.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import types
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

SAMURAI_ROOT = Path(__file__).resolve().parents[1]
LIB_PRO_GATE = SAMURAI_ROOT / "bin" / "lib_pro_gate.sh"
SAMURAI_BIN = SAMURAI_ROOT / "bin" / "samurai"

FAKE_KEY = "FAKE-TEST-KEY-0000-1111-2222"
FAKE_EMAIL = "buyer@example.com"

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# --------------------------------------------------------------------------- #
# Helpers (same conventions as test_license_lifecycle.py)
# --------------------------------------------------------------------------- #

def _patch_providers(monkeypatch, *, gumroad_validate=None, gumroad_activate=None,
                     lemonsqueezy_validate=None, lemonsqueezy_activate=None):
    if gumroad_validate is not None:
        monkeypatch.setattr(gumroad_mcp, "validate_license_key", gumroad_validate)
    if gumroad_activate is not None:
        monkeypatch.setattr(gumroad_mcp, "activate_license_key", gumroad_activate)
    if lemonsqueezy_validate is not None:
        monkeypatch.setattr(lemonsqueezy_mcp, "validate_license_key", lemonsqueezy_validate)
    if lemonsqueezy_activate is not None:
        monkeypatch.setattr(lemonsqueezy_mcp, "activate_license_key", lemonsqueezy_activate)


def _never_called(*_args, **_kwargs):
    raise AssertionError("provider function was called when it should not have been")


class _CallCounter:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.calls = 0
        self.args = []
        self.kwargs = []

    def __call__(self, *args, **kwargs):
        self.calls += 1
        self.args.append(args)
        self.kwargs.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return self.result


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


GOOD = {"valid": True, "status": "active", "refunded": False, "customer_email": FAKE_EMAIL}
UNREACHABLE = {"valid": False,
               "error": "Could not reach Gumroad (timed out). Check your network connection and try again."}


def _write_license(home: Path, **overrides) -> dict:
    """A Pro entitlement purchased/activated 1 day before NOW, last checked 2 days ago
    (so a check is due) unless overridden. A None override removes the field."""
    ent = {
        "tier": "pro", "provider": "gumroad", "valid": True, "status": "active",
        "refunded": False, "license_key": FAKE_KEY, "instance_id": "gum_abc123",
        "instance_name": "macbook-dev", "customer_email": FAKE_EMAIL,
        "activated_at": _iso(NOW - timedelta(days=1)),
        "purchased_at": _iso(NOW - timedelta(days=1)),
        "last_checked_at": _iso(NOW - timedelta(days=2)),
        "last_verified_at": _iso(NOW - timedelta(days=2)),
        "settled": False, "simulated": False,
    }
    ent.update(overrides)
    ent = {k: v for k, v in ent.items() if v is not None}
    (home / "license.json").write_text(json.dumps(ent))
    return ent


def _read(home: Path) -> dict:
    return json.loads((home / "license.json").read_text())


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMURAI_HOME", str(tmp_path))
    return tmp_path


def _no_provider(monkeypatch):
    _patch_providers(monkeypatch, gumroad_validate=_never_called, gumroad_activate=_never_called,
                     lemonsqueezy_validate=_never_called, lemonsqueezy_activate=_never_called)


def _assert_no_pii(text: str):
    assert FAKE_KEY not in text
    assert FAKE_EMAIL not in text


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

def test_constants():
    assert licensing.REFUND_WINDOW_DAYS == 21
    assert licensing.RECHECK_INTERVAL_HOURS == 24
    assert isinstance(licensing.REVALIDATE_TIMEOUT_S, int)
    assert 0 < licensing.REVALIDATE_TIMEOUT_S <= 10


# --------------------------------------------------------------------------- #
# 1. gumroad_mcp.validate_license_key surfaces purchased_at
# --------------------------------------------------------------------------- #

def _gumroad_http_with(monkeypatch, purchase: dict):
    import urllib.request

    def fake_urlopen(req, *_a, **_k):
        return _FakeResponse(json.dumps({"success": True, "uses": 1, "purchase": {
            "email": FAKE_EMAIL, "refunded": False, **purchase}}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def test_gumroad_validate_returns_purchased_at_from_sale_timestamp(monkeypatch):
    _gumroad_http_with(monkeypatch, {"sale_timestamp": "2026-09-20T10:00:00Z",
                                     "created_at": "2026-09-01T00:00:00Z"})
    res = gumroad_mcp.validate_license_key(FAKE_KEY)
    assert res["purchased_at"] == "2026-09-20T10:00:00Z"


def test_gumroad_validate_falls_back_to_created_at(monkeypatch):
    _gumroad_http_with(monkeypatch, {"created_at": "2026-09-01T00:00:00Z"})
    res = gumroad_mcp.validate_license_key(FAKE_KEY)
    assert res["purchased_at"] == "2026-09-01T00:00:00Z"


# --------------------------------------------------------------------------- #
# 2. activate() stores the new fields
# --------------------------------------------------------------------------- #

def _activate(monkeypatch, validate_extra: dict):
    _patch_providers(
        monkeypatch,
        gumroad_validate=lambda key, product_id=None, **kw: {**GOOD, **validate_extra},
        gumroad_activate=lambda key, instance_name: {
            "activated": True, "instance_id": "gum_abc123", "instance_name": instance_name},
    )
    return licensing.activate(FAKE_KEY, instance_name="macbook-dev")


def test_activate_stores_purchased_at_from_validate_response(home, monkeypatch):
    assert _activate(monkeypatch, {"purchased_at": "2026-09-20T10:00:00Z"})["ok"] is True
    ent = _read(home)
    assert ent["purchased_at"] == "2026-09-20T10:00:00Z"
    assert ent["settled"] is False


def test_activate_defaults_purchased_at_to_activated_at(home, monkeypatch):
    assert _activate(monkeypatch, {})["ok"] is True
    ent = _read(home)
    assert ent["purchased_at"] == ent["activated_at"]


def test_activate_sets_check_timestamps_to_activation_time(home, monkeypatch):
    _activate(monkeypatch, {"purchased_at": "2026-09-20T10:00:00Z"})
    ent = _read(home)
    assert ent["last_checked_at"] == ent["activated_at"]
    assert ent["last_verified_at"] == ent["activated_at"]
    assert ent["settled"] is False


# --------------------------------------------------------------------------- #
# 3a-d. revalidate(): skip cases never call the provider
# --------------------------------------------------------------------------- #

def test_revalidate_skips_when_no_license(home, monkeypatch):
    _no_provider(monkeypatch)
    res = licensing.revalidate(now=NOW)
    assert res["result"] == "skipped" and res["tier"] == "free"


@pytest.mark.parametrize("overrides", [
    {"tier": "free"},
    {"status": "refunded", "refunded": True, "valid": False},
    {"valid": False, "status": "inactive"},
])
def test_revalidate_skips_non_pro_or_revoked(home, monkeypatch, overrides):
    _write_license(home, **overrides)
    _no_provider(monkeypatch)
    res = licensing.revalidate(now=NOW)
    assert res["result"] == "skipped" and res["tier"] == "free"


def test_revalidate_skips_simulated_entitlement(home, monkeypatch):
    _write_license(home, simulated=True)
    _no_provider(monkeypatch)
    res = licensing.revalidate(now=NOW)
    assert res["result"] == "skipped" and res["tier"] == "pro"


def test_revalidate_skips_settled(home, monkeypatch):
    _write_license(home, settled=True)
    _no_provider(monkeypatch)
    res = licensing.revalidate(now=NOW)
    assert res["result"] == "skipped" and res["tier"] == "pro"


def test_revalidate_skips_when_checked_within_24h(home, monkeypatch):
    _write_license(home, last_checked_at=_iso(NOW - timedelta(hours=23)))
    _no_provider(monkeypatch)
    res = licensing.revalidate(now=NOW)
    assert res["result"] == "skipped" and res["tier"] == "pro"


def test_revalidate_checks_once_24h_elapsed(home, monkeypatch):
    _write_license(home, last_checked_at=_iso(NOW - timedelta(hours=24, seconds=1)))
    counter = _CallCounter(GOOD)
    _patch_providers(monkeypatch, gumroad_validate=counter, gumroad_activate=_never_called)
    res = licensing.revalidate(now=NOW)
    assert counter.calls == 1
    assert res["result"] == "active"


@pytest.mark.parametrize("overrides", [
    {"settled": True},
    {"last_checked_at": _iso(NOW - timedelta(hours=1))},
])
def test_force_bypasses_settled_and_interval(home, monkeypatch, overrides):
    _write_license(home, **overrides)
    counter = _CallCounter(GOOD)
    _patch_providers(monkeypatch, gumroad_validate=counter, gumroad_activate=_never_called)
    res = licensing.revalidate(force=True, now=NOW)
    assert counter.calls == 1
    assert res["result"] in ("active", "settled")


# --------------------------------------------------------------------------- #
# 3e. Provider routing and call shape
# --------------------------------------------------------------------------- #

def test_gumroad_called_without_incrementing_seats_and_with_short_timeout(home, monkeypatch):
    _write_license(home)
    counter = _CallCounter(GOOD)
    _patch_providers(monkeypatch, gumroad_validate=counter, gumroad_activate=_never_called,
                     lemonsqueezy_validate=_never_called, lemonsqueezy_activate=_never_called)
    licensing.revalidate(now=NOW)
    assert counter.calls == 1
    assert counter.args[0][0] == FAKE_KEY
    kw = counter.kwargs[0]
    assert "increment_uses_count" in kw and kw["increment_uses_count"] is False
    assert kw["timeout"] == licensing.REVALIDATE_TIMEOUT_S


def test_provider_defaults_to_gumroad_when_field_missing(home, monkeypatch):
    _write_license(home, provider=None)
    counter = _CallCounter(GOOD)
    _patch_providers(monkeypatch, gumroad_validate=counter, gumroad_activate=_never_called,
                     lemonsqueezy_validate=_never_called, lemonsqueezy_activate=_never_called)
    licensing.revalidate(now=NOW)
    assert counter.calls == 1


def test_lemonsqueezy_license_uses_lemonsqueezy_only(home, monkeypatch):
    _write_license(home, provider="lemonsqueezy")
    counter = _CallCounter(GOOD)
    _patch_providers(monkeypatch, gumroad_validate=_never_called, gumroad_activate=_never_called,
                     lemonsqueezy_validate=counter, lemonsqueezy_activate=_never_called)
    res = licensing.revalidate(now=NOW)
    assert counter.calls == 1
    assert res["result"] == "active"


# --------------------------------------------------------------------------- #
# 3f. Refund revokes
# --------------------------------------------------------------------------- #

REFUND_ANSWERS = [
    {"valid": False, "refunded": True, "status": "refunded"},
    {"valid": True, "refunded": True, "status": "refunded"},
    {"valid": False, "refunded": False, "status": "refunded"},
    {"valid": False, "refunded": True},
]


@pytest.mark.parametrize("answer", REFUND_ANSWERS)
def test_refund_revokes_entitlement(home, monkeypatch, answer):
    _write_license(home)
    _patch_providers(monkeypatch, gumroad_validate=_CallCounter(answer), gumroad_activate=_never_called)
    res = licensing.revalidate(now=NOW)
    assert res["result"] == "revoked" and res["tier"] == "free"
    ent = _read(home)
    assert ent["status"] == "refunded"
    assert ent["refunded"] is True
    assert ent["valid"] is False


def test_after_revocation_is_pro_false_and_offline(home, monkeypatch):
    _write_license(home)
    _patch_providers(monkeypatch, gumroad_validate=_CallCounter(REFUND_ANSWERS[0]),
                     gumroad_activate=_never_called)
    licensing.revalidate(now=NOW)
    _no_provider(monkeypatch)
    for _ in range(3):
        assert licensing.is_pro() is False


# --------------------------------------------------------------------------- #
# 3g. Valid answer: active / settled, boundary at 21 days
# --------------------------------------------------------------------------- #

def test_valid_answer_inside_window_stays_active_and_unsettled(home, monkeypatch):
    _write_license(home, purchased_at=_iso(NOW - timedelta(days=20)))
    _patch_providers(monkeypatch, gumroad_validate=_CallCounter(GOOD), gumroad_activate=_never_called)
    res = licensing.revalidate(now=NOW)
    assert res["result"] == "active" and res["tier"] == "pro"
    ent = _read(home)
    assert ent["settled"] is False
    assert ent["last_checked_at"] == _iso(NOW)
    assert ent["last_verified_at"] == _iso(NOW)


def test_valid_answer_past_window_settles(home, monkeypatch):
    _write_license(home, purchased_at=_iso(NOW - timedelta(days=22)))
    _patch_providers(monkeypatch, gumroad_validate=_CallCounter(GOOD), gumroad_activate=_never_called)
    res = licensing.revalidate(now=NOW)
    assert res["result"] == "settled" and res["tier"] == "pro"
    ent = _read(home)
    assert ent["settled"] is True
    assert ent["last_verified_at"] == _iso(NOW)


def test_settles_exactly_at_21_days(home, monkeypatch):
    _write_license(home, purchased_at=_iso(NOW - timedelta(days=21)))
    _patch_providers(monkeypatch, gumroad_validate=_CallCounter(GOOD), gumroad_activate=_never_called)
    assert licensing.revalidate(now=NOW)["result"] == "settled"


def test_settled_entitlement_never_calls_provider_even_years_later(home, monkeypatch):
    _write_license(home, purchased_at=_iso(NOW - timedelta(days=22)))
    _patch_providers(monkeypatch, gumroad_validate=_CallCounter(GOOD), gumroad_activate=_never_called)
    licensing.revalidate(now=NOW)
    _no_provider(monkeypatch)
    later = NOW + timedelta(days=365 * 3)
    res = licensing.revalidate(now=later)
    assert res["result"] == "skipped" and res["tier"] == "pro"
    assert licensing.is_pro() is True
    assert licensing.is_pro() is True


# --------------------------------------------------------------------------- #
# 3h. Unreachable never revokes
# --------------------------------------------------------------------------- #

def _unreachable_cases():
    return [("error_dict", _CallCounter(UNREACHABLE)),
            ("raises", _CallCounter(exc=RuntimeError("boom")))]


@pytest.mark.parametrize("kind", ["error_dict", "raises"])
@pytest.mark.parametrize("purchase_age_days", [5, 400])
def test_unreachable_keeps_pro_and_records_attempt_only(home, monkeypatch, kind, purchase_age_days):
    prior_verified = _iso(NOW - timedelta(days=3))
    _write_license(home, purchased_at=_iso(NOW - timedelta(days=purchase_age_days)),
                   last_verified_at=prior_verified)
    counter = dict(_unreachable_cases())[kind]
    _patch_providers(monkeypatch, gumroad_validate=counter, gumroad_activate=_never_called)
    res = licensing.revalidate(now=NOW)
    assert res["result"] == "unreachable" and res["tier"] == "pro"
    ent = _read(home)
    assert ent["last_checked_at"] == _iso(NOW)
    assert ent["last_verified_at"] == prior_verified
    assert ent["settled"] is False
    assert ent["status"] == "active" and ent["valid"] is True


def test_unreachable_retries_after_24h_not_before(home, monkeypatch):
    _write_license(home)
    counter = _CallCounter(UNREACHABLE)
    _patch_providers(monkeypatch, gumroad_validate=counter, gumroad_activate=_never_called)
    licensing.revalidate(now=NOW)
    licensing.revalidate(now=NOW + timedelta(hours=23))
    assert counter.calls == 1
    licensing.revalidate(now=NOW + timedelta(hours=24, seconds=1))
    assert counter.calls == 2


# --------------------------------------------------------------------------- #
# 3i. Inconclusive
# --------------------------------------------------------------------------- #

def test_not_found_is_inconclusive_and_keeps_pro(home, monkeypatch):
    _write_license(home, purchased_at=_iso(NOW - timedelta(days=30)))
    _patch_providers(monkeypatch, gumroad_activate=_never_called, gumroad_validate=_CallCounter(
        {"valid": False, "not_found": True, "error": "license key not recognized by Gumroad"}))
    res = licensing.revalidate(now=NOW)
    assert res["result"] == "inconclusive" and res["tier"] == "pro"
    ent = _read(home)
    assert ent["settled"] is False
    assert ent["last_checked_at"] == _iso(NOW)
    assert licensing.is_pro() is True


# --------------------------------------------------------------------------- #
# 3j. Legacy license.json
# --------------------------------------------------------------------------- #

def test_legacy_license_is_due_and_purchased_at_falls_back_to_activated_at(home, monkeypatch):
    _write_license(home, purchased_at=None, last_checked_at=None, last_verified_at=None,
                   settled=None, activated_at=_iso(NOW - timedelta(days=40)))
    counter = _CallCounter(GOOD)
    _patch_providers(monkeypatch, gumroad_validate=counter, gumroad_activate=_never_called)
    res = licensing.revalidate(now=NOW)
    assert counter.calls == 1
    assert res["result"] == "settled"  # activated_at is 40 days ago => outside the window
    assert _read(home)["settled"] is True


def test_legacy_license_inside_window_stays_active(home, monkeypatch):
    _write_license(home, purchased_at=None, last_checked_at=None, last_verified_at=None,
                   settled=None, activated_at=_iso(NOW - timedelta(days=3)))
    _patch_providers(monkeypatch, gumroad_validate=_CallCounter(GOOD), gumroad_activate=_never_called)
    assert licensing.revalidate(now=NOW)["result"] == "active"


# --------------------------------------------------------------------------- #
# 4. is_pro() integration
# --------------------------------------------------------------------------- #

def _due_license(home, **kw):
    """Due for a check relative to the REAL clock (is_pro takes no `now`)."""
    real = datetime.now(timezone.utc)
    return _write_license(home, activated_at=_iso(real - timedelta(days=1)),
                          purchased_at=_iso(real - timedelta(days=1)),
                          last_checked_at=_iso(real - timedelta(days=2)),
                          last_verified_at=_iso(real - timedelta(days=2)), **kw)


def test_is_pro_does_exactly_one_revalidation_when_due(home, monkeypatch):
    _due_license(home)
    counter = _CallCounter(GOOD)
    _patch_providers(monkeypatch, gumroad_validate=counter, gumroad_activate=_never_called)
    assert licensing.is_pro() is True
    assert counter.calls == 1
    assert licensing.is_pro() is True  # now checked within 24h
    assert counter.calls == 1


def test_is_pro_false_on_the_call_that_discovers_a_refund(home, monkeypatch):
    _due_license(home)
    counter = _CallCounter(REFUND_ANSWERS[0])
    _patch_providers(monkeypatch, gumroad_validate=counter, gumroad_activate=_never_called)
    assert licensing.is_pro() is False
    assert counter.calls == 1
    assert licensing.is_pro() is False
    assert counter.calls == 1


def test_is_pro_never_raises_when_provider_raises(home, monkeypatch):
    _due_license(home)
    counter = _CallCounter(exc=RuntimeError("boom"))
    _patch_providers(monkeypatch, gumroad_validate=counter, gumroad_activate=_never_called)
    assert licensing.is_pro() is True
    assert counter.calls == 1


def test_is_pro_without_license_makes_no_provider_call(home, monkeypatch):
    _no_provider(monkeypatch)
    assert licensing.is_pro() is False


# --------------------------------------------------------------------------- #
# 5. Privacy
# --------------------------------------------------------------------------- #

def _privacy_scenarios():
    return {
        "skipped": (dict(settled=True), None),
        "active": (dict(), GOOD),
        "settled": (dict(purchased_at=_iso(NOW - timedelta(days=30))), GOOD),
        "revoked": (dict(), REFUND_ANSWERS[0]),
        "unreachable": (dict(), UNREACHABLE),
        "inconclusive": (dict(), {"valid": False, "not_found": True, "error": "not recognized",
                                  "license_key": FAKE_KEY, "customer_email": FAKE_EMAIL}),
    }


@pytest.mark.parametrize("scenario", ["skipped", "active", "settled", "revoked",
                                      "unreachable", "inconclusive"])
def test_revalidate_leaks_neither_key_nor_email(home, monkeypatch, capsys, scenario):
    overrides, answer = _privacy_scenarios()[scenario]
    _write_license(home, **overrides)
    if answer is None:
        _no_provider(monkeypatch)
    else:
        _patch_providers(monkeypatch, gumroad_validate=_CallCounter(answer),
                         gumroad_activate=_never_called)
    res = licensing.revalidate(now=NOW)
    assert res["result"] == scenario
    _assert_no_pii(json.dumps(res, default=str))
    out = capsys.readouterr()
    _assert_no_pii(out.out)
    _assert_no_pii(out.err)


def test_provider_exception_text_with_key_is_not_leaked(home, monkeypatch, capsys):
    _write_license(home)
    _patch_providers(monkeypatch, gumroad_activate=_never_called,
                     gumroad_validate=_CallCounter(exc=RuntimeError(f"bad {FAKE_KEY} {FAKE_EMAIL}")))
    res = licensing.revalidate(now=NOW)
    assert res["result"] == "unreachable"
    _assert_no_pii(json.dumps(res, default=str))
    out = capsys.readouterr()
    _assert_no_pii(out.out + out.err)


def test_status_masks_email(home, monkeypatch, capsys):
    _write_license(home, settled=True)
    _no_provider(monkeypatch)
    st = licensing.status()
    blob = json.dumps(st, default=str)
    assert FAKE_EMAIL not in blob
    assert "example.com" in blob
    assert FAKE_KEY not in blob
    out = capsys.readouterr()
    _assert_no_pii(out.out + out.err)


# --------------------------------------------------------------------------- #
# 6. Shell gate agrees with Python
# --------------------------------------------------------------------------- #

def _gate_exit(home: Path) -> int:
    env = dict(os.environ, SAMURAI_HOME=str(home))
    return subprocess.run(["bash", "-c", f'source "{LIB_PRO_GATE}"; is_pro'],
                          capture_output=True, text=True, env=env, timeout=30).returncode


def test_shell_gate_rejects_refunded_status(home):
    _write_license(home, status="refunded", settled=True)  # refunded wins even if settled
    assert _gate_exit(home) == 1


def test_shell_gate_rejects_refunded_after_revocation(home, monkeypatch):
    _write_license(home)
    _patch_providers(monkeypatch, gumroad_validate=_CallCounter(REFUND_ANSWERS[0]),
                     gumroad_activate=_never_called)
    licensing.revalidate(now=NOW)
    assert _gate_exit(home) == 1


def test_shell_gate_accepts_settled_pro(home):
    _write_license(home, settled=True)
    assert _gate_exit(home) == 0


def test_shell_gate_accepts_simulated_pro(home):
    _write_license(home, simulated=True)
    assert _gate_exit(home) == 0


# --------------------------------------------------------------------------- #
# 7. CLI: `samurai license --refresh`
# --------------------------------------------------------------------------- #

def _load_samurai():
    loader = importlib.machinery.SourceFileLoader("samurai_cli_under_test", str(SAMURAI_BIN))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_cli_license_refresh_forces_revalidation_and_prints_tier(home, monkeypatch, capsys):
    _write_license(home, settled=True)
    calls = []

    def fake_revalidate(force=False, now=None):
        calls.append(force)
        return {"tier": "pro", "result": "active"}

    monkeypatch.setattr(licensing, "revalidate", fake_revalidate, raising=False)
    _no_provider(monkeypatch)
    cli = _load_samurai()
    rc = cli.cmd_license(types.SimpleNamespace(refresh=True))
    out = capsys.readouterr()
    assert rc == 0
    assert calls == [True]
    assert "PRO" in out.out.upper()
    _assert_no_pii(out.out + out.err)


def test_cli_license_without_refresh_makes_no_provider_call(home, monkeypatch, capsys):
    _write_license(home, settled=True)
    _no_provider(monkeypatch)
    monkeypatch.setattr(licensing, "revalidate", _never_called, raising=False)
    cli = _load_samurai()
    assert cli.cmd_license(types.SimpleNamespace(refresh=False)) == 0
    out = capsys.readouterr()
    assert "PRO" in out.out.upper()
    _assert_no_pii(out.out + out.err)
