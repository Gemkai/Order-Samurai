#!/usr/bin/env python3
"""Order Samurai Pro entitlement — the single authority for "is this machine Pro?".

Activation is online: the key is validated via Gumroad (execution/gumroad_mcp.py; Lemon
Squeezy is a legacy fallback in execution/lemonsqueezy_mcp.py) and the entitlement is
written to ``~/.samurai/license.json``. Every Pro gate then reads that file locally.

Refund window, then offline forever (TERMS.md §1-§2, EULA §3): refunds are allowed for
14 days and revoke the key, while activation is "offline perpetual". So ``is_pro()``
re-verifies with the provider at most once per RECHECK_INTERVAL_HOURS, and only until
purchase + REFUND_WINDOW_DAYS (14-day window + 7 days for refund processing). The re-check
never consumes a seat (Gumroad increment_uses_count=false) and uses a short timeout.
- An affirmative refunded / chargebacked / dispute-lost answer revokes: the file is
  rewritten as refunded and every gate reads Free from then on.
- A network failure or an inconclusive answer never revokes: offline users keep Pro and
  the check is retried after the interval. This is deliberately fail-OPEN on the network
  so the offline-perpetual promise holds; it is fail-CLOSED on a provider's revocation.
- The first good answer at or after the window end marks the entitlement ``settled``;
  a settled entitlement never touches the network again.
Neither the key nor the email is ever logged or returned unmasked.
Limits: api/src/licensing.ts only reads the file, so a refund reaches the TS reflex engine
when any Python gate (CLI, shell gates, aggregate) next runs the check. A dispute that is
still open reads as revoked (gumroad_mcp); a key the seller disables reads as not_found,
which is inconclusive, not a revocation.

Fail-CLOSED to Free: a missing, malformed, refunded, or inactive license.json never
yields Pro. Stdlib only, so the CLI, the reducers and the TS engine (which reads the same
JSON) all agree on one contract.

Contract of ``~/.samurai/license.json`` (also read by api/src/licensing.ts):
    {
      "tier": "pro",
      "valid": true,
      "status": "active",            # "refunded"/"inactive" => not Pro
      "license_key": "…",
      "instance_id": "…",
      "instance_name": "hostname",
      "customer_email": "…",
      "activated_at": "ISO-8601",
      "purchased_at": "ISO-8601",    # provider sale time, else activated_at
      "last_checked_at": "ISO-8601", # last re-check attempt (any outcome)
      "last_verified_at": "ISO-8601",# last good provider answer
      "settled": false               # true => past the refund window, offline forever
    }
"""
from __future__ import annotations

import json
import os
import socket
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

#: 14-day refund guarantee + 7 days for the processor to post the refund.
REFUND_WINDOW_DAYS = 21
#: At most one provider re-check per day while inside the window.
RECHECK_INTERVAL_HOURS = 24
#: Short, because the re-check runs inline in a Pro gate.
REVALIDATE_TIMEOUT_S = 5


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


def _stored_pro(ent: Any) -> bool:
    """The local, offline half of the gate: the file itself says active Pro."""
    return (
        isinstance(ent, dict)
        and ent.get("tier") == "pro"
        and ent.get("valid") is True
        and ent.get("status") == "active"
        and not ent.get("refunded", False)
    )


def is_pro() -> bool:
    """True only when a VALID, ACTIVE, non-refunded Pro entitlement is on disk.

    Fail-closed: any absence, malformation, or non-active status => False (Free).
    While inside the refund window and a re-check is due, performs ONE bounded provider
    re-check first (see the module docstring). Never raises."""
    ent = read_entitlement()
    if not _stored_pro(ent):
        return False
    try:
        if _recheck_due(ent, _now()):
            return revalidate()["tier"] == "pro"
    except Exception:
        pass  # a broken re-check never revokes; the stored entitlement stands
    return True


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(val: Any) -> datetime | None:
    if not isinstance(val, str) or not val:
        return None
    try:
        dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _recheck_due(ent: dict[str, Any], now: datetime) -> bool:
    if ent.get("simulated") or ent.get("settled"):
        return False
    last = _parse_ts(ent.get("last_checked_at"))
    if last is None or last > now + timedelta(hours=1):  # clock was ahead: re-check
        return True
    return now - last >= timedelta(hours=RECHECK_INTERVAL_HOURS)


