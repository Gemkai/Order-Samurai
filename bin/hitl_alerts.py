#!/usr/bin/env python3
"""hitl_alerts.py — surface the HITL approval queue to the human.

The maker-checker loop's weak seam (found 2026-07-19): Bushido routes queue-tier reflexes
to state/hitl_queue.json for human sign-off, but nothing showed the human the queue — items
expired unreviewed. This script is the delivery layer, two modes on one reader:

  --notify   Desktop banner via ~/.claude/scripts/notify.py (the cross-platform notifier from
             the 2026-07-19 nudge redesign). One AGGREGATED banner when the pending set changes,
             plus a 24h re-reminder while anything stays pending. Never one-banner-per-item.
             Scheduled: com.agentica.hitl-notifier (30 min).
  --email    Daily digest email (pending + recently-expired-unreviewed). Recipient: HITL_DIGEST_TO,
             REQUIRED — the mode exits 1 without sending when it is unset. Transport: Resend API
             when RESEND_API_KEY is set, else macOS Mail.app via osascript (values passed as
             argv — never string-interpolated into AppleScript). Once-per-day guard in state;
             --force overrides for testing. Scheduled: com.agentica.hitl-digest (08:00 daily).

Both modes are read-only over the queue; state lives in state/hitl_alert_state.json.
Morning-joe consumption is separate (build_payload.py hitl_section reads the queue directly).

2026-08-08 hardening (the propose-only lane's saved patch had no human-facing surface):
  • Both modes also surface state/pending_remediation_*.patch — validated propose-only
    patches the reflex engine saved for human review (count + filename + age). Review CLI:
    bin/review_pending_patch.py. Kill switch: HITL_PATCH_SURFACE=false.
    reflex-engine.ts's _enqueuePendingPatchHitl (2026-08-02, REFLEX_PATCH_HITL_ENQUEUE) already
    routes each patch onto hitl_queue.json as a `source: 'reflex_patch'` item — this glob is a
    second, independent surface (it still shows a patch if that enqueue was off or failed) and
    load_queue() de-duplicates the two against each other so a live patch is never counted twice.
  • The digest gains a read-only PROPOSED_BACKLOG section (pending approved:false count +
    oldest-item age). Kill switch: HITL_BACKLOG_SURFACE=false.
  • --notify raises a delivery-lag banner when last_email_date falls >1 day behind while
    items are pending (the digest died silently when Mail.app was closed, 2026-08-04).
    Kill switch: HITL_LAG_ALARM=false.
  • The Mail.app transport launches Mail in the background (launch, not activate) before
    composing, so a closed Mail.app no longer kills the send with AppleEvent -600.
    Kill switch: HITL_MAIL_LAUNCH=false.

2026-08-09 fleet health (five launchd jobs were failing with nothing surfacing anywhere
until the next morning's mechanism-audit read — see HANDOFF-nightly-goal-and-routine-audit-
2026-08-09.md): --notify now also reads Governance/data/fleet_probe.json (written by
bin/fleet_probe.py) and raises a banner naming failing launchd jobs / unreachable local
services, deduped against the last-seen failure set so an unchanged failure re-banners only
on the same 24h REMIND_HOURS cadence as the queue banner. --email gains a matching FLEET
HEALTH digest section. Kill switch: HITL_FLEET_ALARM=false.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")  # Windows cp1252 guard

_ROOT = Path(os.environ.get("ORDER_SAMURAI_ROOT", str(Path(__file__).resolve().parents[1])))
QUEUE_PATH = _ROOT / "state" / "hitl_queue.json"
STATE_PATH = _ROOT / "state" / "hitl_alert_state.json"
PATCH_DIR = _ROOT / "state"                       # reflex-engine writes pending patches here
PATCH_GLOB = "pending_remediation_*.patch"        # must match reflex-engine.ts patchIdSlug naming
BACKLOG_PATH = _ROOT / "state" / "PROPOSED_BACKLOG.json"
FLEET_PROBE_PATH = _ROOT.parent / "data" / "fleet_probe.json"    # written by bin/fleet_probe.py
NOTIFY_PY = Path.home() / ".claude" / "scripts" / "notify.py"
DASHBOARD_URL = "http://127.0.0.1:4322/"

REMIND_HOURS = 24          # re-banner cadence while items stay pending
EXPIRED_WINDOW_DAYS = 7    # digest includes items that expired unreviewed this recently
EMAIL_LAG_DAYS = 1         # digest is daily; further behind than this with items pending = lagging
SUBPROCESS_TIMEOUT = 30    # every external call gets a timeout (Release It! rule)
MAIL_TIMEOUT = 90          # Mail.app path may cold-start Mail (launch + delay) before sending


def _flag(name: str) -> bool:
    """Env kill-switch, default ON (the fixed behavior); only the literal 'false' disables.
    Mirrors the reflex-engine pattern (REFLEX_VERIFY_GATE et al.); read at call time so a
    scheduled run picks up an env change without a module reload."""
    return os.environ.get(name, "true").strip().lower() != "false"


class QueueReadError(RuntimeError):
    """The approval queue could not be read or did not satisfy its schema."""


class AlertStateError(RuntimeError):
    """Alert delivery state could not be read or durably persisted."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _fmt_days(days: int) -> str:
    return f"{days}d" if days < 14 else f"{days // 7}wk"


