"""Threshold-change audit trail (plan Phase 2, anti-gaming guard 2).

Order Samurai is a self-governing agentic system: the needs-attention count is an implicit
incentive the reflex engine optimizes against. Loosening a warn/fail threshold (or dropping a
metric) would silently lower that count without fixing anything. This module records every
change to the *effective* thresholds against a snapshot baseline, so no edit — hand-made or
recalibrated — can happen invisibly. The log is append-only and never consulted at a decision
point; it exists purely so a human (or a later audit) can see what moved and when.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

_STATE = Path(__file__).resolve().parents[1] / "state"
AUDIT_PATH = _STATE / "threshold_audit.jsonl"
SNAPSHOT_PATH = _STATE / "threshold_snapshot.json"


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def audit_threshold_changes(
    rules: dict[str, dict], *,
    audit_path: Path = AUDIT_PATH,
    snapshot_path: Path = SNAPSHOT_PATH,
    now: str | None = None,
    source: str = "calibration",
) -> list[dict]:
    """Diff the current effective thresholds against the last snapshot; append a JSONL entry for
    every metric whose warn/fail changed or that was added/removed. Returns the entries written.

    rules: {metric: {"warn": float, "fail": float, "dir": str}} — typically insights.METRIC_RULES.

    First run (no snapshot file yet) seeds the baseline silently — the initial set of thresholds
    is not a "change". Subsequent edits are logged.
    """
    now = now or datetime.now(timezone.utc).isoformat()
    cur = {m: {"warn": r.get("warn"), "fail": r.get("fail"), "dir": r.get("dir")}
           for m, r in rules.items()}

    first_run = not snapshot_path.exists()
    prev = _load_json(snapshot_path)

    changes: list[dict] = []
    if not first_run:
        for m, r in cur.items():
            old = prev.get(m)
            if old is None:
                changes.append({"ts": now, "source": source, "metric": m, "change": "added", "new": r})
            elif old.get("warn") != r["warn"] or old.get("fail") != r["fail"]:
                changes.append({
                    "ts": now, "source": source, "metric": m, "change": "threshold",
                    "old": {"warn": old.get("warn"), "fail": old.get("fail")},
                    "new": {"warn": r["warn"], "fail": r["fail"]},
                })
        for m, old in prev.items():
            if m not in cur:
                changes.append({"ts": now, "source": source, "metric": m, "change": "removed", "old": old})

    if changes:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("a", encoding="utf-8") as f:
            for c in changes:
                f.write(json.dumps(c) + "\n")

    # Refresh the baseline so the next diff is against the latest accepted state.
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps(cur, indent=1, sort_keys=True), encoding="utf-8")
    return changes
