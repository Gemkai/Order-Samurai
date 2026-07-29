#!/usr/bin/env python3
"""hitl_alerts.py — surface the HITL approval queue to the human.

The maker-checker loop's weak seam (found 2026-07-19): Bushido routes queue-tier reflexes
to state/hitl_queue.json for human sign-off, but nothing showed the human the queue — items
expired unreviewed. This script is the delivery layer, two modes on one reader:

  --notify   Desktop banner via ~/.claude/scripts/notify.py (the cross-platform notifier from
             the 2026-07-19 nudge redesign). One AGGREGATED banner when the pending set changes,
             plus a 24h re-reminder while anything stays pending. Never one-banner-per-item.
             Scheduled: com.agentica.hitl-notifier (30 min).
  --email    Daily digest email (pending + recently-expired-unreviewed). Transport: Resend API
             when RESEND_API_KEY is set, else macOS Mail.app via osascript (values passed as
             argv — never string-interpolated into AppleScript). Once-per-day guard in state;
             --force overrides for testing. Scheduled: com.agentica.hitl-digest (08:00 daily).

Both modes are read-only over the queue; state lives in state/hitl_alert_state.json.
Morning-joe consumption is separate (build_payload.py hitl_section reads the queue directly).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")  # Windows cp1252 guard

_ROOT = Path(os.environ.get("ORDER_SAMURAI_ROOT", str(Path(__file__).resolve().parents[1])))
QUEUE_PATH = _ROOT / "state" / "hitl_queue.json"
STATE_PATH = _ROOT / "state" / "hitl_alert_state.json"
NOTIFY_PY = Path.home() / ".claude" / "scripts" / "notify.py"
DASHBOARD_URL = "http://127.0.0.1:4322/"

REMIND_HOURS = 24          # re-banner cadence while items stay pending
EXPIRED_WINDOW_DAYS = 7    # digest includes items that expired unreviewed this recently
SUBPROCESS_TIMEOUT = 30    # every external call gets a timeout (Release It! rule)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _age_str(item: dict) -> str:
    t = _parse_ts(item.get("enqueued_at"))
    if not t:
        return "?"
    days = (_now() - t).days
    return f"{days}d" if days < 14 else f"{days // 7}wk"


def load_queue() -> tuple[list[dict], list[dict]]:
    """Return (pending, recently_expired_unreviewed). Missing/torn file → ([], [])."""
    try:
        d = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], []
    items = d.get("items", []) if isinstance(d, dict) else d
    if not isinstance(items, list):
        return [], []
    pending = [i for i in items if isinstance(i, dict) and i.get("status") == "pending"]
    expired = []
    for i in items:
        if not (isinstance(i, dict) and i.get("status") == "expired"):
            continue
        t = _parse_ts(i.get("expired_at"))
        if t and (_now() - t).days <= EXPIRED_WINDOW_DAYS:
            expired.append(i)
    return pending, expired


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    try:
        STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass  # alerting must never crash on state persistence


def _item_line(i: dict) -> str:
    return (f"{i.get('command', '?')} — {i.get('pillar', '?')}/"
            f"{(i.get('metric_id') or '').split(':')[-1]} · waiting {_age_str(i)}"
            f"{' · blast: ' + i['blast_radius'] if i.get('blast_radius') else ''}")


# ── notify mode ──────────────────────────────────────────────────────────────

def do_notify() -> int:
    pending, _ = load_queue()
    state = _load_state()
    prev_ids = set(state.get("last_pending_ids", []))
    cur_ids = {str(i["id"]) for i in pending if i.get("id")}
    last_banner = _parse_ts(state.get("last_banner_at"))
    stale = last_banner is None or (_now() - last_banner).total_seconds() > REMIND_HOURS * 3600

    should = bool(pending) and (cur_ids != prev_ids or stale)
    if should:
        top = _item_line(pending[0])
        more = f" (+{len(pending) - 1} more)" if len(pending) > 1 else ""
        env = {**os.environ, "NUDGE_DESKTOP_NOTIFY": "true"}
        try:
            subprocess.run(
                [sys.executable, str(NOTIFY_PY),
                 f"Order Samurai: {len(pending)} approval(s) waiting",
                 f"{top}{more}",
                 "review at the dashboard HITL queue",
                 "--severity", "HIGH"],
                env=env, timeout=SUBPROCESS_TIMEOUT, capture_output=True,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"hitl_alerts: notify dispatch failed: {exc}", file=sys.stderr)
        state["last_banner_at"] = _now().isoformat()
    state["last_pending_ids"] = sorted(cur_ids)
    _save_state(state)
    print(f"hitl_alerts --notify: {len(pending)} pending; banner {'sent' if should else 'suppressed'}")
    return 0


# ── email mode ───────────────────────────────────────────────────────────────

def _digest_body(pending: list[dict], expired: list[dict]) -> str:
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
    if expired:
        lines.append("")
        lines.append(f"EXPIRED UNREVIEWED in the last {EXPIRED_WINDOW_DAYS}d ({len(expired)}):")
        for i in expired:
            lines.append(f"  • {i.get('command', '?')} ({i.get('pillar', '?')}) — "
                         f"expired {str(i.get('expired_at', ''))[:10]}")
    lines += ["", f"Review: {DASHBOARD_URL}", "— hitl_alerts.py (daily digest)"]
    return "\n".join(lines)


_MAIL_APPLESCRIPT = '''on run argv
  set theSubject to item 1 of argv
  set theBody to item 2 of argv
  set theTo to item 3 of argv
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
            return 200 <= r.status < 300
    except OSError as exc:
        print(f"hitl_alerts: resend failed: {exc}", file=sys.stderr)
        return False


def _send_mail_app(subject: str, body: str, to: str) -> bool:
    try:
        out = subprocess.run(
            ["osascript", "-", subject, body, to],
            input=_MAIL_APPLESCRIPT, text=True,
            timeout=SUBPROCESS_TIMEOUT, capture_output=True,
        )
        if out.returncode != 0:
            print(f"hitl_alerts: Mail.app send failed: {out.stderr.strip()[:200]}", file=sys.stderr)
        return out.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"hitl_alerts: Mail.app send failed: {exc}", file=sys.stderr)
        return False


def do_email(force: bool) -> int:
    state = _load_state()
    today = _now().date().isoformat()
    if not force and state.get("last_email_date") == today:
        print(f"hitl_alerts --email: already sent {today} — skipping (use --force to resend)")
        return 0

    pending, expired = load_queue()
    if not pending and not expired and not force:
        print("hitl_alerts --email: nothing pending or recently expired — no email today")
        state["last_email_date"] = today
        _save_state(state)
        return 0

    to = os.environ.get("HITL_DIGEST_TO", "user@example.com")
    subject = (f"[Order Samurai] {len(pending)} approval(s) waiting"
               if pending else "[Order Samurai] HITL digest — queue clear")
    body = _digest_body(pending, expired)
    key = os.environ.get("RESEND_API_KEY", "")
    sent = _send_resend(subject, body, to, key) if key else _send_mail_app(subject, body, to)
    if sent:
        state["last_email_date"] = today
        _save_state(state)
    print(f"hitl_alerts --email: {'sent' if sent else 'FAILED'} → {to} "
          f"({len(pending)} pending, {len(expired)} expired, via "
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
