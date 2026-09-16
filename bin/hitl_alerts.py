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

2026-09-02 escalation that resolves or retires (coverage review R4.1 / R4.2 — 12 HITL items
expired unreviewed and vanished; the same eight launchd jobs were named in the banner every
day for weeks with no escalation tier):
  • Expiry is a STATE, not an end. Every `status: expired` item is listed in the digest under
    EXPIRED WITHOUT DECISION, with its age, until a human writes a decision with
    bin/bushido_check.py (--retire / --reject / --approve). The old 7-day window is gone.
  • Fleet-banner tiers. --notify records the first time each failing job was seen
    (state: fleet_first_seen). Day >= FLEET_DIGEST_DAY: the digest subject names the job.
    Day >= FLEET_HITL_DAY: one HITL item per job ("auto-disable <job>?", source `fleet`,
    empty command, backlog_id `fleet-disable:<job>`, expires_at +FLEET_HITL_EXPIRY_DAYS,
    on_expire disable_launchd_job), written through bushido_engine.enqueue_hitl so it is
    locked, deduped and chained like every other item.
  • Sweep (every --notify): an approved fleet item disables the job now; a pending one past
    expires_at is disabled and retired with the reason recorded (the loop-worthiness
    doctrine: retirement is the default outcome of silence). Disable = `launchctl disable`
    (survives login) + `launchctl bootout`, finalized only against an observed
    disabled+unloaded state; a failure either restores the job or journals
    `rollback_failed`, which forces a banner and a nonzero --notify. Each retirement is
    appended to
    state/fleet_retirements.json with its exact revert command and shown in the digest under
    AUTO-DISABLED. Kill switch: HITL_FLEET_AUTO_DISABLE=false makes an unanswered item
    plain `expired` (so it stays in EXPIRED WITHOUT DECISION) and never touches launchctl.
  The queue is still read-only for THIS file; every write goes through bushido_engine's
  locked writers (enqueue_hitl / review_hitl / mark_complete).
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
from typing import Callable, NamedTuple

sys.stdout.reconfigure(encoding="utf-8")  # Windows cp1252 guard

_ROOT = Path(os.environ.get("ORDER_SAMURAI_ROOT", str(Path(__file__).resolve().parents[1])))
QUEUE_PATH = _ROOT / "state" / "hitl_queue.json"
STATE_PATH = _ROOT / "state" / "hitl_alert_state.json"
PATCH_DIR = _ROOT / "state"                       # reflex-engine writes pending patches here
PATCH_GLOB = "pending_remediation_*.patch"        # must match reflex-engine.ts patchIdSlug naming
BACKLOG_PATH = _ROOT / "state" / "PROPOSED_BACKLOG.json"


def _governance_dir(root: Path) -> Path:
    """The directory holding agentica_core/, config/ and data/ beside this pack.

    Live tree:      <repo>/Governance/Order Samurai -> root.parent (Governance/)
    Public export:  <root>                          -> root itself: the pack is
                    flattened and the allow-listed Governance files land beside it.
    A fixed `root.parent` described only the first; in the export it resolved to a
    directory OUTSIDE the distribution, so the exported escalation suite could not
    even collect (2026-09-06). Marker, not depth -- the same rule emit_event.py
    applies to its agentica_core import.
    """
    parent = root.parent
    if parent.name == "Governance" and (parent / "agentica_core").is_dir():
        return parent
    return root


_GOVERNANCE = _governance_dir(_ROOT)
FLEET_PROBE_PATH = _GOVERNANCE / "data" / "fleet_probe.json"    # written by bin/fleet_probe.py
DOCTOR_STATE_PATH = _ROOT / "state" / "doctor_last.json"          # written by execution/doctor.py --write-state
RETIREMENTS_PATH = _ROOT / "state" / "fleet_retirements.json"     # owned by this file (R4.2 sweep)
OPERATOR_REGISTRY_PATH = _GOVERNANCE / "config" / "operator_registry.json"
NOTIFY_PY = Path.home() / ".claude" / "scripts" / "notify.py"
DASHBOARD_URL = "http://127.0.0.1:4322/"

REMIND_HOURS = 24          # re-banner cadence while items stay pending
EMAIL_LAG_DAYS = 1         # digest is daily; further behind than this with items pending = lagging
FLEET_DIGEST_DAY = 3       # R4.2 tier 2: a job failing this many days is named in the digest subject
FLEET_HITL_DAY = 7         # R4.2 tier 3: a job failing this many days gets an "auto-disable?" HITL item
FLEET_HITL_EXPIRY_DAYS = 7 # ... which, unanswered for this long, disables the job and records why
FLEET_REDECIDE_DAYS = 14   # a rejected/retired fleet item is not re-raised for this long
RETIREMENTS_DIGEST_DAYS = 7  # AUTO-DISABLED digest section shows retirements this recent
FLEET_PROBE_STALE_HOURS = 3   # _refresh_fleet_probe rides the 30-min notify cadence (~6 cycles);
                               # older than this is a dead prober, not a single hiccup
DOCTOR_STALE_HOURS = 36    # com.agentica.os-doctor runs daily at 06:45; 36h clears one missed
                           # run to sleep without letting a dead scheduler read as healthy
SUBPROCESS_TIMEOUT = 30    # every external call gets a timeout (Release It! rule)
MAIL_TIMEOUT = 90          # Mail.app path may cold-start Mail (launch + delay) before sending


def _flag(name: str) -> bool:
    """Env kill-switch, default ON (the fixed behavior); only the literal 'false' disables.
    Mirrors the reflex-engine pattern (REFLEX_VERIFY_GATE et al.); read at call time so a
    scheduled run picks up an env change without a module reload."""
    return os.environ.get(name, "true").strip().lower() != "false"


def _resend_key() -> str:
    """RESEND_API_KEY from env, else the macOS Keychain via Governance/bin/secret_env.

    launchd jobs never source ~/.zshrc, so the scheduled digest ran keyless and fell
    back to Mail.app (2026-08-16 secret-brokerage M1). Env still wins so tests and
    one-off overrides work; empty string preserves the Mail.app fallback."""
    key = os.environ.get("RESEND_API_KEY", "")
    if key:
        return key
    gov_bin = str(_ROOT.parent / "bin")
    if gov_bin not in sys.path:
        sys.path.insert(0, gov_bin)
    try:
        from secret_env import lookup
        return lookup("RESEND_API_KEY") or ""
    except Exception:
        return ""


class QueueReadError(RuntimeError):
    """The approval queue could not be read or did not satisfy its schema."""


