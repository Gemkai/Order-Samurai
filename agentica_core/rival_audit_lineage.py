"""rival_audit_lineage — the append-only cadence record for rival's weekly self-audit.

Mirrors experiment_lineage.py's shape exactly (itself mirroring harness_lineage.py) —
append-only JSONL, atomic single-line append, `ts` auto-filled — but is NOT that module
reused: different required keys, different vocabulary, different question. This file
answers exactly one question: did a self-audit round run, and when — the input to the
168h spacing guard rival_fixture_review.py enforces on itself. The actual per-fixture
pass/fail content (seeded vs. actual verdict) lives in state/rival_self_audit.jsonl,
written by rival_fixture_review.py --record, not here — same separation
experiment_lineage.py keeps from experiments.py.

Never prune this file. A round that leaves no row here re-runs at every cycle forever —
the same invisible-spacing failure mode harness_lineage.py's own docstring warns about.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

_LEDGER = (
    Path(__file__).resolve().parents[1] / "Order Samurai" / "state" / "rival_audit_lineage.jsonl"
)

# `skipped_no_candidate` mirrors experiment_lineage's own value: the round ran (passed
# its spacing guard) but found no unrun fixture this round. Deliberately NO
# `skipped_spacing` value, same reasoning as experiment_lineage.py: logging a
# spacing-skip would reset hours_since_last_round's own clock, so the very next check
# would see a fresh "round" seconds old and skip again forever.
DECISIONS = ("ran", "skipped_no_candidate", "error")

_REQUIRED = ("round", "decision")


def ledger_path() -> Path:
    return _LEDGER


def append_entry(entry: dict, path: Optional[Path] = None) -> dict:
    """Append one round record. Returns the stored entry (with `ts` filled in).

    Validates the decision vocabulary and required keys — same discipline as
    experiment_lineage.append_entry, for the same reason: a ledger that accepts
    free-form decisions cannot be aggregated later, and this file is the only evidence
    a round ran. `fixture_id` is NOT required — absent for skipped_no_candidate/error
    rounds that never reached a specific fixture.
    """
    for key in _REQUIRED:
        if key not in entry:
            raise ValueError(f"rival audit lineage entry missing required key {key!r}")
    if entry["decision"] not in DECISIONS:
        raise ValueError(
            f"rival audit lineage decision {entry['decision']!r} not one of {DECISIONS}"
        )

    record = dict(entry)
    record.setdefault("ts", datetime.now(timezone.utc).isoformat())

    p = path or _LEDGER
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False)
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
    the very first round through). Scans for the max timestamp, not just the last
    physically-appended line, defending against any future out-of-order write.
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
