#!/usr/bin/env python3
"""Idempotent backstop: requeue orphaned "doing" backlog items so a later cycle re-scans them.

STEP C of meditation_cycle.md picks the highest item with status != "done" AND
!= "doing" — so any item left in "doing" is invisible to every future cycle. When
a cycle halts after Step C marks items "doing" but before it completes them (API
crash, budget cap, a blocked `git worktree add`, a hard timeout — all of which
happened 2026-07-08/09), those items are stranded forever and the Agent-/Human-
hours heroes never see the work. Prompt instructions to revert them are not
guarantees; this is the code-level guarantee.

Run at the START of every overnight run (before the cycle loop) and/or manually.
A run lasts at most RUN_HOURS (default 6), and the single-instance lock forbids a
second concurrent run — so any "doing" item whose started_at predates the
staleness window is provably from a dead prior run, not in-flight work. Such items
are reset to status="todo", started_at cleared, and requeue bookkeeping stamped.

Env:
  MEDITATION_STALE_DOING_HOURS  staleness threshold in hours (default 6)
Flags:
  --dry-run   report what would be requeued without writing
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# State is canonical in the MAIN tree. meditation_overnight.sh runs the cycle in a disposable
# worktree and exports MEDITATION_STATE_DIR pointing at the main-tree state/ so requeues land
# where the next run and the hero metrics read them; standalone/manual use falls back to the
# script-relative state/ dir.
_STATE_DIR = os.environ.get("MEDITATION_STATE_DIR")
STATE = (Path(_STATE_DIR) if _STATE_DIR else Path(__file__).resolve().parents[1] / "state") / "MEDITATION_STATE.json"


def _parse_iso(s: str | None) -> datetime | None:
    if not s or not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        # A full datetime without a Z/offset parses tz-naive; treat it as UTC so the
        # age subtraction against an aware `now` never raises TypeError (the cycle
        # models copy the historical style and can write naive started_at values).
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        # date-only (YYYY-MM-DD) parses as naive midnight -> treat as UTC
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def main() -> int:
    dry_run = "--dry-run" in sys.argv[1:]
    if not STATE.exists():
        print(f"reap_stale_doing: {STATE} not found")
        return 1
    try:
        stale_hours = float(os.environ.get("MEDITATION_STALE_DOING_HOURS", "6"))
    except ValueError:
        stale_hours = 6.0

    data = json.loads(STATE.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat().replace("+00:00", "Z")
    reaped = []
    for item in data.get("backlog", []):
        if item.get("status") != "doing":
            continue
        started = _parse_iso(item.get("started_at"))
        # No started_at, or started_at older than the staleness window => orphaned.
        age_h = None if started is None else (now - started).total_seconds() / 3600.0
        if started is not None and age_h < stale_hours:
            continue  # plausibly in-flight this run — leave it
        if not dry_run:
            item["status"] = "todo"
            item["started_at"] = None
            item["requeued_at"] = now_iso
            item["requeue_count"] = int(item.get("requeue_count") or 0) + 1
        reaped.append((item.get("id", "?"), item.get("pillar", "?"),
                       "no-started_at" if age_h is None else f"{age_h:.1f}h stale"))

    if reaped and not dry_run:
        tmp = STATE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(STATE)

    verb = "would requeue" if dry_run else "requeued"
    print(f"reap_stale_doing: {verb} {len(reaped)} stale 'doing' item(s) "
          f"(threshold {stale_hours:g}h)")
    for _id, pillar, why in reaped:
        print(f"  ~ [{pillar}] {_id} ({why}) -> todo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