def _fmt_age(t: datetime | None) -> str:
    return _fmt_days((_now() - t).days) if t else "?"


def _age_str(item: dict) -> str:
    return _fmt_age(_parse_ts(item.get("enqueued_at")))


def load_pending_patches() -> list[dict]:
    """Validated propose-only patches awaiting human review, oldest first.

    The reflex engine's propose-only lane (REFLEX_AUTO_APPLY=false) writes its entire
    product to state/pending_remediation_*.patch. reflex-engine.ts's
    _enqueuePendingPatchHitl already routes each one onto hitl_queue.json too (source
    'reflex_patch'); this glob is a second, independent surface — it still shows a
    patch when that enqueue is off (REFLEX_PATCH_HITL_ENQUEUE=false) or failed — and
    load_queue() below de-duplicates the two so a live patch is never double-counted.
    A missing state dir globs to empty — that is fine here because an unreadable
    QUEUE_PATH in the same dir already fails the run loudly.
    """
    if not _flag("HITL_PATCH_SURFACE"):
        return []
    patches = []
    for p in sorted(PATCH_DIR.glob(PATCH_GLOB)):
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue  # raced with a concurrent review/apply — the patch is being handled
        patches.append({"name": p.name, "path": str(p), "mtime": mtime})
    patches.sort(key=lambda p: p["mtime"])
    return patches


def load_backlog_summary() -> dict | None:
    """Read-only PROPOSED_BACKLOG surface: pending (approved:false) count + oldest age.

    Purely informational — never a send-trigger and never a reason to fail the run.
    Missing file → None (nothing to report); unreadable/malformed → {'error': ...} so the
    digest says "unreadable" rather than silently equating a broken file with empty.
    """
    if not _flag("HITL_BACKLOG_SURFACE"):
        return None
    try:
        raw = BACKLOG_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        return {"error": str(exc)}
    try:
        d = json.loads(raw)
    except ValueError as exc:
        return {"error": f"invalid JSON: {exc}"}
    items = d.get("items") if isinstance(d, dict) else d
    if not isinstance(items, list):
        return {"error": "unexpected schema (no items list)"}
    pending = [i for i in items if isinstance(i, dict) and i.get("approved") is False]
    oldest_days = None
    for i in pending:
        # triaged_at is free text with a YYYY-MM-DD prefix on triaged items; unparseable
        # or absent dates simply don't contribute to the age.
        try:
            t = datetime.fromisoformat(str(i.get("triaged_at", ""))[:10]).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        days = (_now() - t).days
        oldest_days = days if oldest_days is None else max(oldest_days, days)
    return {"pending": len(pending), "oldest_days": oldest_days}


