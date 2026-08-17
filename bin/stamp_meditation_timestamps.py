#!/usr/bin/env python3
"""Idempotent backstop: stamp missing started_at / completed_at on meditation backlog items.

The sensei prompt (meditation_cycle.md Steps C/F) is instructed to stamp timestamps,
but prompt instructions are not guarantees. This script is the code-level
guarantee that calibration samples (started_at, completed_at) accumulate.
Run at the end of every cycle (Step F) or manually.

Items stamped here get "backfilled": true so calibration can distinguish a
measured duration from a stamped-at-discovery one.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# State is canonical in the MAIN tree; meditation_overnight.sh exports MEDITATION_STATE_DIR when the
# cycle runs in a worktree so calibration timestamps accumulate in the state the hero metrics read.
# Falls back to the script-relative state/ for standalone use.
_STATE_DIR = os.environ.get("MEDITATION_STATE_DIR")
STATE = (Path(_STATE_DIR) if _STATE_DIR else Path(__file__).resolve().parents[1] / "state") / "MEDITATION_STATE.json"


def main() -> int:
    if not STATE.exists():
        print(f"stamp_meditation_timestamps: {STATE} not found")
        return 1
    data = json.loads(STATE.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    stamped = 0
    for item in data.get("backlog", []):
        status = item.get("status")
        if status == "doing" and not item.get("started_at"):
            item["started_at"] = now
            stamped += 1
        elif status == "done" and not item.get("completed_at"):
            item["completed_at"] = now
            item["backfilled"] = True
            stamped += 1
        elif (status == "done" and isinstance(item.get("completed_at"), str)
                and len(item["completed_at"]) == 10
                and item.get("started_at")):
            # Date-only completed_at (cycle models copy the historical style)
            # parses as midnight -> negative duration -> discarded calibration
            # sample. Only normalize items with a real started_at: those are
            # this run's completions, so "now" is minutes off at most.
            # Pre-instrumentation historical items (started_at null) untouched.
            item["completed_at"] = now
            item["backfilled"] = True
            stamped += 1
    if stamped:
        tmp = STATE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(STATE)
    print(f"stamp_meditation_timestamps: stamped {stamped} item(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
