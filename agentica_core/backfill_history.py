"""Bootstrap metrics_history.jsonl from existing telemetry so σ-based reflexes and
trend sparklines have real data points immediately (instead of falling back to fixed
thresholds while live snapshots slowly accumulate).

Replays ALL telemetry grouped by ISO week, computes each week's combined pillar metrics,
and writes one history row per week (keyed `pillar/group/metric`, matching
insights.populate_history). Idempotent: regenerates all weekly rows each run. Does NOT
preserve sub-weekly live snapshots as ROWS — those are discarded because they crowd the
trailing window with near-identical intra-day refreshes. The aggregate layer re-appends
today's live value at runtime via populate_history().

It DOES preserve their values (2026-08-08 seq 9c): a key that _week_values cannot
recompute from telemetry — every live-only reducer (scouts, exec-log metrics, security
signals) — is carried forward onto the rebuilt weekly rows from the most recent prior
observation at or before each week. Without that, the truncate-and-rebuild erased those
series on every run and left 24 metrics stuck at exactly one history point forever.

  python -m agentica_core.backfill_history
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import aggregate as agg, insights
from .telemetry import parse_ts


def _week_monday_iso(week: str) -> str:
    try:
        y, w = week.split("-W")
        return datetime.fromisocalendar(int(y), int(w), 1).replace(tzinfo=timezone.utc).isoformat()
    except (ValueError, AttributeError):
        return week


def _week_values(wrecs: list[dict]) -> dict[str, float]:
    """Flatten one week's combined telemetry pillars to {pillar/group/metric: value}."""
    return insights.live_numeric_metrics(agg.build_pillars(wrecs))


def _week_row(week: str, wrecs: list[dict]) -> dict:
    """One weekly history row. Metrics that reported a state instead of a number
    (Mechanism_Liveness idle_by_design) are listed under "idle" so _carry_forward does
    not back-fill an older number into a week that deliberately has none."""
    pillars = agg.build_pillars(wrecs)
    row = {"ts": _week_monday_iso(week), "week": week, "kind": "weekly",
           "values": insights.live_numeric_metrics(pillars)}
    idle = sorted(f"{pk}/{g}/{mk}" for pk, groups in pillars.items()
                  for g, metrics in groups.items() for mk, env in metrics.items()
                  if env.get("state") and not env.get("is_simulated"))
    if idle:
        row["idle"] = idle
    return row


def _sort_key(ts: str) -> tuple:
    """Sortable key for a history row's `ts`. Unparseable timestamps sort last so a
    junk row can never shadow a real observation."""
    dt = parse_ts(ts)
    if dt is None:
        return (1, datetime.max.replace(tzinfo=timezone.utc))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (0, dt)


def _existing_rows(store: Path) -> list[dict]:
    """Every parseable row already in the store, oldest first (best-effort)."""
    if not store.exists():
        return []
    rows = []
    for ln in store.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            row = json.loads(ln)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("values"), dict):
            rows.append(row)
    rows.sort(key=lambda r: _sort_key(r.get("ts", "")))
    return rows


def _carry_forward(weekly_rows: list[dict], prior_rows: list[dict]) -> list[dict]:
    """Re-attach values for keys the weekly rebuild does not produce.

    _week_values() emits only what can be recomputed from telemetry. Live-only metrics
    (scout counts, exec-log reducers, security signals) exist solely on the snapshot
    rows this rebuild truncates away — so every run wiped their history and re-stamped
    the survivors with today's value. For each rebuilt week take the most recent prior
    observation at or **before** that week's timestamp; never a later one, which would
    back-date a value that did not exist yet. Keys the week DOES produce are untouched —
    a real recomputation always wins over a carried one.

    Kill switch: BACKFILL_CARRY_FORWARD=false restores the pure truncate-and-rebuild."""
    if os.environ.get("BACKFILL_CARRY_FORWARD", "true").strip().lower() in ("false", "0", "no"):
        return weekly_rows
    if not prior_rows:
        return weekly_rows
    _keep_recorded_idle(weekly_rows, prior_rows)

    ordered = sorted(weekly_rows, key=lambda r: _sort_key(r.get("ts", "")))
    latest: dict[str, float] = {}   # key -> most recent prior value at/before this week
    i = 0
    for row in ordered:
        cutoff = _sort_key(row.get("ts", ""))
        while i < len(prior_rows) and _sort_key(prior_rows[i].get("ts", "")) <= cutoff:
            for k, v in (prior_rows[i].get("values") or {}).items():
                if isinstance(v, (int, float)):
                    latest[k] = v
            i += 1
        idle = set(row.get("idle") or ())
        carried = sorted(k for k in latest if k not in row["values"] and k not in idle)
        if carried:
            for k in carried:
                row["values"][k] = latest[k]
            # Mark synthetic points so a reader (and any future calibration guard) can
            # tell a carried value from a fresh weekly observation.
            row["carried_forward"] = carried
    return weekly_rows