def load_fleet_probe() -> dict | None:
    """Read-only fleet_probe.json surface: failing launchd jobs + unreachable local services.

    Written by bin/fleet_probe.py, scheduled to ride the existing 30-min hitl-notifier
    carrier rather than a new launchd job. Missing file -> None (probe hasn't run yet,
    not the same as "fleet healthy"); unreadable/malformed -> {'error': ...} so a banner
    or digest can say "unreadable" instead of silently equating a broken file with a
    clean fleet — same discipline as load_backlog_summary().
    """
    if not _flag("HITL_FLEET_ALARM"):
        return None
    try:
        raw = FLEET_PROBE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        return {"error": str(exc)}
    try:
        d = json.loads(raw)
    except ValueError as exc:
        return {"error": f"invalid JSON: {exc}"}
    if not isinstance(d, dict):
        return {"error": "unexpected schema (not an object)"}
    failing = d.get("failing_jobs")
    unreachable = d.get("unreachable_services")
    if not isinstance(failing, list) or not isinstance(unreachable, list):
        return {"error": "unexpected schema (missing failing_jobs/unreachable_services list)"}
    return {"failing_jobs": failing, "unreachable_services": unreachable,
            "generated_at": d.get("generated_at")}


#  Matches the filename token itself, not "patch=<rest of the string>": the patch's
#  absolute path embeds ".../Order Samurai/state/..." — a directory name WITH a space —
#  so a whitespace-delimited capture off "patch=" would truncate mid-path. patchIdSlug
#  (reflex-engine.ts) replaces every non [A-Za-z0-9_-] char before ".patch", so the
#  filename itself is always contiguous and space-free regardless of where it lives.
_PATCH_FILENAME_RE = re.compile(r"(pending_remediation_[A-Za-z0-9_-]+\.patch)")


def _reflex_patch_filename(item: dict) -> str | None:
    """The pending-patch filename a `source: 'reflex_patch'` queue item names, or None.

    _enqueuePendingPatchHitl (reflex-engine.ts) embeds `patch=<abs path>` in the item's
    free-text context; this is the only link between that queue row and the patch file
    load_pending_patches() finds by globbing state/ directly. Returns None for any item
    that isn't a reflex_patch enqueue or whose context doesn't name a patch file —
    callers must treat None as "not a dedup candidate", never as a match.
    """
    if item.get("source") != "reflex_patch":
        return None
    m = _PATCH_FILENAME_RE.search(item.get("context") or "")
    return m.group(1) if m else None


def load_queue() -> tuple[list[dict], list[dict], list[dict]]:
    """Return (pending, recently expired, pending patches); never equate unreadable with empty.

    A `source: 'reflex_patch'` pending item and the on-disk patch it names are the SAME
    event surfaced two ways (see load_pending_patches' docstring) — counting both would
    double the banner/digest total for one artifact. Drop the queue-item copy whenever its
    named patch is still on disk; a reflex_patch item whose patch is already gone (archived
    by review_pending_patch.py, or the context didn't parse) stays visible on purpose —
    under-surfacing a genuinely-unresolved approval is worse than a rare stale duplicate.
    """
    try:
        raw = QUEUE_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise QueueReadError(f"cannot read {QUEUE_PATH}: {exc}") from exc
    try:
        d = json.loads(raw)
    except ValueError as exc:
        raise QueueReadError(f"invalid JSON in {QUEUE_PATH}: {exc}") from exc
    if isinstance(d, dict):
        if "items" not in d:
            raise QueueReadError(f"invalid queue schema in {QUEUE_PATH}: missing 'items'")
        items = d["items"]
    elif isinstance(d, list):
        items = d
    else:
        raise QueueReadError(f"invalid queue schema in {QUEUE_PATH}: expected object or list")
    if not isinstance(items, list):
        raise QueueReadError(f"invalid queue schema in {QUEUE_PATH}: 'items' is not a list")
    patches = load_pending_patches()
    patch_names_on_disk = {p["name"] for p in patches}
    pending = [
        i for i in items
        if isinstance(i, dict) and i.get("status") == "pending"
        and _reflex_patch_filename(i) not in patch_names_on_disk
    ]
    expired = []
    for i in items:
        if not (isinstance(i, dict) and i.get("status") == "expired"):
            continue
        t = _parse_ts(i.get("expired_at"))
        if t and (_now() - t).days <= EXPIRED_WINDOW_DAYS:
            expired.append(i)
    return pending, expired, patches