def revalidate(force: bool = False, now: datetime | None = None) -> dict[str, Any]:
    """Re-verify a stored Pro entitlement with its provider (refund-window check).

    Returns {"tier": "pro"|"free", "result": ...}, result being one of:
      skipped      — nothing to check (no Pro, simulated, settled, or checked < interval)
      active       — provider confirms the purchase; still inside the refund window
      settled      — provider confirms it past the window; never re-checked again
      revoked      — provider reports refunded/chargebacked/dispute lost => Free
      unreachable  — network failure; Pro kept, retried after the interval
      inconclusive — any other non-answer (e.g. key unknown); Pro kept
      error        — the provider module cannot be imported (broken install); Pro kept
    ``force`` (CLI ``samurai license --refresh``) bypasses the interval and ``settled``.
    The result never carries the key or the email."""
    now = now or _now()
    ent = read_entitlement()
    if not _stored_pro(ent):
        return {"tier": "free", "result": "skipped"}
    if ent.get("simulated"):
        return {"tier": "pro", "result": "skipped"}
    if not force and not _recheck_due(ent, now):
        return {"tier": "pro", "result": "skipped"}

    key = str(ent.get("license_key") or "")
    try:
        if ent.get("provider", "gumroad") == "lemonsqueezy":
            import execution.lemonsqueezy_mcp  # noqa: F401, PLC0415
        else:
            import execution.gumroad_mcp  # noqa: F401, PLC0415
    except Exception:
        # Broken install, not a network problem: keep Pro (nothing proves a refund) but
        # report it distinctly so `samurai license --refresh` and the E2E kit show it.
        val = {"valid": False, "_import_error": True}
    else:
        val = _ask_provider(ent, key)
    if not isinstance(val, dict):
        val = {}
    return _apply_answer(ent, val, now)


def _ask_provider(ent: dict[str, Any], key: str) -> Any:
    try:
        if ent.get("provider", "gumroad") == "lemonsqueezy":
            from execution.lemonsqueezy_mcp import validate_license_key as l_val  # noqa: PLC0415
            val = l_val(key, instance_id=ent.get("instance_id"), timeout=REVALIDATE_TIMEOUT_S)
        else:
            from execution.gumroad_mcp import validate_license_key as g_val  # noqa: PLC0415
            val = g_val(key, increment_uses_count=False, timeout=REVALIDATE_TIMEOUT_S)
    except Exception:
        # The exception text may echo request data; never surface it.
        val = {"valid": False, "error": "could not reach payment provider"}
    return val


def _apply_answer(ent: dict[str, Any], val: dict[str, Any], now: datetime) -> dict[str, Any]:
    stamp = now.isoformat()
    ident = (ent.get("license_key"), ent.get("instance_id"))
    if _is_refunded(val):
        _commit(ident, lambda cur: cur.update(
            status="refunded", refunded=True, valid=False,
            last_checked_at=stamp, revoked_at=stamp))
        return {"tier": "free", "result": "revoked"}
    if val.get("valid"):
        purchased = _parse_ts(ent.get("purchased_at")) or _parse_ts(ent.get("activated_at"))
        fields = {"last_checked_at": stamp, "last_verified_at": stamp}
        if purchased is None:  # no usable date: the window starts now
            fields["purchased_at"] = stamp
            purchased = now
        settled = now >= purchased + timedelta(days=REFUND_WINDOW_DAYS)
        fields["settled"] = settled
        _commit(ident, lambda cur: cur.update(fields))
        return {"tier": "pro", "result": "settled" if settled else "active"}
    _commit(ident, lambda cur: cur.update(last_checked_at=stamp))
    if val.get("_import_error"):
        return {"tier": "pro", "result": "error"}
    return {"tier": "pro", "result": "unreachable" if _is_unreachable(val) else "inconclusive"}