def _keep_recorded_idle(weekly_rows: list[dict], prior_rows: list[dict]) -> None:
    """A week the store already recorded as idle stays idle on rebuild unless the rebuild
    observed real activity (a value > 0, or an engine run logged that week). The evidence that made it idle (reflex
    heartbeats) rotates away, so a later rebuild would otherwise read the same week as a
    graded 0 or carry an older number into it."""
    recorded: dict[str, set[str]] = defaultdict(set)
    for r in prior_rows:
        if r.get("kind") == "weekly" and r.get("week"):
            recorded[r["week"]].update(r.get("idle") or ())
    for row in weekly_rows:
        if row.get("week") not in recorded or not agg.recorded_idle_still_valid(row["week"]):
            continue
        keep = {k for k in recorded[row["week"]]
                if not (isinstance(row["values"].get(k), (int, float)) and row["values"][k] > 0)}
        if not keep:
            continue
        for k in keep:
            row["values"].pop(k, None)
        row["idle"] = sorted(keep | set(row.get("idle") or ()))


def backfill(store: Path | None = None) -> int:
    store = store or insights.default_history_path()
    store.parent.mkdir(parents=True, exist_ok=True)

    # all telemetry across platforms, grouped by ISO week
    all_recs: list[dict] = []
    for p in agg.list_platforms():
        all_recs.extend(agg.load_records(p))
    by_week: dict[str, list] = defaultdict(list)
    for r in all_recs:
        w = agg.iso_week(r.get("timestamp", ""))
        if w:
            by_week[w].append(r)

    weekly_rows = [_week_row(w, by_week[w]) for w in sorted(by_week)]

    # Zero rows means the SOURCE failed (a platform that didn't resolve, a moved
    # telemetry file, timestamps that no longer parse) — never evidence that the
    # history is empty. Rewriting the store from nothing would destroy every sigma
    # baseline, sparkline and prior-week comparison the reducers read back, and
    # refresh_dashboard calls this on every --snapshot run. Keep what's there.
    if not weekly_rows and store.exists():
        return 0

    # Read the store BEFORE the lock (see below): merging happens here so the locked
    # truncate-and-rewrite block stays exactly as reviewed. A live append racing this
    # read is no worse off than before — the truncate already discarded it.
    prior_rows = _existing_rows(store)
    weekly_rows = _carry_forward(weekly_rows, prior_rows)

    # Preserve the single most recent kind:"live" row across the rebuild (same
    # BACKFILL_CARRY_FORWARD kill switch as _carry_forward — both exist to survive
    # this truncate-and-rebuild; "false" restores the PURE rebuild neither one).
    # max_live_rows=1 per scorecard's data_history_live_rows probe.
    #
    # Why this matters: refresh_dashboard.py calls backfill() BEFORE
    # aggregate()/populate_history() on every --snapshot run. Without this, the live
    # row the PRIOR cycle's aggregate() appended (this module's own comment below
    # says "populate_history() still appends today's live value as the latest
    # point") is wiped here before THIS cycle's populate_history() ever reads it.
    # insights._comparable_history_rows() (kind in (None, "live")) then always finds
    # zero rows to diff against, so delta/trend and the sigma-anomaly tier stay
    # stuck at their neutral defaults forever instead of comparing against the last
    # snapshot.
    kill_switch = os.environ.get("BACKFILL_CARRY_FORWARD", "true").strip().lower() in ("false", "0", "no")
    live_rows = [] if kill_switch else [r for r in prior_rows if r.get("kind") == "live"]
    latest_live = [live_rows[-1]] if live_rows else []

    # Weekly cadence IS the canonical history series (one row per ISO week). Prior
    # sub-weekly live snapshots beyond the single preserved one are discarded — they
    # crowd the trailing window with near-identical intra-day refreshes and bury
    # week-over-week variation. populate_history() still appends today's live value
    # as the latest point at runtime, and the weekly --snapshot task extends this
    # series one row per week going forward.
    #
    # Rewrite under the SAME flock insights.append_snapshot takes, so a concurrent
    # live append cannot interleave with this truncate-and-rebuild (adversarial
    # review 2026-07-26: the append path was locked but this rewrite was not).
    # Open "a+" then truncate — opening "w" would truncate BEFORE the lock is held.
    try:
        import fcntl
    except ImportError:  # non-POSIX host: fall back to unlocked rewrite
        fcntl = None
    with store.open("a+", encoding="utf-8") as fh:
        locked = False
        if fcntl is not None:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX)
                locked = True
            except OSError:  # filesystem without POSIX locks
                pass
        try:
            fh.seek(0)
            fh.truncate()
            fh.write("\n".join(json.dumps(r) for r in weekly_rows + latest_live) + "\n")
            # Flush + fsync BEFORE unlock, else the buffered tail lands after a
            # concurrent locked append and tears the file (adversarial review).
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            if locked and fcntl is not None:
                fcntl.flock(fh, fcntl.LOCK_UN)
    return len(weekly_rows)


def main() -> int:
    n = backfill()
    print(f"backfill_history: wrote {n} weekly history rows -> {insights.default_history_path()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