def _load_state() -> dict:
    try:
        raw = STATE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise AlertStateError(f"cannot read {STATE_PATH}: {exc}") from exc
    try:
        state = json.loads(raw)
    except ValueError as exc:
        raise AlertStateError(f"invalid JSON in {STATE_PATH}: {exc}") from exc
    if not isinstance(state, dict):
        raise AlertStateError(f"invalid alert state in {STATE_PATH}: expected object")
    return state


def _save_state(state: dict) -> None:
    tmp = STATE_PATH.with_name(f".{STATE_PATH.name}.{os.getpid()}.tmp")
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(STATE_PATH)
    except OSError as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise AlertStateError(f"cannot persist {STATE_PATH}: {exc}") from exc


def _item_line(i: dict) -> str:
    return (f"{i.get('command', '?')} — {i.get('pillar', '?')}/"
            f"{(i.get('metric_id') or '').split(':')[-1]} · waiting {_age_str(i)}"
            f"{' · blast: ' + i['blast_radius'] if i.get('blast_radius') else ''}")


# ── notify mode ──────────────────────────────────────────────────────────────

def _dispatch_banner(title: str, body: str, subtitle: str) -> bool:
    """One aggregated desktop banner via notify.py; True only on an acknowledged dispatch."""
    env = {**os.environ, "NUDGE_DESKTOP_NOTIFY": "true"}
    try:
        out = subprocess.run(
            [sys.executable, str(NOTIFY_PY), title, body, subtitle, "--severity", "HIGH"],
            env=env, timeout=SUBPROCESS_TIMEOUT, capture_output=True,
        )
        detail = (out.stderr or b"")
        if isinstance(detail, bytes):
            detail = detail.decode(errors="replace")
        # notify.py intentionally exits zero for both "dispatched" and
        # "suppressed". Its explicit acknowledgement is therefore part of
        # the delivery contract; exit status alone is not confirmation.
        acknowledged = detail.strip().splitlines()[-1:] == ["dispatched"]
        if out.returncode != 0 or not acknowledged:
            print(
                f"hitl_alerts: notify dispatch failed (exit {out.returncode}): "
                f"{detail.strip()[:200]}",
                file=sys.stderr,
            )
            return False
        return True
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"hitl_alerts: notify dispatch failed: {exc}", file=sys.stderr)
        return False


def _maybe_lag_banner(state: dict, n_waiting: int) -> None:
    """Delivery-lag alarm: the daily digest died silently for days when Mail.app was closed
    (no delivery since 2026-08-04, exit 1 monitored by nothing). When last_email_date falls
    more than EMAIL_LAG_DAYS behind while items are waiting, raise a banner saying so, on the
    same 24h re-reminder cadence as the queue banner. Mutates state (last_lag_banner_at) only
    on an acknowledged dispatch; the caller persists it. Dispatch failure is non-fatal — the
    queue banner is this mode's primary product."""
    if not _flag("HITL_LAG_ALARM") or n_waiting == 0:
        return
    last_email = state.get("last_email_date")
    if not last_email:
        return  # never delivered / fresh install — nothing to fall behind from
    try:
        lag_days = (_now().date() - datetime.fromisoformat(last_email).date()).days
    except ValueError:
        return
    if lag_days <= EMAIL_LAG_DAYS:
        return
    last_lag = _parse_ts(state.get("last_lag_banner_at"))
    if last_lag is not None and (_now() - last_lag).total_seconds() <= REMIND_HOURS * 3600:
        return
    if _dispatch_banner(
        "Order Samurai: digest delivery is LAGGING",
        f"no HITL digest email since {last_email} ({lag_days}d ago) with {n_waiting} item(s) waiting",
        "check Mail.app / hitl-digest launchd log",
    ):
        state["last_lag_banner_at"] = _now().isoformat()


