"""Bootstrap metrics_history.jsonl from existing telemetry so σ-based reflexes and
trend sparklines have real data points immediately (instead of falling back to fixed
thresholds while live snapshots slowly accumulate).

Replays ALL telemetry grouped by ISO week, computes each week's combined pillar metrics,
and writes one history row per week (keyed `pillar/group/metric`, matching
insights.populate_history). Idempotent: regenerates all weekly rows each run. Does NOT
preserve sub-weekly live snapshots — those are discarded because they crowd the trailing
window with near-identical intra-day refreshes. The aggregate layer re-appends today's
live value at runtime via populate_history().

  python -m agentica_core.backfill_history
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import aggregate as agg, insights


def _week_monday_iso(week: str) -> str:
    try:
        y, w = week.split("-W")
        return datetime.fromisocalendar(int(y), int(w), 1).replace(tzinfo=timezone.utc).isoformat()
    except (ValueError, AttributeError):
        return week


def _week_values(wrecs: list[dict]) -> dict[str, float]:
    """Flatten one week's combined telemetry pillars to {pillar/group/metric: value}."""
    return insights.live_numeric_metrics(agg.build_pillars(wrecs))


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

    weekly_rows = [
        {"ts": _week_monday_iso(w), "week": w, "values": _week_values(by_week[w])}
        for w in sorted(by_week)
    ]

    # Weekly cadence IS the canonical history series (one row per ISO week). Prior
    # sub-weekly live snapshots are discarded — they crowd the trailing window with
    # near-identical intra-day refreshes and bury week-over-week variation.
    # populate_history() still appends today's live value as the latest point at runtime,
    # and the weekly --snapshot task extends this series one row per week going forward.
    store.write_text("\n".join(json.dumps(r) for r in weekly_rows) + "\n", encoding="utf-8")
    return len(weekly_rows)


def main() -> int:
    n = backfill()
    print(f"backfill_history: wrote {n} weekly history rows -> {insights.default_history_path()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
