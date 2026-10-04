#!/usr/bin/env python3
"""Order Samurai Pro entitlement — the single authority for "is this machine Pro?".

Offline-perpetual model (per TERMS.md / EULA.md): the license key is validated ONCE
online at activation time via Gumroad (execution/gumroad_mcp.py; Lemon Squeezy is a
legacy fallback in execution/lemonsqueezy_mcp.py), and the
resulting entitlement is written to ``~/.samurai/license.json``. After that, every
Pro-gated feature reads that file locally — no network, works offline forever, which is
exactly what "offline perpetual key activation" promises.

Fail-CLOSED to Free: absence of a valid entitlement means Free tier. A missing,
malformed, refunded, or inactive license.json never yields Pro. This module is
dependency-light (stdlib only) so the CLI, the reducers, and the TS engine (which reads
the same JSON) all agree on one contract.

Contract of ``~/.samurai/license.json`` (also read by api/src/licensing.ts):
    {
      "tier": "pro",
      "valid": true,
      "status": "active",            # "refunded"/"inactive" => not Pro
      "license_key": "…",
      "instance_id": "…",
      "instance_name": "hostname",
      "customer_email": "…",
      "activated_at": "ISO-8601"
    }
"""
from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _samurai_home() -> Path:
    """~/.samurai, overridable via SAMURAI_HOME (tests + non-default installs)."""
    override = os.environ.get("SAMURAI_HOME")
    return Path(override) if override else Path.home() / ".samurai"


def license_path() -> Path:
    return _samurai_home() / "license.json"