def _maybe_fleet_banner(state: dict) -> bool:
    """Fleet-health alarm: launchd failures and unreachable local services used to surface
    only at the next 07:00 mechanism-audit read (see fleet_probe.py's docstring for the
    2026-08-09 incident this closes). Banners on a CHANGE in the failing/unreachable set
    (a fresh problem, or the set shrinking as things recover), then re-reminds on the same
    24h REMIND_HOURS cadence as the queue banner while the set stays non-empty and
    unchanged — never once per 30-min poll. Mutates state (last_fleet_signature,
    last_fleet_banner_at) only on an acknowledged dispatch; the caller persists it.
    Missing/unreadable probe data is silently skipped, not alarmed on — a probe that
    hasn't run yet or glitched once is not itself a fleet failure. Returns True only on
    an acknowledged dispatch, so do_notify()'s own summary line can report it accurately."""
    if not _flag("HITL_FLEET_ALARM"):
        return False
    probe = load_fleet_probe()
    if probe is None or "error" in probe:
        return False
    failing = sorted(probe.get("failing_jobs") or [])
    unreachable = sorted(probe.get("unreachable_services") or [])
    signature = "|".join([f"job:{j}" for j in failing] + [f"svc:{s}" for s in unreachable])
    prev_signature = state.get("last_fleet_signature", "")
    if not signature:
        if prev_signature:
            # Fleet recovered. Clear the stored signature — otherwise a future
            # recurrence of the IDENTICAL failure set would be wrongly read as
            # "unchanged" and suppressed, even though it's a fresh incident.
            state["last_fleet_signature"] = ""
        return False
    last_banner = _parse_ts(state.get("last_fleet_banner_at"))
    stale = last_banner is None or (_now() - last_banner).total_seconds() > REMIND_HOURS * 3600
    if signature == prev_signature and not stale:
        return False
    parts = []
    if failing:
        parts.append(f"{len(failing)} job(s) failing: {', '.join(failing[:3])}"
                     f"{' …' if len(failing) > 3 else ''}")
    if unreachable:
        parts.append(f"unreachable: {', '.join(unreachable)}")
    dispatched = _dispatch_banner(
        f"Order Samurai: fleet health — {len(failing) + len(unreachable)} issue(s)",
        "; ".join(parts),
        "check launchctl list / OrbStack, or run bin/fleet_probe.py",
    )
    if dispatched:
        state["last_fleet_signature"] = signature
        state["last_fleet_banner_at"] = _now().isoformat()
    return dispatched


FLEET_PROBE_BIN = _ROOT.parent / "bin" / "fleet_probe.py"


def _refresh_fleet_probe() -> None:
    """Re-run bin/fleet_probe.py inline so the existing 30-min --notify cadence keeps
    fleet_probe.json fresh WITHOUT a new launchd job (Mechanism budget). --fast skips
    fleet_probe's own DarkWake backoff window (~40s worst case) since --notify already
    re-samples every 30 minutes regardless — a false-unreachable reading from hitting a
    resuming VM gets corrected by the very next cycle, so paying for the long window here
    would only slow down every single notify run for a benefit the cadence already covers.
    Failure is logged, never raised: a probe hiccup must degrade to a stale-but-present
    reading (load_fleet_probe still serves the last successful write), not take down the
    approval-queue banner, which is this mode's primary product."""
    if not _flag("HITL_FLEET_ALARM"):
        return
    try:
        subprocess.run(
            [sys.executable, str(FLEET_PROBE_BIN), "--fast"],
            timeout=SUBPROCESS_TIMEOUT, capture_output=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"hitl_alerts: fleet_probe refresh failed: {exc}", file=sys.stderr)


