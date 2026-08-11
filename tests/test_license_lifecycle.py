"""Paid lifecycle tests for the Order Samurai Pro entitlement (agentica_core/licensing.py).

Mocks the provider boundary (execution/gumroad_mcp.py, execution/lemonsqueezy_mcp.py) --
no test in this file makes a real network call. Covers activation, the fail-closed
matrix for every non-valid key shape, offline re-verification, the Free-tier gate in
bin/lib_pro_gate.sh, uninstall's license-preservation behavior, and the key-masking
contract so a raw license key never reaches stdout/stderr/logs.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
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

LIB_PRO_GATE = ROOT / "bin" / "lib_pro_gate.sh"
SAMURAI_BIN = ROOT / "bin" / "samurai"

FAKE_KEY = "SAMURAI-PRO-KEY-2026-7781-9921-X"


# --------------------------------------------------------------------------- #
# Provider mocking helpers
# --------------------------------------------------------------------------- #

def _patch_providers(
    monkeypatch,
    *,
    gumroad_validate=None,
    gumroad_activate=None,
    lemonsqueezy_validate=None,
    lemonsqueezy_activate=None,
):
    """Patch the SAME module objects licensing.activate() lazily imports from.

    licensing.activate() does `from execution.gumroad_mcp import validate_license_key
    as g_val` *inside the function body* on every call, so patching the attribute on
    the already-imported module object is picked up on the next call.
    """
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
    """Wraps a return value so tests can assert a provider fn's call count."""

    def __init__(self, result):
        self.result = result
        self.calls = 0

    def __call__(self, *_args, **_kwargs):
        self.calls += 1
        return self.result


# --------------------------------------------------------------------------- #
# 1. Valid key activates
# --------------------------------------------------------------------------- #

def test_valid_pro_key_activates_and_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMURAI_HOME", str(tmp_path))

    _patch_providers(
        monkeypatch,
        gumroad_validate=lambda key, product_id=None: {
            "valid": True,
            "license_key": key,
            "customer_email": "buyer@example.com",
            "status": "active",
            "refunded": False,
        },
        gumroad_activate=lambda key, instance_name: {
            "activated": True,
            "instance_id": "gum_abc123",
            "instance_name": instance_name,
            "license_key": key,
            "customer_email": "buyer@example.com",
        },
    )

    result = licensing.activate(FAKE_KEY, instance_name="macbook-dev")
    assert result["ok"] is True

    lic_path = tmp_path / "license.json"
    assert lic_path.exists()
    mode = lic_path.stat().st_mode & 0o777
    assert mode == 0o600, f"license.json must be 0600, got {oct(mode)}"

    assert licensing.is_pro() is True


# --------------------------------------------------------------------------- #
# 2. Fail-closed matrix -- invalid / malformed / expired / revoked / refunded /
#    wrong-product keys must never yield Pro, and must never leave a file behind.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "case,gumroad_val,lemonsqueezy_val",
    [
        (
            "invalid",
            {"valid": False, "error": "license key not recognized by Gumroad"},
            {"valid": False, "error": "license key not recognized by Lemon Squeezy"},
        ),
        (
            "expired",
            {"valid": False, "status": "expired", "error": "license key expired"},
            {"valid": False, "status": "expired", "error": "license key expired"},
        ),
        (
            "revoked",
            {"valid": False, "status": "revoked", "error": "license key revoked"},
            {"valid": False, "status": "revoked", "error": "license key revoked"},
        ),
        (
            "wrong_product",
            {"valid": False, "error": "license key belongs to a different product"},
            {"valid": False, "error": "license key belongs to a different product"},
        ),
    ],
)
def test_non_valid_keys_fail_closed(tmp_path, monkeypatch, case, gumroad_val, lemonsqueezy_val):
    monkeypatch.setenv("SAMURAI_HOME", str(tmp_path))
    _patch_providers(
        monkeypatch,
        gumroad_validate=lambda key, product_id=None, _v=gumroad_val: _v,
        gumroad_activate=_never_called,
        lemonsqueezy_validate=lambda key, instance_id=None, _v=lemonsqueezy_val: _v,
        lemonsqueezy_activate=_never_called,
    )

    result = licensing.activate(FAKE_KEY, instance_name="macbook-dev")
    assert result["ok"] is False, f"{case} key must not activate"
    assert licensing.is_pro() is False, f"{case} key must not yield Pro"
    assert not (tmp_path / "license.json").exists(), f"{case} key must not leave a license file"