def _commit(ident: tuple[Any, Any], apply: Any) -> None:
    """Apply a re-check result to the license.json that is on disk NOW.

    The provider call takes seconds; meanwhile another gate may have revoked the key, or
    the user may have run `samurai deactivate` or activated a different key. So re-read
    under a lock and update only a still-Pro file for the same key and instance. A stale
    copy is never written back, so nothing can resurrect a revoked or removed license.
    Best effort: an unwritable home must not raise (is_pro() would then keep Pro)."""
    try:
        with _license_lock():
            cur = read_entitlement()
            if not _stored_pro(cur) or (cur.get("license_key"), cur.get("instance_id")) != ident:
                return
            apply(cur)
            _write_entitlement(cur)
    except OSError:
        pass


class _license_lock:
    """Advisory exclusive lock on ~/.samurai/.license.lock (no-op where fcntl is absent)."""

    def __enter__(self):
        self._fh = None
        try:
            import fcntl  # noqa: PLC0415
        except ImportError:
            return self
        home = _samurai_home()
        home.mkdir(parents=True, exist_ok=True)
        self._fh = open(home / ".license.lock", "a")
        fcntl.flock(self._fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if self._fh is not None:
            self._fh.close()  # closing releases the flock
        return False


def status() -> dict[str, Any]:
    """Human/CLI-facing entitlement summary. Always returns a dict (never raises).
    The key and the email are masked."""
    ent = read_entitlement()
    if not ent or not isinstance(ent, dict):
        return {"tier": "free", "activated": False,
                "reason": "no license found — running the Free tier"}
    pro = is_pro()
    ent = read_entitlement() or ent  # is_pro() may have re-checked and rewritten it
    return {
        "tier": "pro" if pro else "free",
        "activated": pro,
        "provider": ent.get("provider", "gumroad") if pro else None,
        "status": ent.get("status"),
        "license_key": _mask_key(ent.get("license_key", "")),
        "instance_name": ent.get("instance_name"),
        "customer_email": _mask_email(ent.get("customer_email")),
        "activated_at": ent.get("activated_at"),
        "last_verified_at": ent.get("last_verified_at"),
        "settled": bool(ent.get("settled")),
        **({"reason": "license present but not active (refunded/inactive)"}
           if not pro else {}),
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


def _mask_email(email: Any) -> str | None:
    """b***@example.com — enough to recognize, not enough to harvest."""
    if not email:
        return None
    local, sep, domain = str(email).partition("@")
    if not sep or not local:
        return "****"
    return f"{local[0]}***@{domain}"


def _is_refunded(val: dict[str, Any]) -> bool:
    """True when a provider's validate_license_key() response marks the key
    refunded/revoked (chargebacks and lost disputes arrive as refunded=True)."""
    return bool(val.get("refunded")) or val.get("status") == "refunded"


def activate(license_key: str, instance_name: str | None = None) -> dict[str, Any]:
    """Validate + activate a Pro key ONLINE, then persist the entitlement locally.

    Returns {"ok": bool, "message": str, ...}. The one place that touches the network;
    lemonsqueezy_mcp is imported lazily so importing this module never requires it.
    On success writes ~/.samurai/license.json (0600) — the file every gate reads."""
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

    activated_at = datetime.now(timezone.utc).isoformat()
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
        "activated_at": activated_at,
        "purchased_at": val.get("purchased_at") or activated_at,
        "last_checked_at": activated_at,
        "last_verified_at": activated_at,
        "settled": False,
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
    """Atomic 0600 write: a gate reading concurrently sees the old file or the new one."""
    home = _samurai_home()
    home.mkdir(parents=True, exist_ok=True)
    p = license_path()
    fd, tmp = tempfile.mkstemp(prefix=".license.", suffix=".tmp", dir=home)  # 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:  # entitlement carries key + email
            fh.write(json.dumps(entitlement, indent=2))
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# CLI-facing constant so callers can name the feature set consistently.
PRO_FEATURES = (
    "Nightly Dojo automated regression runs",
    "Explicit human approval for staged repairs",
    "Maker-checker patch staging",
    "Extended telemetry time windows",
)