def do_notify() -> int:
    try:
        pending, _, patches = load_queue()
        state = _load_state()
    except (QueueReadError, AlertStateError) as exc:
        print(f"hitl_alerts --notify: FAILED — {exc}", file=sys.stderr)
        return 1
    prev_ids = set(state.get("last_pending_ids", []))
    # Pending patches join the change set under their filename so a newly proposed patch
    # re-banners exactly like a newly enqueued approval.
    cur_ids = {str(i["id"]) for i in pending if i.get("id")} | {p["name"] for p in patches}
    last_banner = _parse_ts(state.get("last_banner_at"))
    stale = last_banner is None or (_now() - last_banner).total_seconds() > REMIND_HOURS * 3600
    n_waiting = len(pending) + len(patches)

    should = n_waiting > 0 and (cur_ids != prev_ids or stale)
    sent = False
    if should:
        if pending:
            top = _item_line(pending[0])
        else:
            top = f"{patches[0]['name']} — validated patch waiting {_fmt_age(patches[0]['mtime'])}"
        extra = len(pending) + len(patches) - 1
        more = f" (+{extra} more)" if extra else ""
        patch_note = f" · {len(patches)} validated patch(es)" if pending and patches else ""
        sent = _dispatch_banner(
            f"Order Samurai: {n_waiting} approval(s) waiting",
            f"{top}{more}{patch_note}",
            "review at the dashboard HITL queue",
        )
        if not sent:
            print(f"hitl_alerts --notify: {n_waiting} pending; banner FAILED")
            return 1
        state["last_banner_at"] = _now().isoformat()
        state["last_pending_ids"] = sorted(cur_ids)
    elif state.get("last_pending_ids", []) != sorted(cur_ids):
        # Clearing the snapshot is not a delivery record; it lets a future item
        # with a reused ID be treated as new.
        state["last_pending_ids"] = sorted(cur_ids)

    # After the queue banner so a failed run keeps its "state untouched" guarantee.
    _maybe_lag_banner(state, n_waiting)
    _refresh_fleet_probe()
    fleet_sent = _maybe_fleet_banner(state)

    try:
        _save_state(state)
    except AlertStateError as exc:
        delivery = " after banner delivery" if (sent or fleet_sent) else ""
        print(f"hitl_alerts --notify: state persistence FAILED{delivery} — {exc}", file=sys.stderr)
        return 1
    fleet_note = " · fleet banner sent" if fleet_sent else ""
    print(f"hitl_alerts --notify: {n_waiting} pending; banner "
          f"{'sent' if sent else 'suppressed'}{fleet_note}")
    return 0


# ── email mode ───────────────────────────────────────────────────────────────