def test_refunded_key_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMURAI_HOME", str(tmp_path))
    # Gumroad reports the key as VALID but refunded -- must still fail closed,
    # and must never even try Lemon Squeezy (gumroad already answered "valid").
    _patch_providers(
        monkeypatch,
        gumroad_validate=lambda key, product_id=None: {
            "valid": True,
            "status": "refunded",
            "refunded": True,
            "customer_email": "buyer@example.com",
        },
        gumroad_activate=_never_called,
        lemonsqueezy_validate=_never_called,
        lemonsqueezy_activate=_never_called,
    )

    result = licensing.activate(FAKE_KEY, instance_name="macbook-dev")
    assert result["ok"] is False
    assert "refund" in result["message"].lower()
    assert licensing.is_pro() is False
    assert not (tmp_path / "license.json").exists()


def test_malformed_key_fails_closed_without_calling_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMURAI_HOME", str(tmp_path))
    _patch_providers(
        monkeypatch,
        gumroad_validate=_never_called,
        gumroad_activate=_never_called,
        lemonsqueezy_validate=_never_called,
        lemonsqueezy_activate=_never_called,
    )

    for malformed in ("", "   ", None):
        result = licensing.activate(malformed, instance_name="macbook-dev")
        assert result["ok"] is False
        assert "empty" in result["message"].lower()

    assert licensing.is_pro() is False
    assert not (tmp_path / "license.json").exists()


# --------------------------------------------------------------------------- #
# 3. Network error during activation fails closed with a retry message.
#    Regression: a SAMURAI-PRO-KEY prefix used to be treated as valid on a
#    network error. This must never happen for ANY key shape, prefix included.
# --------------------------------------------------------------------------- #