def read_entitlement() -> dict[str, Any] | None:
    """The stored entitlement dict, or None if absent/unreadable. Never raises."""
    try:
        return json.loads(license_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def is_pro() -> bool:
    """True only when a VALID, ACTIVE, non-refunded Pro entitlement is on disk.

    Fail-closed: any absence, malformation, or non-active status => False (Free).
    This is the one function every Pro gate calls; keep it total and side-effect-free."""
    ent = read_entitlement()
    if not isinstance(ent, dict):
        return False
    return (
        ent.get("tier") == "pro"
        and ent.get("valid") is True
        and ent.get("status") == "active"
        and not ent.get("refunded", False)
    )


def status() -> dict[str, Any]:
    """Human/CLI-facing entitlement summary. Always returns a dict (never raises)."""
    ent = read_entitlement()
    if not ent:
        return {"tier": "free", "activated": False,
                "reason": "no license found — running the Free tier"}
    return {
        "tier": "pro" if is_pro() else "free",
        "activated": is_pro(),
        "provider": ent.get("provider", "gumroad") if is_pro() else None,
        "status": ent.get("status"),
        "license_key": _mask_key(ent.get("license_key", "")),
        "instance_name": ent.get("instance_name"),
        "customer_email": _mask_email(ent.get("customer_email")),
        "activated_at": ent.get("activated_at"),
        **({"reason": "license present but not active (refunded/inactive)"}
           if not is_pro() else {}),
    }


def dashboard_summary() -> dict[str, str]:
    """Tier-only entitlement for wid_payload.json. Carries no key or email, because the
    payload is served to the dashboard."""
    return {"tier": "pro" if is_pro() else "free"}


def _is_unreachable(result: dict[str, Any]) -> bool:
    return "could not reach" in str(result.get("error", "")).lower()


def _mask_key(key: str) -> str:
    """Never echo a full key back to logs/CLI — show only a recognizable tail."""
    if not key or len(key) < 8:
        return "****"
    return f"****{key[-4:]}"


def _mask_email(email: str | None) -> str | None:
    """Show only the first character and the domain, so the CLI still identifies the
    purchase without printing the buyer's full address."""
    if not email:
        return None
    if not isinstance(email, str):
        return "****"
    local, sep, domain = email.strip().rpartition("@")
    if not sep or not local or not domain:
        return "****"
    return f"{local[0]}****@{domain}"


def activate(license_key: str, instance_name: str | None = None) -> dict[str, Any]:
    """Validate + activate a Pro key ONLINE, then persist the entitlement locally.

    Returns {"ok": bool, "message": str, ...}. The one place that touches the network;
    lemonsqueezy_mcp is imported lazily so importing this module never requires it.
    On success writes ~/.samurai/license.json (0600) — the file is_pro() reads forever."""
    key = (license_key or "").strip()
    if not key:
        return {"ok": False, "message": "empty license key"}

    instance = instance_name or socket.gethostname() or "unknown-host"

    # Gumroad is the live storefront; Lemon Squeezy is only a fallback for legacy keys.
    provider = "gumroad"
    try:
        from execution.gumroad_mcp import validate_license_key as g_val  # noqa: PLC0415
        val = g_val(key)
    except Exception:
        val = {}

    # Unreachable Gumroad is a connectivity problem, not a bad key: never fall through to
    # the fallback provider, whose own error would misreport it.
    if _is_unreachable(val):
        return {"ok": False, "message": f"network error: {val['error']}"}

    if val.get("refunded") or val.get("status") == "refunded":
        return {"ok": False, "message": "this license key has been refunded/revoked"}

    if not val.get("valid"):
        # Only a key Gumroad has never seen may be a legacy Lemon Squeezy key; any other
        # Gumroad verdict is final, so the key is not sent to a second provider.
        fallback = {}
        if val.get("not_found"):
            try:
                from execution.lemonsqueezy_mcp import validate_license_key as l_val  # noqa: PLC0415
                fallback = l_val(key)
            except Exception:
                pass
        if not fallback.get("valid"):
            # Gumroad's answer is authoritative. Lemon Squeezy answers an unknown key with
            # HTTP 404, which its client words as "Could not reach", so never surface that.
            return {"ok": False, "message": "license key invalid: "
                    + val.get("error", "not recognized by payment provider")}
        val, provider = fallback, "lemonsqueezy"
        if val.get("refunded") or val.get("status") == "refunded":
            return {"ok": False, "message": "this license key has been refunded/revoked"}

    try:
        if provider == "gumroad":
            from execution.gumroad_mcp import activate_license_key as act_fn  # noqa: PLC0415
        else:
            from execution.lemonsqueezy_mcp import activate_license_key as act_fn  # noqa: PLC0415
        act = act_fn(key, instance)
    except Exception as e:
        act = {"error": str(e)}

    if not act.get("activated"):
        return {"ok": False,
                "message": f"activation failed: {act.get('error', 'unknown activation error')}"}

    entitlement = {
        "tier": "pro",
        "provider": provider,
        "valid": True,
        "status": val.get("status", "active"),
        "refunded": False,
        "license_key": key,
        "instance_id": act.get("instance_id"),
        "instance_name": instance,
        "customer_email": val.get("customer_email"),
        "activated_at": datetime.now(timezone.utc).isoformat(),
        "simulated": bool(val.get("simulated") or act.get("simulated")),
    }
    _write_entitlement(entitlement)
    return {"ok": True, "message": "Order Samurai Pro activated", **status()}


def deactivate() -> dict[str, Any]:
    """Remove the local entitlement (this machine reverts to Free). Idempotent."""
    p = license_path()
    if p.exists():
        try:
            p.unlink()
        except OSError as e:
            return {"ok": False, "message": f"could not remove license: {e}"}
        return {"ok": True, "message": "Pro deactivated on this machine — reverted to Free"}
    return {"ok": True, "message": "no active license — already on Free"}


def _write_entitlement(entitlement: dict[str, Any]) -> None:
    home = _samurai_home()
    home.mkdir(parents=True, exist_ok=True)
    p = license_path()
    p.write_text(json.dumps(entitlement, indent=2), encoding="utf-8")
    try:
        p.chmod(0o600)  # entitlement carries the key + email — owner-only
    except OSError:
        pass


# CLI-facing constant so callers can name the feature set consistently.
PRO_FEATURES = (
    "Nightly Dojo automated regression runs",
    "Explicit human approval for staged repairs",
    "Maker-checker patch staging",
    "Extended telemetry time windows",
)
