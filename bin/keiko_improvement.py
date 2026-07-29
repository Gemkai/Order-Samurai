#!/usr/bin/env python3
"""keiko_improvement.py — loop-until-dry early-stop signal for the meditation keiko.

A keiko (meditation_overnight.sh) runs a fixed >=60 cycles / 6h regardless of whether the
cycles are productive. Running past the point of genuine work wastes tokens and tempts
the agent into manufactured busywork. This script tracks a progress signal across cycles
and tells the loop to halt once K consecutive cycles produce no improvement.

Progress signal = sum of each pillar's live_current (count of live metrics) in
state/MEDITATION_STATE.json. live_current rising = more instrumentation = real progress.

State carried in MEDITATION_STATE.json (additive fields, ignored by other readers):
  _keiko_last_signal : the signal at the previous cycle
  flat_cycle_count   : consecutive cycles with no improvement

Exit code: 0 = keep going (or first observation), 3 = halt (K flat cycles reached).
Called after each cycle by meditation_overnight.sh.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")  # Windows cp1252 guard

# State is canonical in the MAIN tree; meditation_overnight.sh exports MEDITATION_STATE_DIR when the
# cycle runs in a worktree so the flat-cycle signal is read/written against the same state the hero
# metrics see. Falls back to the script-relative state/ for standalone use.
_STATE_DIR = os.environ.get("MEDITATION_STATE_DIR")
STATE = (Path(_STATE_DIR) if _STATE_DIR else Path(__file__).resolve().parents[1] / "state") / "MEDITATION_STATE.json"


def _signal(state: dict) -> float:
    """Sum of live_current across pillars (None treated as 0)."""
    total = 0.0
    for p in (state.get("pillars") or {}).values():
        v = p.get("live_current")
        if isinstance(v, (int, float)):
            total += v
    return total


def evaluate(state: dict, k: int) -> tuple[dict, int, str]:
    """Pure core: returns (updated_state, exit_code, message). No I/O — unit-testable."""
    signal = _signal(state)
    prev = state.get("_keiko_last_signal")
    flat = int(state.get("flat_cycle_count", 0))

    if prev is None:
        msg = f"keiko: baseline signal={signal:g} (first observation)"
        flat = 0
    elif signal > prev:
        msg = f"keiko: improved {prev:g} -> {signal:g} (counter reset)"
        flat = 0
    else:
        flat += 1
        msg = f"keiko: no improvement ({signal:g} <= {prev:g}), flat {flat}/{k}"

    state["_keiko_last_signal"] = signal
    state["flat_cycle_count"] = flat
    return state, (3 if flat >= k else 0), msg


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5, help="halt after K consecutive flat cycles")
    ap.add_argument("--state", type=Path, default=STATE)
    args = ap.parse_args()

    try:
        state = json.loads(args.state.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"keiko: cannot read {args.state} ({exc}) — not halting")
        return 0  # never halt on a read error; let the loop's other guards govern

    state, code, msg = evaluate(state, args.k)
    print(msg)

    tmp = args.state.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(args.state)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