def test_network_error_during_activation_fails_closed_with_retry_message(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMURAI_HOME", str(tmp_path))

    network_error = {
        "valid": False,
        "error": "Could not reach Lemon Squeezy to validate this license (timed out). "
                 "Check your network connection and try again.",
    }
    _patch_providers(
        monkeypatch,
        gumroad_validate=lambda key, product_id=None: network_error,
        gumroad_activate=_never_called,
        lemonsqueezy_validate=lambda key, instance_id=None: network_error,
        lemonsqueezy_activate=_never_called,
    )

    result = licensing.activate(FAKE_KEY, instance_name="macbook-dev")
    assert result["ok"] is False
    assert "try again" in result["message"].lower()
    assert licensing.is_pro() is False
    assert not (tmp_path / "license.json").exists()


# --------------------------------------------------------------------------- #
# 4. After activation, verification works OFFLINE -- no provider call on is_pro().
# --------------------------------------------------------------------------- #

def test_offline_verification_never_calls_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMURAI_HOME", str(tmp_path))

    validate_counter = _CallCounter({
        "valid": True, "license_key": FAKE_KEY, "status": "active",
        "refunded": False, "customer_email": "buyer@example.com",
    })
    _patch_providers(
        monkeypatch,
        gumroad_validate=validate_counter,
        gumroad_activate=lambda key, instance_name: {
            "activated": True, "instance_id": "gum_abc123",
            "instance_name": instance_name, "license_key": key,
        },
    )

    result = licensing.activate(FAKE_KEY, instance_name="macbook-dev")
    assert result["ok"] is True
    calls_after_activation = validate_counter.calls
    assert calls_after_activation >= 1

    # Now make ANY further provider call an assertion failure, and confirm
    # repeated is_pro()/status() calls never touch the network at all.
    _patch_providers(monkeypatch, gumroad_validate=_never_called, lemonsqueezy_validate=_never_called)

    for _ in range(3):
        assert licensing.is_pro() is True
    assert licensing.status()["activated"] is True


# --------------------------------------------------------------------------- #
# 5. Free Core still functions without any license.
# --------------------------------------------------------------------------- #

def test_lib_pro_gate_is_pro_exits_1_on_free_tier(tmp_path):
    env = dict(os.environ)
    env["SAMURAI_HOME"] = str(tmp_path)  # no license.json written here
    res = subprocess.run(
        ["bash", "-c", f'source "{LIB_PRO_GATE}"; is_pro'],
        capture_output=True, text=True, env=env,
    )
    assert res.returncode == 1


def test_lib_pro_gate_require_pro_exits_2_with_upgrade_notice(tmp_path):
    env = dict(os.environ)
    env["SAMURAI_HOME"] = str(tmp_path)
    res = subprocess.run(
        ["bash", "-c", f'source "{LIB_PRO_GATE}"; require_pro "Nightly Dojo"'],
        capture_output=True, text=True, env=env,
    )
    assert res.returncode == 2
    assert "Pro feature" in res.stderr
    assert "samurai activate" in res.stderr


# --------------------------------------------------------------------------- #
# 6. License verification transmits no source code / prompts / telemetry --
#    only the key (+ instance identifiers) leave the machine.
# --------------------------------------------------------------------------- #

ALLOWED_PAYLOAD_KEYS = {"license_key", "product_id", "instance_id", "instance_name"}


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_activation_payload_contains_only_key_and_instance_identifiers(monkeypatch):
    import urllib.parse
    import urllib.request

    captured = {}

    def fake_urlopen(req, *_args, **_kwargs):
        captured["data"] = urllib.parse.parse_qs(req.data.decode("utf-8"))
        return _FakeResponse(json.dumps({"success": True, "purchase": {"email": "buyer@example.com"}}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    gumroad_mcp.validate_license_key(FAKE_KEY)
    sent_keys = set(captured["data"].keys())
    assert sent_keys <= ALLOWED_PAYLOAD_KEYS, f"unexpected fields sent to provider: {sent_keys - ALLOWED_PAYLOAD_KEYS}"

    captured.clear()

    def fake_urlopen_ls(req, *_args, **_kwargs):
        captured["data"] = urllib.parse.parse_qs(req.data.decode("utf-8"))
        return _FakeResponse(json.dumps({"activated": True, "instance": {"id": "ls_1"}}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen_ls)
    lemonsqueezy_mcp.activate_license_key(FAKE_KEY, "macbook-dev")
    sent_keys = set(captured["data"].keys())
    assert sent_keys <= ALLOWED_PAYLOAD_KEYS, f"unexpected fields sent to provider: {sent_keys - ALLOWED_PAYLOAD_KEYS}"


# --------------------------------------------------------------------------- #
# 7. samurai uninstall preserves the license to
#    ~/order-samurai-license-backup.json (v1.0.1 behavior).
# --------------------------------------------------------------------------- #

def test_uninstall_preserves_license_backup(tmp_path):
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    samurai_home = home_dir / ".samurai"
    samurai_home.mkdir()
    entitlement = {
        "tier": "pro", "valid": True, "status": "active",
        "license_key": FAKE_KEY, "instance_name": "macbook-dev",
    }
    (samurai_home / "license.json").write_text(json.dumps(entitlement))

    env = dict(os.environ)
    env["HOME"] = str(home_dir)
    env["SAMURAI_ROOT"] = str(ROOT)

    res = subprocess.run(
        [sys.executable, str(SAMURAI_BIN), "uninstall"],
        capture_output=True, text=True, env=env,
    )
    assert res.returncode == 0

    backup = home_dir / "order-samurai-license-backup.json"
    assert backup.exists()
    backed_up = json.loads(backup.read_text())
    assert backed_up["license_key"] == FAKE_KEY
    assert not samurai_home.exists()

    # Mask check lives here too: uninstall's own output must never echo the raw key.
    assert FAKE_KEY not in res.stdout
    assert FAKE_KEY not in res.stderr


# --------------------------------------------------------------------------- #
# 8. A license key value never appears in stdout/stderr of any command.
# --------------------------------------------------------------------------- #

def test_mask_key_never_reveals_full_license_key():
    masked = licensing._mask_key(FAKE_KEY)
    assert FAKE_KEY not in masked
    assert masked.endswith(FAKE_KEY[-4:])
    assert masked.startswith("****")


def test_status_masks_license_key_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMURAI_HOME", str(tmp_path))
    entitlement = {
        "tier": "pro", "valid": True, "status": "active",
        "license_key": FAKE_KEY, "instance_name": "macbook-dev",
    }
    (tmp_path / "license.json").write_text(json.dumps(entitlement))

    st = licensing.status()
    assert FAKE_KEY not in json.dumps(st)
    assert st["license_key"].endswith(FAKE_KEY[-4:])