class AlertStateError(RuntimeError):
    """Alert delivery state could not be read or durably persisted."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(s: str | None) -> datetime | None:
    """Parse an ISO timestamp, grading a tz-naive value as UTC.

    Every caller compares the result against _now(), which is aware, so a naive value
    used to raise "can't subtract offset-naive and offset-aware datetimes" straight out
    of whichever reader touched it. Grading naive as UTC is the same rule doctor
    applies to its own hand-maintained stamps.
    """
    if not s or not isinstance(s, str):
        return None
    try:
        parsed = datetime.fromisoformat(s)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


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


def load_doctor_last() -> dict | None:
    """Read-only view of state/doctor_last.json: the last doctor run's counts + FAIL rows.

    Written by execution/doctor.py --write-state on the com.agentica.os-doctor daily
    job. Same discipline as load_fleet_probe(): missing file -> None ("doctor has not
    run", which doctor_alert_lines turns into its own WARN, NOT silence);
    unreadable/malformed -> {'error': ...} so the banner and digest can say so rather
    than equating a broken file with a clean bill of health.

    That distinction is the entire point of audit finding B2 — doctor's FAIL=1 was
    invisible from 2026-08-23 because nothing read it, and a reader that treats
    "no data" as "no problem" reopens exactly that gap.
    """
    if not _flag("HITL_DOCTOR_ALARM"):
        return None
    try:
        raw = DOCTOR_STATE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"missing": True}
    except OSError as exc:
        return {"error": str(exc)}
    try:
        d = json.loads(raw)
    except ValueError as exc:
        return {"error": f"invalid JSON: {exc}"}
    if not isinstance(d, dict):
        return {"error": "unexpected schema (not an object)"}
    fails = d.get("fails")
    counts = d.get("counts")
    if not isinstance(fails, list) or not isinstance(counts, dict):
        return {"error": "unexpected schema (missing counts/fails)"}
    # Validate the ROWS too, not just the container. `fails: [null]` satisfied the
    # container check and then raised AttributeError inside doctor_alert_lines, taking
    # down both alert modes — the exact opposite of the "malformed becomes a WARN"
    # promise. Reported as unreadable rather than silently dropped: a row we cannot
    # parse may be the FAIL that mattered.
    if not all(isinstance(row, dict) for row in fails):
        return {"error": "unexpected schema (a fails entry is not an object)"}
    # Spectator FAILs are persisted for the dashboard, but remain non-paging just as
    # they remain non-gating. Historical rows have no flag and are gating by default.
    paging_fails = [row for row in fails if row.get("gating") is not False]
    return {"counts": counts, "fails": paging_fails, "generated_at": d.get("generated_at")}


def doctor_alert_lines(doctor: dict | None) -> list[str]:
    """The lines a human must see about doctor, or [] when there is genuinely nothing.

    [] is returned ONLY for a fresh file with zero FAIL rows — every other state,
    including "the file is not there" and "the file is older than DOCTOR_STALE_HOURS",
    produces a line. A scheduler that dies is indistinguishable from a healthy system
    unless its absence is itself reported, which is the failure mode B2 documented.
    """
    if doctor is None:
        return []
    if doctor.get("missing"):
        return [f"DOCTOR: has never written {DOCTOR_STATE_PATH.name} — the daily "
                f"com.agentica.os-doctor job has not produced a result "
                f"(no health signal at all, not a clean one)"]
    if "error" in doctor:
        return [f"DOCTOR: {DOCTOR_STATE_PATH.name} unreadable — {doctor['error']}"]

    generated_at = _parse_ts(doctor.get("generated_at"))
    fails = doctor.get("fails") or []
    if generated_at is None:
        return [f"DOCTOR: last result carries no readable generated_at — cannot tell "
                f"whether its {len(fails)} FAIL row(s) are current"]
    age_hours = (_now() - generated_at).total_seconds() / 3600.0
    if age_hours > DOCTOR_STALE_HOURS:
        return [f"DOCTOR: has not run since {generated_at.isoformat()} "
                f"({age_hours:.0f}h ago, stale past {DOCTOR_STALE_HOURS}h) — last known "
                f"{len(fails)} FAIL row(s); check the com.agentica.os-doctor job"]
    return [f"DOCTOR FAIL: {f.get('label', '?')} — {f.get('detail', '')}" for f in fails]


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


def _queue_items() -> list[dict]:
    """Every row of the queue, or QueueReadError. Shared by load_queue and the fleet sweep."""
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
    return [i for i in items if isinstance(i, dict)]


def load_queue() -> tuple[list[dict], list[dict], list[dict]]:
    """Return (pending, expired-without-decision, pending patches); never equate unreadable
    with empty.

    A `source: 'reflex_patch'` pending item and the on-disk patch it names are the SAME
    event surfaced two ways (see load_pending_patches' docstring) — counting both would
    double the banner/digest total for one artifact. Drop the queue-item copy whenever its
    named patch is still on disk; a reflex_patch item whose patch is already gone (archived
    by review_pending_patch.py, or the context didn't parse) stays visible on purpose —
    under-surfacing a genuinely-unresolved approval is worse than a rare stale duplicate.

    `expired` is EVERY `status: expired` row, oldest first, however old (R4.1). The former
    7-day window made expiry a silent end: twelve reflex items expired unreviewed in
    June–July 2026 and dropped out of the digest a week later with nobody having decided
    anything. An expired item now leaves this list only through a recorded decision —
    `bin/hitl_review.py retire <id>` (status `retired`).
    """
    items = _queue_items()
    patches = load_pending_patches()
    patch_names_on_disk = {p["name"] for p in patches}
    pending = [
        i for i in items
        if i.get("status") == "pending"
        and _reflex_patch_filename(i) not in patch_names_on_disk
    ]
    expired = sorted(
        (i for i in items if i.get("status") == "expired"),
        key=lambda i: str(i.get("expired_at") or ""),
    )
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
    unchanged — never once per 30-min poll.

    Also banners, on its own FLEET_PROBE_STALE_HOURS/REMIND_HOURS cadence, when the probe
    DATA itself has gone stale (generated_at older than FLEET_PROBE_STALE_HOURS). Without
    this, a prober that crashes after one successful write keeps serving its last-known —
    possibly clean — snapshot forever: every failing/unreachable set below would then be
    read as current when it is actually frozen, silently reopening the exact 2026-08-09
    class of incident this mechanism exists to close. This check runs even when the
    failing/unreachable set is currently empty, since a dead prober can freeze on a
    healthy-looking snapshot too.

    Mutates state (last_fleet_signature, last_fleet_banner_at, last_fleet_stale_banner_at)
    only on an acknowledged dispatch; the caller persists it. Missing/unreadable probe data
    is silently skipped, not alarmed on — a probe that hasn't run yet or glitched once is
    not itself a fleet failure. Returns True if either banner dispatched, so do_notify()'s
    own summary line can report it accurately."""
    if not _flag("HITL_FLEET_ALARM"):
        return False
    probe = load_fleet_probe()
    if probe is None or "error" in probe:
        return False
    dispatched_any = False

    generated_at = _parse_ts(probe.get("generated_at"))
    if generated_at is not None:
        probe_age_hours = (_now() - generated_at).total_seconds() / 3600.0
        if probe_age_hours > FLEET_PROBE_STALE_HOURS:
            last_stale_banner = _parse_ts(state.get("last_fleet_stale_banner_at"))
            stale_reminder_due = (
                last_stale_banner is None
                or (_now() - last_stale_banner).total_seconds() > REMIND_HOURS * 3600
            )
            if stale_reminder_due and _dispatch_banner(
                "Order Samurai: fleet probe is STALE",
                f"fleet_probe.json last wrote {probe_age_hours:.1f}h ago — the "
                f"failing/unreachable set below (if any) may no longer reflect reality",
                "run bin/fleet_probe.py by hand, then check its launchd/cron carrier",
            ):
                state["last_fleet_stale_banner_at"] = _now().isoformat()
                dispatched_any = True

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
        return dispatched_any
    last_banner = _parse_ts(state.get("last_fleet_banner_at"))
    stale = last_banner is None or (_now() - last_banner).total_seconds() > REMIND_HOURS * 3600
    if signature == prev_signature and not stale:
        return dispatched_any
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
        dispatched_any = True
    return dispatched_any


# ── fleet escalation tiers (R4.2) ───────────────────────────────────────────────

FLEET_BACKLOG_PREFIX = "fleet-disable:"

#: Jobs silence must never disable: the alert carriers themselves, doctor, and the
#: dashboards that render the result. If hitl-notifier were auto-disabled for failing,
#: the sweep that disabled it would be the last thing anyone heard. A human can still
#: approve disabling one of these explicitly; an unanswered item on them just expires.
def _auto_disable_block(job: str) -> str | None:
    """Why silence may not auto-disable ``job``, or ``None`` when it may.

    ``auto_disable: false`` in the operator registry is the canonical carrier policy.
    Registry failure is fail-closed: losing the safety policy must never grant authority
    to stop an unattended job. Human approval still bypasses this guard.
    """
    try:
        data = json.loads(OPERATOR_REGISTRY_PATH.read_text(encoding="utf-8"))
        automations = data.get("automations") if isinstance(data, dict) else None
        if not isinstance(automations, list):
            raise ValueError("automations is not a list")
    except (OSError, ValueError) as exc:
        return f"operator registry unavailable ({type(exc).__name__}); auto-disable fails closed"
    label = _launchd_label(job)
    entry = next((row for row in automations
                  if isinstance(row, dict) and row.get("id") == label), None)
    if entry is not None and "auto_disable" in entry and not isinstance(entry["auto_disable"], bool):
        return "operator registry has invalid auto_disable policy; auto-disable fails closed"
    if entry is not None and entry.get("auto_disable") is False:
        return "operator registry marks it auto_disable:false"
    return None


def _launchd_label(job: str) -> str:
    """fleet_probe.json names jobs without the `com.` prefix (mechanism_audit strips it);
    launchctl wants the full label."""
    return job if job.startswith("com.") else f"com.{job}"


def track_fleet_first_seen(state: dict, failing: list[str], now: datetime | None = None) -> dict[str, int]:
    """Update state['fleet_first_seen'] from the current failing set and return
    {job: days_failing}. A job that recovered is forgotten, so a later recurrence starts
    its tiers from day 1 again (same rule as the banner signature)."""
    now = now or _now()
    seen = state.get("fleet_first_seen")
    if not isinstance(seen, dict):
        seen = {}
    current = set(failing)
    seen = {job: ts for job, ts in seen.items() if job in current}
    for job in current:
        seen.setdefault(job, now.isoformat())
    state["fleet_first_seen"] = seen
    days: dict[str, int] = {}
    for job, ts in seen.items():
        t = _parse_ts(ts)
        days[job] = (now - t).days if t else 0
    return days


def fleet_days_from_state(state: dict, failing: list[str], now: datetime | None = None) -> dict[str, int]:
    """{job: days_failing} READ from state['fleet_first_seen'] without advancing it — the
    digest and the dashboard read tiers; only --notify writes them. A failing job the
    notifier has not yet recorded reads as day 0."""
    now = now or _now()
    seen = state.get("fleet_first_seen")
    if not isinstance(seen, dict):
        seen = {}
    days: dict[str, int] = {}
    for job in failing:
        t = _parse_ts(seen.get(job))
        days[job] = (now - t).days if t else 0
    return days


def fleet_tiers(days: dict[str, int]) -> dict[str, dict]:
    """{job: {days, tier}} with tier in banner | digest | hitl by the R4.2 thresholds."""
    out: dict[str, dict] = {}
    for job, d in sorted(days.items()):
        tier = "hitl" if d >= FLEET_HITL_DAY else "digest" if d >= FLEET_DIGEST_DAY else "banner"
        out[job] = {"days": d, "tier": tier}
    return out


def fleet_subject_clause(tiers: dict[str, dict]) -> str:
    """The digest-subject fragment for jobs at the digest tier or above, or ''."""
    named = [(j, t["days"]) for j, t in tiers.items() if t["tier"] != "banner"]
    if not named:
        return ""
    named.sort(key=lambda jd: (-jd[1], jd[0]))
    job, d = named[0]
    more = f" (+{len(named) - 1} more)" if len(named) > 1 else ""
    return f"fleet: {job} failing {d}d{more}"


def _bushido():
    """agentica_core.bushido_engine, imported lazily: the queue's only writer. Kept out of
    module import so a broken agentica_core cannot take the banner down with it. Located
    from THIS file, not from _ROOT: ORDER_SAMURAI_ROOT relocates the state, never the code."""
    gov = str(Path(__file__).resolve().parents[2])
    if gov not in sys.path:
        sys.path.insert(0, gov)
    from agentica_core import bushido_engine
    return bushido_engine


def _bushido_root() -> Path:
    """The root bushido_engine keys its lock and files off (parent of state/)."""
    return QUEUE_PATH.parent.parent


def _fleet_item_job(item: dict) -> str | None:
    bid = str(item.get("backlog_id") or "")
    return bid[len(FLEET_BACKLOG_PREFIX):] if bid.startswith(FLEET_BACKLOG_PREFIX) else None


def _recently_decided(item: dict, now: datetime) -> bool:
    """True when a rejected/retired/done fleet item was decided within FLEET_REDECIDE_DAYS —
    the human said no (or the job was already handled); do not ask again yet."""
    for key in ("rejected_at", "retired_at", "completed_at", "expired_at"):
        t = _parse_ts(item.get(key))
        if t and (now - t).days < FLEET_REDECIDE_DAYS:
            return True
    return False


def enqueue_fleet_hitl(tiers: dict[str, dict], items: list[dict], now: datetime | None = None) -> list[str]:
    """One "auto-disable <job>?" HITL item per job at the hitl tier. Returns the jobs
    enqueued THIS call (deduped against pending/approved/executing items and against a
    decision younger than FLEET_REDECIDE_DAYS)."""
    now = now or _now()
    existing: dict[str, list[dict]] = {}
    for it in items:
        job = _fleet_item_job(it)
        if job:
            existing.setdefault(job, []).append(it)
    be = _bushido()
    enqueued: list[str] = []
    for job, t in tiers.items():
        if t["tier"] != "hitl":
            continue
        rows = existing.get(job, [])
        if any(r.get("status") in ("pending", "approved", "executing", "dispatched") for r in rows):
            continue
        if any(_recently_decided(r, now) for r in rows):
            continue
        expires = now + __import__("datetime").timedelta(days=FLEET_HITL_EXPIRY_DAYS)
        be.enqueue_hitl(
            be.WorkItem(
                skill="launchd-retire", source="fleet", command="",
                blast_radius=be.BlastRadius.SYSTEM, reversible=True,
                pillar="bow", metric_id="metric:bow:Scheduled_Job_Failures",
                backlog_id=f"{FLEET_BACKLOG_PREFIX}{job}",
                context=(f"launchd job {_launchd_label(job)} has failed on every run for "
                         f"{t['days']} days. approve = disable it now (launchctl disable + bootout, "
                         f"revert command recorded); reject = keep it and stop asking for "
                         f"{FLEET_REDECIDE_DAYS}d; unanswered by {expires.date().isoformat()} = "
                         f"auto-disable (HITL_FLEET_AUTO_DISABLE). Retirement is the default "
                         f"outcome of silence (coverage review R4.2)."),
                expires_at=expires.isoformat(), on_expire="disable_launchd_job",
            ),
            be.Tier.HITL, _bushido_root(),
        )
        enqueued.append(job)
    return enqueued


#: `launchctl print` on a label launchd does not know. Every other nonzero is a
#: diagnostic failure (permissions, a dead domain) and proves nothing about the job.
LAUNCHCTL_NO_SUCH_LABEL = 113

DISABLED_UNLOADED = "disabled+unloaded"   # the retirement end state
ENABLED_LOADED = "enabled+loaded"         # the restored end state
UNMEASURED = "unmeasured"                 # unreadable, or stopped between the two


def _read_launchd_state(job: str) -> tuple[str, str]:
    """Observe the job: ``(state, detail)``. The single source of truth for both
    directions of the transaction; only a known end state may finalize it.
    ``UNMEASURED`` covers "launchctl would not answer" and "answered, half-applied"
    alike — neither is evidence that anything succeeded."""
    label = _launchd_label(job)
    uid = os.getuid()
    try:
        dis = subprocess.run(["launchctl", "print-disabled", f"gui/{uid}"],
                             capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT)
        pr = subprocess.run(["launchctl", "print", f"gui/{uid}/{label}"],
                            capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return UNMEASURED, f"launchctl unreadable: {exc}"
    if dis.returncode != 0:
        return UNMEASURED, f"print-disabled exited {dis.returncode}; disabled unknown"
    # A label absent from print-disabled was never disabled; only an explicit true is.
    disabled = bool(re.search(rf'"{re.escape(label)}"\s*=>\s*(?:true|disabled)\b',
                              dis.stdout or ""))
    if pr.returncode == 0:
        loaded = True
    elif pr.returncode == LAUNCHCTL_NO_SUCH_LABEL:
        loaded = False
    else:
        return UNMEASURED, (f"print exited {pr.returncode}, not {LAUNCHCTL_NO_SUCH_LABEL}; "
                            f"loaded unknown")
    if disabled and not loaded:
        return DISABLED_UNLOADED, DISABLED_UNLOADED
    if not disabled and loaded:
        return ENABLED_LOADED, ENABLED_LOADED
    return UNMEASURED, f"half-applied: disabled={disabled}, loaded={loaded}"


def _disable_launchd_job(job: str) -> tuple[bool, str, str | None]:
    """Bring the job to ``DISABLED_UNLOADED`` — `launchctl disable` (survives logins,
    the switch mechanism_audit's fleet check honours) then `bootout`. List args,
    timeouts, never shell.

    Idempotent: a job already at the end state is reported as success untouched, so a
    retry settles an interrupted transaction instead of restarting the service.

    Postcondition: on success launchd is observed at ``DISABLED_UNLOADED``; on failure
    at ``ENABLED_LOADED``, or ``rollback_state`` is ``"rollback_failed"`` and the
    machine is unreconciled. ``rollback_state`` is ``None`` when nothing changed."""
    label = _launchd_label(job)
    target = f"gui/{os.getuid()}/{label}"

    def undo(detail: str) -> tuple[bool, str, str]:
        restored, how = _restore_launchd_job(job)
        if restored:
            return False, f"{detail}; job restored ({how})", "rolled_back"
        return False, (f"{detail}; ROLLBACK INCOMPLETE ({how}) — revert by hand: "
                       f"{_revert_command(job)}"), "rollback_failed"

    state, seen = _read_launchd_state(job)
    if state == DISABLED_UNLOADED:
        return True, f"already {seen}", None
    try:
        dis = subprocess.run(["launchctl", "disable", target], capture_output=True,
                             text=True, timeout=SUBPROCESS_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        # A timed-out disable may still have landed, and it survives logins.
        return undo(f"launchctl disable timed out: {exc}")
    except OSError as exc:
        return False, f"launchctl disable failed: {exc}", None
    if dis.returncode != 0:
        return False, f"launchctl disable exited {dis.returncode}: {(dis.stderr or '').strip()[:200]}", None
    try:
        boot = subprocess.run(["launchctl", "bootout", target], capture_output=True,
                              text=True, timeout=SUBPROCESS_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return undo(f"launchctl bootout failed: {exc}")
    if boot.returncode != 0:
        return undo(f"launchctl bootout exited {boot.returncode}: "
                    f"{(boot.stderr or '').strip()[:200]}")
    state, seen = _read_launchd_state(job)
    if state != DISABLED_UNLOADED:
        return undo(f"post-disable state is {seen}")
    return True, "disabled + booted out (verified)", None


def _restore_launchd_job(job: str) -> tuple[bool, str]:
    """Put the job back to ``ENABLED_LOADED``. Success is the observed state, never the
    exit codes: `bootstrap` returns nonzero for an already-loaded job, and a zero exit
    proves nothing about what launchd did."""
    label = _launchd_label(job)
    domain = f"gui/{os.getuid()}"
    plist = str(Path.home() / "Library" / "LaunchAgents" / f"{label}.plist")
    try:
        en = subprocess.run(["launchctl", "enable", f"{domain}/{label}"], capture_output=True,
                            text=True, timeout=SUBPROCESS_TIMEOUT)
        bs = subprocess.run(["launchctl", "bootstrap", domain, plist], capture_output=True,
                            text=True, timeout=SUBPROCESS_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"restore failed: {exc}"
    state, seen = _read_launchd_state(job)
    if state == ENABLED_LOADED:
        return True, "re-enabled + bootstrapped (verified)"
    return False, f"restore unverified: {seen}; enable={en.returncode}, bootstrap={bs.returncode}"


def _revert_command(job: str) -> str:
    label = _launchd_label(job)
    uid = os.getuid()
    return (f"launchctl enable gui/{uid}/{label} && launchctl bootstrap gui/{uid} "
            f"~/Library/LaunchAgents/{label}.plist")


def _retirement_io():
    gov = str(Path(__file__).resolve().parents[2])
    if gov not in sys.path:
        sys.path.insert(0, gov)
    from agentica_core.atomic import atomic_json_write, file_write_lock
    return atomic_json_write, file_write_lock


def _read_retirement_rows() -> list[dict]:
    try:
        data = json.loads(RETIREMENTS_PATH.read_text(encoding="utf-8"))
        rows = data.get("items") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            rows = []
    except (OSError, ValueError):
        rows = []
    return [row for row in rows if isinstance(row, dict)]


def _record_retirement(job: str, queue_id: str | None, reason: str, detail: str,
                       state: str, rows: list[dict] | None = None) -> list[dict]:
    """Upsert one write-ahead journal row. Caller holds the retirement sidecar lock."""
    rows = _read_retirement_rows() if rows is None else rows
    row = next((r for r in rows if r.get("queue_id") == queue_id and r.get("job") == job), None)
    if row is None:
        row = {"job": job, "label": _launchd_label(job), "queue_id": queue_id,
               "started_at": _now().isoformat(), "revert": _revert_command(job)}
        rows.append(row)
    row.update({"state": state, "reason": reason, "detail": detail,
                "updated_at": _now().isoformat()})
    if state == "disabled":
        row["disabled_at"] = _now().isoformat()
    RETIREMENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_json_write, _ = _retirement_io()
    atomic_json_write(RETIREMENTS_PATH, {"items": rows})
    return rows


def _try_record(job: str, queue_id: str | None, reason: str, detail: str,
                state: str, rows: list[dict]) -> bool:
    """Journal write that must not abort a transaction which already changed launchd.
    A raise here (full disk, permissions, a failed atomic replace) would otherwise
    escape past the compensation and strand the machine."""
    try:
        _record_retirement(job, queue_id, reason, detail, state, rows)
        return True
    except Exception as exc:  # noqa: BLE001 — the machine matters more than the journal
        print(f"hitl_alerts: retirement journal write failed for {job} "
              f"({type(exc).__name__}: {exc})", file=sys.stderr)
        return False


def _nondurable_incident(job: str, detail: str, recorded: bool) -> dict | None:
    """A rollback failure the journal could not accept. Recorded ones are read back
    from the journal; only these have to be carried out of the call that saw them."""
    if recorded:
        return None
    return {"job": job, "label": _launchd_label(job), "state": "rollback_failed",
            "detail": detail, "revert": _revert_command(job),
            "updated_at": _now().isoformat(), "durable": False}


def _unwind(job: str, queue_id: str, reason: str, why: str,
            rows: list[dict]) -> tuple[str, dict | None]:
    """Put launchd back after a disable that cannot be finalized, and journal which
    reality resulted. Returns the operator line and, when the job is left possibly
    disabled AND the journal write failed, the incident the caller must carry."""
    restored, how = _restore_launchd_job(job)
    detail = f"{why}; {how}"
    recorded = _try_record(job, queue_id, reason, detail,
                           "rolled_back" if restored else "rollback_failed", rows)
    if restored:
        return f"{why}; job restored", None
    return (f"{why}; ROLLBACK INCOMPLETE ({how}) — revert by hand: {_revert_command(job)}",
            _nondurable_incident(job, detail, recorded))


def _retire_job_transaction(job: str, queue_id: str, reason: str,
                            decide: Callable[[], bool]) -> tuple[bool, str, dict | None]:
    """Journal the intent, reach ``DISABLED_UNLOADED``, persist that, then settle the
    queue.

    Postcondition: either the queue is settled and the journal says ``disabled``, or
    launchd is observed back at ``ENABLED_LOADED``, or the job is reported unreconciled
    — durably in the journal, or through the returned incident when that write itself
    failed. Nothing after the disable may raise past the compensation, so the journal
    writes and ``decide`` are both inside boundaries; a raising ledger is a refused
    decision. The queue settles only once the ``disabled`` row is durable, since an
    unrecorded disable is indistinguishable from one that never happened."""
    _, file_write_lock = _retirement_io()
    with file_write_lock(RETIREMENTS_PATH):
        rows = _read_retirement_rows()
        _record_retirement(job, queue_id, reason, "disable requested", "intent", rows)
        ok, detail, rollback_state = _disable_launchd_job(job)
        if not ok:
            recorded = _try_record(job, queue_id, reason, detail,
                                   rollback_state or "failed", rows)
            incident = (_nondurable_incident(job, detail, recorded)
                        if rollback_state == "rollback_failed" else None)
            return False, f"{detail} — will retry", incident
        if not _try_record(job, queue_id, reason, detail, "disabled", rows):
            line, incident = _unwind(job, queue_id, reason,
                                     "disabled but the journal write failed", rows)
            return False, line, incident
        try:
            settled = decide()
        except Exception as exc:  # noqa: BLE001 — a raising ledger is a refused decision
            line, incident = _unwind(job, queue_id, reason,
                                     f"queue decision raised {type(exc).__name__}: {exc}", rows)
            return False, line, incident
        if settled:
            return True, detail, None
        line, incident = _unwind(job, queue_id, reason,
                                 "queue decision could not be recorded", rows)
        return False, line, incident


def load_unreconciled_retirements() -> list[dict]:
    """Durable rows whose compensation never verified: launchd may still be disabled
    with no decision behind it. Deliberately un-aged, unlike :func:`load_retirements` —
    a job nobody chose to stop stays visible until a later sweep upserts the row to a
    reconciled state, because time passing is not resolution."""
    return [r for r in _read_retirement_rows() if r.get("state") == "rollback_failed"]


def merge_unreconciled(durable: list[dict], incidents: list[dict]) -> list[dict]:
    """Durable unresolved rows plus incidents from THIS invocation whose journal write
    did not land, deduped by label. Only the sweep that produced the incidents may pass
    them; every other consumer reads the durable rows alone, so a retry that reconciles
    the journal is never shadowed by an alert nothing can clear."""
    seen = {r.get("label") for r in durable}
    return durable + [i for i in incidents if i.get("label") not in seen]


def load_retirements(days: int = RETIREMENTS_DIGEST_DAYS) -> list[dict] | None:
    """Retirements this recent, oldest first; None when the file has never been written."""
    try:
        data = json.loads(RETIREMENTS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return [{"error": "fleet_retirements.json unreadable"}]
    rows = data.get("items") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return [{"error": "fleet_retirements.json has no items list"}]
    recent = []
    for r in rows:
        if isinstance(r, dict) and r.get("state") not in (None, "disabled"):
            continue
        t = _parse_ts(r.get("disabled_at")) if isinstance(r, dict) else None
        if t and (_now() - t).days <= days:
            recent.append(r)
    return recent


def sweep_fleet_hitl(items: list[dict], now: datetime | None = None,
                     incidents: list[dict] | None = None) -> list[str]:
    """Honour decisions on fleet items. Returns human-readable lines for the operator.

      approved            -> disable now, mark_complete (done), record.
      pending, past       -> auto-disable on: disable, retire with the reason, record.
        expires_at           auto-disable off: expire (stays in EXPIRED WITHOUT DECISION).
    Each attempt is journaled before launchd is touched, and the queue settles only
    against an observed disabled+unloaded state. Anything else — a failed bootout,
    an unreadable state, a refused queue write — restores the job or leaves a durable
    `rollback_failed` row saying it could not. `incidents`, when given, collects the
    rollback failures whose journal row could not be written — the caller's own list,
    so the signal never outlives the invocation that observed it."""
    now = now or _now()
    lines: list[str] = []
    be = None
    for it in items:
        job = _fleet_item_job(it)
        if not job or it.get("source") != "fleet":
            continue
        status = it.get("status")
        qid = str(it.get("id") or "")
        if status == "approved":
            be = be or _bushido()
            ok, detail, incident = _retire_job_transaction(
                job, qid, "human approved disable",
                lambda be=be, qid=qid: be.mark_complete(qid, _bushido_root()),
            )
            if incident is not None and incidents is not None:
                incidents.append(incident)
            if not ok:
                lines.append(f"FLEET: {job} approved for disable but {detail}")
                continue
            lines.append(f"FLEET: {job} disabled on approval ({detail}); revert: {_revert_command(job)}")
        elif status == "pending":
            exp = _parse_ts(it.get("expires_at"))
            if exp is None or exp > now:
                continue
            be = be or _bushido()
            blocked = _auto_disable_block(job)
            if not _flag("HITL_FLEET_AUTO_DISABLE") or blocked:
                why = ("auto-disable is off (HITL_FLEET_AUTO_DISABLE=false)"
                       if _flag("HITL_FLEET_AUTO_DISABLE") is False
                       else blocked)
                be.review_hitl(qid, _bushido_root(), "expire",
                               reason=f"unanswered for {FLEET_HITL_EXPIRY_DAYS}d; {why} so {job} "
                                      f"keeps running — decide with bushido_check.py --retire/--reject")
                lines.append(f"FLEET: {job} disable request expired unanswered; not auto-disabled ({why})")
                continue
            reason = (f"unanswered for {FLEET_HITL_EXPIRY_DAYS}d after failing "
                      f"every run — auto-disabled per coverage review R4.2; revert: {_revert_command(job)}")
            ok, detail, incident = _retire_job_transaction(
                job, qid, "unanswered 7d — auto-disabled",
                lambda be=be, qid=qid, reason=reason: be.review_hitl(
                    qid, _bushido_root(), "retire", reason=reason),
            )
            if incident is not None and incidents is not None:
                incidents.append(incident)
            if ok:
                lines.append(f"FLEET: {job} auto-disabled after silence ({detail}); revert: {_revert_command(job)}")
            else:
                lines.append(f"FLEET: {job} unanswered past expiry but {detail}")
    return lines


def _maybe_fleet_escalations(state: dict, incidents: list[dict] | None = None) -> list[str]:
    """Tiers 2 and 3 for --notify: track first-seen, enqueue day-7 items, run the sweep.
    Returns operator lines and fills `incidents` for the caller. Never raises — a broken
    queue or probe reports and moves on."""
    if not _flag("HITL_FLEET_ALARM"):
        return []
    lines: list[str] = []
    probe = load_fleet_probe()
    failing: list[str] | None = None
    if probe and "error" not in probe:
        gen = _parse_ts(probe.get("generated_at"))
        fresh = gen is not None and (_now() - gen).total_seconds() / 3600.0 <= FLEET_PROBE_STALE_HOURS
        if fresh:
            failing = sorted(probe.get("failing_jobs") or [])
    try:
        items = _queue_items()
    except QueueReadError as exc:
        return [f"FLEET: escalation skipped — queue unreadable ({exc})"]
    if failing is not None:
        tiers = fleet_tiers(track_fleet_first_seen(state, failing))
        try:
            for job in enqueue_fleet_hitl(tiers, items):
                lines.append(f"FLEET: {job} failing {tiers[job]['days']}d — HITL item raised "
                             f"(auto-disable in {FLEET_HITL_EXPIRY_DAYS}d if unanswered)")
        except Exception as exc:  # noqa: BLE001 — report, never take the banner down
            lines.append(f"FLEET: could not raise HITL item(s): {type(exc).__name__}: {exc}")
        if lines:
            try:
                items = _queue_items()
            except QueueReadError as exc:
                # Same "never raises" contract as the read above: a transient failure
                # here (disk hiccup, concurrent writer caught mid-write) must not
                # propagate uncaught out of do_notify() — that would abort the whole
                # --notify cycle before _save_state() runs and drop this run's
                # bookkeeping entirely.
                lines.append(f"FLEET: could not re-read queue after enqueue — queue "
                             f"unreadable ({exc})")
                return lines
    else:
        lines.append("FLEET: probe stale or unreadable — tiers not advanced this cycle")
    try:
        lines += sweep_fleet_hitl(items, incidents=incidents)
    except Exception as exc:  # noqa: BLE001
        lines.append(f"FLEET: sweep failed: {type(exc).__name__}: {exc}")
    return lines


#: Desktop banners truncate, so the body is capped — but it names EVERY failing
#: check before it runs out of room, rather than the first one plus a count.
BANNER_BODY_CHARS = 400


def _banner_body(lines: list[str], limit: int = BANNER_BODY_CHARS) -> str:
    """Every line that fits, then an explicit count of what did not.

    The body used to be `lines[0][:200]` plus "(+N more)", so with two failing checks
    the second one's label never reached the human — it appeared only in the notifier's
    stdout. The acceptance criterion is that every FAIL is surfaced, and a banner that
    names one of two is how an operator learns the banner is not the whole story.
    """
    shown: list[str] = []
    used = 0
    for line in lines:
        cost = len(line) + (3 if shown else 0)
        if shown and used + cost > limit:
            break
        shown.append(line)
        used += cost
    body = " · ".join(shown)
    hidden = len(lines) - len(shown)
    return f"{body} (+{hidden} not shown)" if hidden else body


def doctor_alert_signature(doctor: dict | None) -> str:
    """A STABLE key for the condition the lines describe — "" when there is none.

    Deliberately not `"|".join(doctor_alert_lines(...))`. The stale line embeds a
    rounded age ("50h ago"), so a rendered-text signature changes every hour: the
    24h re-remind gate never engages and one dead scheduler dispatches a HIGH banner
    roughly hourly, forever. Measured before this fix: 14 polls over 7h produced 8
    banners. The condition here is "doctor last wrote at T and is stale", which does
    not change until doctor writes again.
    """
    if doctor is None:
        return ""
    if doctor.get("missing"):
        return "missing"
    if "error" in doctor:
        return f"error:{doctor['error']}"
    generated_at = _parse_ts(doctor.get("generated_at"))
    if generated_at is None:
        return "undated"
    if (_now() - generated_at).total_seconds() / 3600.0 > DOCTOR_STALE_HOURS:
        return f"stale:{generated_at.isoformat()}"
    return "|".join(str(f.get("label", "?")) for f in doctor.get("fails") or [])


def _maybe_doctor_banner(state: dict) -> list[str]:
    """Health alarm for execution/doctor.py. Returns the lines it reported (possibly
    empty); the caller prints them so a manual --notify run shows what the banner said.

    Banners on a CHANGE in the reported set, then re-reminds on the same 24h
    REMIND_HOURS cadence while it stays non-empty and unchanged — never once per
    30-minute poll. Unlike the fleet banner, a MISSING or STALE doctor result is
    itself reported rather than skipped: doctor's whole failure mode (audit B2) was
    a result nobody read, and "the job stopped running" is the same class of silence.

    Mutates state (last_doctor_signature, last_doctor_banner_at) only on an
    acknowledged dispatch; the caller persists it.
    """
    doctor = load_doctor_last()
    lines = doctor_alert_lines(doctor)
    signature = doctor_alert_signature(doctor)
    prev_signature = state.get("last_doctor_signature", "")
    if not signature:
        if prev_signature:
            # Doctor recovered. Clear the signature so an identical failure set
            # recurring later reads as a fresh incident, not an unchanged one.
            state["last_doctor_signature"] = ""
        return lines
    last_banner = _parse_ts(state.get("last_doctor_banner_at"))
    stale = last_banner is None or (_now() - last_banner).total_seconds() > REMIND_HOURS * 3600
    if signature == prev_signature and not stale:
        return lines
    if _dispatch_banner(
        f"Order Samurai: doctor reports {len(lines)} problem(s)",
        _banner_body(lines),
        "run execution/doctor.py, or check the com.agentica.os-doctor job",
    ):
        state["last_doctor_signature"] = signature
        state["last_doctor_banner_at"] = _now().isoformat()
    return lines


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
    # The two reads are separate on purpose. A broken approval queue used to return
    # here, before any doctor reader ran, so an unreadable hitl_queue.json silenced
    # doctor's FAIL in the 30-minute banner AND the daily digest — one unrelated
    # corrupt file switching off the whole "the error signal reaches a human"
    # mechanism. The queue failure is still reported and still sets the exit code;
    # it just no longer suppresses the other alarm.
    queue_error: Exception | None = None
    state_error: Exception | None = None
    pending: list[dict] = []
    patches: list[dict] = []
    state: dict = {}
    try:
        pending, _, patches = load_queue()
    except QueueReadError as exc:
        queue_error = exc
        print(f"hitl_alerts --notify: queue unreadable — {exc}", file=sys.stderr)
    try:
        state = _load_state()
    except AlertStateError as exc:
        state_error = exc
        print(f"hitl_alerts --notify: alert state unreadable — {exc}", file=sys.stderr)

    prev_ids = set(state.get("last_pending_ids", []))
    # Pending patches join the change set under their filename so a newly proposed patch
    # re-banners exactly like a newly enqueued approval.
    cur_ids = {str(i["id"]) for i in pending if i.get("id")} | {p["name"] for p in patches}
    last_banner = _parse_ts(state.get("last_banner_at"))
    stale = last_banner is None or (_now() - last_banner).total_seconds() > REMIND_HOURS * 3600
    n_waiting = len(pending) + len(patches)

    should = queue_error is None and n_waiting > 0 and (cur_ids != prev_ids or stale)
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
    incidents: list[dict] = []          # this invocation's unwritable rollback failures
    fleet_lines = _maybe_fleet_escalations(state, incidents)
    doctor_lines = _maybe_doctor_banner(state)

    save_error: AlertStateError | None = None
    if state_error is None:
        try:
            _save_state(state)
        except AlertStateError as exc:
            # Recorded, never returned on. Nothing below reads the alert state, and one
            # full disk breaks this write and strands a job at the same time — so this
            # failure must not gate the rollback warning.
            save_error = exc
            delivery = " after banner delivery" if (sent or fleet_sent) else ""
            print(f"hitl_alerts --notify: state persistence FAILED{delivery} — {exc}",
                  file=sys.stderr)
    fleet_note = " · fleet banner sent" if fleet_sent else ""
    queue_note = " · QUEUE UNREADABLE" if queue_error else ""
    print(f"hitl_alerts --notify: {n_waiting} pending; banner "
          f"{'sent' if sent else 'suppressed'}{fleet_note}{queue_note}")
    # Printed unconditionally, not only when a banner dispatched: the 24h re-remind
    # cadence deliberately suppresses repeat banners, and an operator running
    # --notify by hand must still be told what doctor currently reports.
    for line in doctor_lines:
        print(f"hitl_alerts --notify: {line}")
    for line in fleet_lines:
        print(f"hitl_alerts --notify: {line}")
    # A job left disabled by a failed rollback is never suppressed by the 24h banner
    # cadence and always fails the run: it is machine state nobody decided on.
    unreconciled = merge_unreconciled(load_unreconciled_retirements(), incidents)
    for r in unreconciled:
        print(f"hitl_alerts --notify: ROLLBACK INCOMPLETE — {r.get('label')} may still be "
              f"disabled; revert: {r.get('revert')}", file=sys.stderr)
    if unreconciled:
        _dispatch_banner(
            "Order Samurai: launchd rollback incomplete",
            _banner_body([f"{r.get('label')} may still be disabled — {r.get('revert')}"
                          for r in unreconciled]),
            f"{len(unreconciled)} job(s) need a manual revert")
    # Non-zero for the degraded read, but only AFTER doctor has had its say.
    return 1 if (queue_error or state_error or save_error or unreconciled) else 0


# ── email mode ───────────────────────────────────────────────────────────────

def _digest_body(pending: list[dict], expired: list[dict],
                 patches: list[dict] | None = None,
                 backlog: dict | None = None,
                 fleet: dict | None = None,
                 doctor: dict | None = None,
                 queue_error: Exception | None = None,
                 fleet_tiers_by_job: dict[str, dict] | None = None,
                 retirements: list[dict] | None = None,
                 unreconciled: list[dict] | None = None) -> str:
    lines = [f"Order Samurai — HITL approval digest ({_now().date().isoformat()})", ""]
    if queue_error:
        # Named FIRST: every count below is measured over an empty list, so the
        # reassuring "No approvals pending" must never be read as a clean queue.
        lines += [f"QUEUE UNREADABLE — {queue_error}",
                  "The approval counts below are NOT a reading of the queue.", ""]
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
        lines.append(f"EXPIRED WITHOUT DECISION ({len(expired)}) — stays here until you decide:")
        for i in expired:
            what = i.get("skill") or i.get("command") or "?"
            lines.append(f"  • {i.get('id', '?')} {what} ({i.get('source', '?')}/"
                         f"{i.get('pillar', '?')}) — expired {str(i.get('expired_at', ''))[:10]}, "
                         f"{_fmt_age(_parse_ts(i.get('expired_at')))} ago")
        lines.append("      decide: python3 bin/bushido_check.py --retire <id> --reason '…'  "
                     "(or --reject / --approve while still pending)")
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
                tiers = fleet_tiers_by_job or {}
                for j in failing:
                    t = tiers.get(j)
                    if t:
                        nxt = {"banner": f"digest subject at day {FLEET_DIGEST_DAY}",
                               "digest": f"auto-disable? HITL item at day {FLEET_HITL_DAY}",
                               "hitl": "HITL item raised — decide or it auto-disables"}[t["tier"]]
                        lines.append(f"  • launchd job failing: {j} — {t['days']}d, tier {t['tier']} ({nxt})")
                    else:
                        lines.append(f"  • launchd job failing: {j}")
                for s in unreachable:
                    lines.append(f"  • service unreachable: {s}")
    if retirements:
        lines.append("")
        if any("error" in r for r in retirements):
            lines.append(f"AUTO-DISABLED: {retirements[0].get('error')}")
        else:
            lines.append(f"AUTO-DISABLED in the last {RETIREMENTS_DIGEST_DAYS}d ({len(retirements)}):")
            for r in retirements:
                lines.append(f"  • {r.get('label')} — {str(r.get('disabled_at', ''))[:10]}: "
                             f"{r.get('reason')}")
                lines.append(f"      revert: {r.get('revert')}")
    # Above this line: jobs a decision retired. Below: jobs left stopped by a failed
    # rollback, which nobody decided and which the digest must not bury.
    if unreconciled:
        lines.append("")
        lines.append(f"ROLLBACK INCOMPLETE — MAY STILL BE DISABLED ({len(unreconciled)}):")
        for r in unreconciled:
            lines.append(f"  • {r.get('label')} — {r.get('detail')}")
            lines.append(f"      revert: {r.get('revert')}")
    if doctor is not None:
        lines.append("")
        problems = doctor_alert_lines(doctor)
        if problems:
            lines.append("DOCTOR:")
            for problem in problems:
                lines.append(f"  • {problem}")
        else:
            counts = doctor.get("counts") or {}
            lines.append(f"DOCTOR: no failing checks ✔ (OK={counts.get('OK', '?')} "
                         f"WARN={counts.get('WARN', '?')} FAIL=0)")
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
        headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json",
            # 2026-08-17: Python's default urllib User-Agent trips Cloudflare's bot
            # protection (error 1010) in front of api.resend.com — every call was
            # blocked before reaching Resend's own auth, masquerading as a generic
            # 403 regardless of key validity. A normal-looking UA clears it.
            "User-Agent": "OrderSamurai-HITL-Digest/1.0",
        },
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


class EmailDecision(NamedTuple):
    """Whether today's digest goes out, and what to tell the operator if not."""
    send: bool
    subject: str
    skip_message: str


def decide_email(*, force: bool, today: str, last_email_date: str | None,
                 pending: list, expired: list, patches: list,
                 doctor_problems: list[str] | None = None,
                 fleet_clause: str = "",
                 unreconciled: list | None = None) -> EmailDecision:
    """The send/skip decision and the subject line — no env, state, or network.

    Extracted from do_email (M7.4) so the skip rules are testable from fixtures.
    They exist to stop a scheduled job mailing noise: one digest per day, and
    nothing at all when there is nothing to report. `force` overrides both, which
    is what makes a manual re-send possible.

    `doctor_problems` counts as something to report. Without it the "empty queue =
    no email" rule silently swallows a doctor FAIL on every day the approval queue
    happens to be clear — which is precisely the silence audit finding B2 recorded,
    moved one layer down rather than fixed.
    """
    doctor_problems = doctor_problems or []
    unreconciled = unreconciled or []
    if not force and last_email_date == today:
        return EmailDecision(
            False, "",
            f"hitl_alerts --email: already sent {today} — skipping (use --force to resend)",
        )
    if (not pending and not expired and not patches and not doctor_problems
            and not fleet_clause and not unreconciled and not force):
        return EmailDecision(
            False, "",
            "hitl_alerts --email: nothing pending, expired without decision, awaiting patch "
            "review, failing in doctor, or failing in the fleet past day "
            f"{FLEET_DIGEST_DAY} — no email today",
        )
    n_waiting = len(pending) + len(patches)
    # A job left disabled with no decision behind it outranks every other subject: it is
    # machine state nobody chose, and the queue it belongs to may well look clear.
    if unreconciled:
        subject = (f"[Order Samurai] ROLLBACK INCOMPLETE — {len(unreconciled)} job(s) "
                   f"may still be disabled")
    elif n_waiting:
        subject = f"[Order Samurai] {n_waiting} approval(s) waiting"
    elif doctor_problems:
        subject = f"[Order Samurai] doctor: {len(doctor_problems)} problem(s)"
    elif fleet_clause:
        subject = "[Order Samurai]"
    else:
        subject = "[Order Samurai] HITL digest — queue clear"
    if fleet_clause:
        # R4.2 tier 2: day >= FLEET_DIGEST_DAY names the job in the subject line — the one
        # place a repeat reader cannot skim past.
        subject = f"{subject} · {fleet_clause}" if subject != "[Order Samurai]" else f"[Order Samurai] {fleet_clause}"
    return EmailDecision(True, subject, "")


def email_channel(api_key: str) -> str:
    """Which delivery channel an API key selects. Named once so the send call and
    the operator log line can never disagree about which one ran."""
    return "resend" if api_key else "Mail.app"


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

    # Same split as do_notify: a corrupt approval queue must not switch off the daily
    # report of doctor's health. The digest reports the broken queue instead of dying
    # on it — a digest that silently stops arriving is the failure mode this whole
    # milestone exists to remove.
    queue_error: Exception | None = None
    state_error: Exception | None = None
    pending: list[dict] = []
    expired: list[dict] = []
    patches: list[dict] = []
    state: dict = {}
    try:
        state = _load_state()
    except AlertStateError as exc:
        state_error = exc
        print(f"hitl_alerts --email: alert state unreadable — {exc}", file=sys.stderr)
    try:
        pending, expired, patches = load_queue()
    except QueueReadError as exc:
        queue_error = exc
        print(f"hitl_alerts --email: queue unreadable — {exc}", file=sys.stderr)

    today = _now().date().isoformat()
    # Read once and reuse: the send decision and the rendered section must never
    # disagree about what doctor said.
    doctor = load_doctor_last()
    problems = doctor_alert_lines(doctor)
    if queue_error:
        problems = problems + [f"QUEUE UNREADABLE: {queue_error}"]
    # Tiers are read from the state --notify maintains (fleet_first_seen); the digest
    # never advances them itself, so a manual --force cannot escalate a job by accident.
    fleet = load_fleet_probe()
    fleet_days: dict[str, int] = {}
    if fleet and "error" not in fleet:
        fleet_days = fleet_days_from_state(state, fleet.get("failing_jobs") or [])
    tiers = fleet_tiers(fleet_days)
    # Durable rows only: the digest runs in its own process and must never inherit
    # an in-memory alert a later retry has already reconciled.
    unreconciled = load_unreconciled_retirements()
    decision = decide_email(
        force=force, today=today, last_email_date=state.get("last_email_date"),
        pending=pending, expired=expired, patches=patches,
        doctor_problems=problems, fleet_clause=fleet_subject_clause(tiers),
        unreconciled=unreconciled,
    )
    if not decision.send:
        print(decision.skip_message)
        return 0

    subject = decision.subject
    body = _digest_body(pending, expired, patches, load_backlog_summary(),
                        fleet, doctor, queue_error,
                        fleet_tiers_by_job=tiers, retirements=load_retirements(),
                        unreconciled=unreconciled)
    key = _resend_key()
    channel = email_channel(key)
    sent = _send_resend(subject, body, to, key) if key else _send_mail_app(subject, body, to)
    if not sent and key and _flag("HITL_MAIL_FALLBACK"):
        # A resolvable-but-rejected key (observed live: Resend 403 on a stale key,
        # 2026-08-16) must not silence the digest entirely. Kill switch keeps the
        # file's pattern: HITL_MAIL_FALLBACK=false restores fail-hard.
        sent = _send_mail_app(subject, body, to)
        channel = "Mail.app (resend failed)"
    if sent and state_error is None:
        state["last_email_date"] = today
        try:
            _save_state(state)
        except AlertStateError as exc:
            print(
                f"hitl_alerts --email: message delivered but delivery-state persistence FAILED — {exc}",
                file=sys.stderr,
            )
            return 1
    queue_note = ", QUEUE UNREADABLE" if queue_error else ""
    print(f"hitl_alerts --email: {'sent' if sent else 'FAILED'} → {to} "
          f"({len(pending)} pending, {len(patches)} patches, {len(expired)} expired{queue_note}, "
          f"via {channel})")
    if not sent:
        return 1
    return 1 if (queue_error or state_error) else 0


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