def _digest_body(pending: list[dict], expired: list[dict],
                 patches: list[dict] | None = None,
                 backlog: dict | None = None,
                 fleet: dict | None = None) -> str:
    lines = [f"Order Samurai — HITL approval digest ({_now().date().isoformat()})", ""]
    if pending:
        lines.append(f"AWAITING YOUR APPROVAL ({len(pending)}):")
        for i in pending:
            lines.append(f"  • {_item_line(i)}")
            ctx = (i.get("context") or "").strip()
            if ctx:
                lines.append(f"      {ctx[:200]}")
    else:
        lines.append("No approvals pending. ✔")
    if patches:
        lines.append("")
        lines.append(f"VALIDATED PATCHES AWAITING REVIEW ({len(patches)}):")
        for p in patches:
            lines.append(f"  • {p['name']} · waiting {_fmt_age(p['mtime'])}")
        lines.append("      review: python3 bin/review_pending_patch.py --list / --show / "
                     "--apply / --reject")
    if expired:
        lines.append("")
        lines.append(f"EXPIRED UNREVIEWED in the last {EXPIRED_WINDOW_DAYS}d ({len(expired)}):")
        for i in expired:
            lines.append(f"  • {i.get('command', '?')} ({i.get('pillar', '?')}) — "
                         f"expired {str(i.get('expired_at', ''))[:10]}")
    if backlog is not None:
        lines.append("")
        if "error" in backlog:
            lines.append(f"PROPOSED_BACKLOG: unreadable — {backlog['error']}")
        else:
            oldest = (_fmt_days(backlog["oldest_days"])
                      if backlog["oldest_days"] is not None else "?")
            lines.append(f"PROPOSED_BACKLOG (read-only): {backlog['pending']} pending "
                         f"(approved:false) · oldest {oldest}")
    if fleet is not None:
        lines.append("")
        if "error" in fleet:
            lines.append(f"FLEET HEALTH: unreadable — {fleet['error']}")
        else:
            failing = fleet.get("failing_jobs") or []
            unreachable = fleet.get("unreachable_services") or []
            if not failing and not unreachable:
                lines.append("FLEET HEALTH: all launchd jobs + local services OK ✔")
            else:
                lines.append("FLEET HEALTH:")
                for j in failing:
                    lines.append(f"  • launchd job failing: {j}")
                for s in unreachable:
                    lines.append(f"  • service unreachable: {s}")
    lines += ["", f"Review: {DASHBOARD_URL}", "— hitl_alerts.py (daily digest)"]
    return "\n".join(lines)


# Assembled from three parts so the launch preamble can be toggled (HITL_MAIL_LAUNCH).
_MAIL_SCRIPT_HEAD = '''on run argv
  set theSubject to item 1 of argv
  set theBody to item 2 of argv
  set theTo to item 3 of argv'''

# Mail.app must be RUNNING to receive the compose AppleEvent; when it is closed the event
# dies with -600 after a long block and the daily digest goes silently undelivered
# (2026-08-04..08 incident). The AppleScript `launch` event is NOT a fix: on this macOS
# version it throws the same -600 itself when Mail is closed (verified 2026-08-09), so the
# pre-launch must happen outside AppleScript — `open -gja Mail` in _ensure_mail_running(),
# then poll for the process before sending any AppleEvent.
def _ensure_mail_running(timeout_s: float = 20.0) -> bool:
    """Start Mail.app in the background (no focus steal) and wait until its process
    exists. `open -gja` is the only pre-launch that works when Mail is fully closed —
    AppleEvents (launch/activate) to a non-running app die with -600 on this host."""
    check = subprocess.run(["pgrep", "-x", "Mail"], capture_output=True, timeout=5)
    if check.returncode == 0:
        return True
    launched = subprocess.run(["open", "-gja", "Mail"], capture_output=True, timeout=10)
    if launched.returncode != 0:
        print("hitl_alerts: `open -gja Mail` failed: "
              f"{launched.stderr.decode(errors='replace').strip()[:200]}", file=sys.stderr)
        return False
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if subprocess.run(
            ["pgrep", "-x", "Mail"], capture_output=True, timeout=5
        ).returncode == 0:
            time.sleep(2)  # give Mail a beat past process-exists before the first AppleEvent
            return True
        time.sleep(0.5)
    print("hitl_alerts: Mail.app did not start within "
          f"{timeout_s:.0f}s of `open -gja Mail`", file=sys.stderr)
    return False

_MAIL_SCRIPT_SEND = '''
  tell application "Mail"
    set msg to make new outgoing message with properties {subject:theSubject, content:theBody, visible:false}
    tell msg to make new to recipient at end of to recipients with properties {address:theTo}
    send msg
  end tell
end run'''


