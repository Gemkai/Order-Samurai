"""experiment_lineage — the append-only cadence record for the weekly experiment lane.

Mirrors harness_lineage.py's shape (append-only JSONL, atomic single-line append, `ts`
auto-filled) but is NOT that module reused: harness_lineage validates a fixed vocabulary
and required keys specific to self-harness code-edit candidates
(candidate_id/round/decision ∈ accepted/rejected/structural_reject/no_candidates), and
this phase's rounds are a different kind of thing entirely — see experiments.py for the
actual experiment content (hypothesis/verdict/etc.), which this ledger deliberately does
NOT carry. This file answers exactly one question: did a round run, and when — the input
to the 168h spacing guard run_experiment_cycle.py enforces on itself, the same way
self_harness_cycle.py enforces its own spacing from harness_lineage.

Never prune this file. A round that leaves no row here re-runs at every cycle forever —
the same invisible-spacing failure mode harness_lineage.py's own docstring warns about.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

_LEDGER = (
    Path(__file__).resolve().parents[1] / "Order Samurai" / "state" / "experiment_lineage.jsonl"
)

# `skipped_no_candidate` mirrors harness_lineage's `no_candidates`: the round ran (passed
# its spacing guard) but found nothing pending in EXPERIMENTS.jsonl to execute.
#
# Deliberately NO `skipped_spacing` value, matching self_harness_cycle.py's documented
# behavior exactly: a round that never got past the spacing check writes NOTHING here.
# Logging it would be self-defeating — hours_since_last_round would then measure time
# since the skip itself, so the very next check sees a fresh "round" a few seconds old
# and skips again, forever. The guard's exit must be silent by construction, not by
# caller discipline; there is no value in this vocabulary a caller could pass to
# reproduce that bug.
DECISIONS = ("ran", "skipped_no_candidate", "error")

_REQUIRED = ("experiment_id", "round", "decision")


def ledger_path() -> Path:
    return _LEDGER


def append_entry(entry: dict, path: Optional[Path] = None) -> dict:
    """Append one round record. Returns the stored entry (with `ts` filled in).

    Validates the decision vocabulary and required keys — same discipline as
    harness_lineage.append_entry, for the same reason: a ledger that accepts free-form
    decisions cannot be aggregated later, and this file is the only evidence a round ran.
    `experiment_id` is `None` for skipped_spacing/skipped_no_candidate rounds (no
    experiment was touched) — required as a KEY, not as a non-null value.
    """
    for key in _REQUIRED:
        if key not in entry:
            raise ValueError(f"experiment lineage entry missing required key {key!r}")
    if entry["decision"] not in DECISIONS:
        raise ValueError(
            f"experiment lineage decision {entry['decision']!r} not one of {DECISIONS}"
        )

    record = dict(entry)
    record.setdefault("ts", datetime.now(timezone.utc).isoformat())

    p = path or _LEDGER
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False)
    # Append mode + a single write of one newline-terminated line: same atomicity
    # rationale as harness_lineage.append_entry — a single-writer weekly cycle's O_APPEND
    # write of a short line never truncates a prior row.
    with open(p, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return record


def iter_entries(path: Optional[Path] = None) -> Iterator[dict]:
    """Yield stored entries oldest-first. Unparseable lines are skipped, never raised.

    A torn final line (killed mid-append) must not make the whole lineage unreadable.
    """
    p = path or _LEDGER
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except ValueError:
            continue


def hours_since_last_round(path: Optional[Path] = None) -> Optional[float]:
    """Hours since the most recent round of ANY decision — the spacing guard's input.

    Returns None when the ledger is empty (no round has ever run — the guard should let
    the very first round through). Every decision counts as "a round ran", including
    skipped_no_candidate: a cycle that checked and found nothing pending still spent its
    slot for the spacing window, same as self_harness_cycle's own no_candidates round.
    """
    last_ts: Optional[datetime] = None
    for e in iter_entries(path):
        ts = e.get("ts")
        if not isinstance(ts, str):
            continue
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if last_ts is None or dt > last_ts:
            last_ts = dt
    if last_ts is None:
        return None
    now = datetime.now(timezone.utc)
    return (now - last_ts).total_seconds() / 3600.0