def _send_resend(subject: str, body: str, to: str, key: str) -> bool:
    import urllib.request
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps({"from": "Order Samurai <onboarding@resend.dev>",
                         "to": [to], "subject": subject, "text": body}).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=SUBPROCESS_TIMEOUT) as r:
            raw = r.read()
            if not 200 <= r.status < 300:
                return False
            try:
                response = json.loads(raw)
            except (TypeError, ValueError):
                print("hitl_alerts: resend returned invalid JSON", file=sys.stderr)
                return False
            if not isinstance(response, dict) or not response.get("id"):
                print("hitl_alerts: resend did not confirm a message id", file=sys.stderr)
                return False
            return True
    except (OSError, ValueError) as exc:
        print(f"hitl_alerts: resend failed: {exc}", file=sys.stderr)
        return False


def _send_mail_app(subject: str, body: str, to: str) -> bool:
    if _flag("HITL_MAIL_LAUNCH") and not _ensure_mail_running():
        return False
    script = _MAIL_SCRIPT_HEAD + _MAIL_SCRIPT_SEND
    try:
        out = subprocess.run(
            ["osascript", "-", subject, body, to],
            input=script, text=True,
            timeout=MAIL_TIMEOUT, capture_output=True,
        )
        if out.returncode != 0:
            print(f"hitl_alerts: Mail.app send failed: {out.stderr.strip()[:200]}", file=sys.stderr)
        return out.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"hitl_alerts: Mail.app send failed: {exc}", file=sys.stderr)
        return False


def do_email(force: bool) -> int:
    # The recipient is deployment config, never a code default. It carried the
    # author's own address until 2026-07-31, which shipped a personal identifier
    # in the product and made a misconfigured install mail a stranger rather than
    # report that it had no recipient. Fail loudly instead — a scheduled digest
    # that silently mails nowhere is indistinguishable from a working one.
    to = os.environ.get("HITL_DIGEST_TO", "").strip()
    if not to:
        print("hitl_alerts --email: HITL_DIGEST_TO is unset — no digest recipient "
              "configured. Set it to the address that should receive the daily "
              "HITL approval digest.", file=sys.stderr)
        return 1

    try:
        state = _load_state()
        pending, expired, patches = load_queue()
    except (QueueReadError, AlertStateError) as exc:
        print(f"hitl_alerts --email: FAILED — {exc}", file=sys.stderr)
        return 1

    today = _now().date().isoformat()
    if not force and state.get("last_email_date") == today:
        print(f"hitl_alerts --email: already sent {today} — skipping (use --force to resend)")
        return 0

    if not pending and not expired and not patches and not force:
        print("hitl_alerts --email: nothing pending, recently expired, or awaiting patch "
              "review — no email today")
        return 0

    n_waiting = len(pending) + len(patches)
    subject = (f"[Order Samurai] {n_waiting} approval(s) waiting"
               if n_waiting else "[Order Samurai] HITL digest — queue clear")
    body = _digest_body(pending, expired, patches, load_backlog_summary(), load_fleet_probe())
    key = os.environ.get("RESEND_API_KEY", "")
    sent = _send_resend(subject, body, to, key) if key else _send_mail_app(subject, body, to)
    if sent:
        state["last_email_date"] = today
        try:
            _save_state(state)
        except AlertStateError as exc:
            print(
                f"hitl_alerts --email: message delivered but delivery-state persistence FAILED — {exc}",
                file=sys.stderr,
            )
            return 1
    print(f"hitl_alerts --email: {'sent' if sent else 'FAILED'} → {to} "
          f"({len(pending)} pending, {len(patches)} patches, {len(expired)} expired, via "
          f"{'resend' if key else 'Mail.app'})")
    return 0 if sent else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Surface the HITL approval queue to the human.")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--notify", action="store_true")
    mode.add_argument("--email", action="store_true")
    ap.add_argument("--force", action="store_true", help="email mode: ignore the once-per-day guard")
    args = ap.parse_args()
    return do_notify() if args.notify else do_email(args.force)


if __name__ == "__main__":
    sys.exit(main())
