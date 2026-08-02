#!/usr/bin/env python3
"""Rotate the kill-chain event logs so they cannot grow unboundedly again.

state/kill_chain_unmatched.jsonl once reached 12,884 rows / 4.8MB (99.4% noise
from the regressed injection-guard routing) with nothing rotating it. This
script trims each kill-chain log to a bounded newest window and archives the
trimmed rows under state/logs/rotated/ (already excluded from public export by
the state/logs/* glob in bin/extract_public.py).

Invoked nightly by bin/meditation_overnight.sh (launchd-registered). Rows are
appended chronologically by the producers, so retention works on line order:
everything before the cutoff index is archived, everything after is kept. A
leading '#' schema header line is always preserved in place.

Live-append race: rows appended between read and atomic replace are lost —
at the post-fix rate (~2 rows/day) that is at most one row per rotation, and
rotation only runs at all once a file exceeds its bounds.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]

LOG_NAMES = ("kill_chain_unmatched.jsonl", "kill_chain_events.jsonl")
DEFAULT_MAX_LINES = 10_000
DEFAULT_KEEP_LINES = 5_000
DEFAULT_MAX_AGE_DAYS = 90


def _row_ts(line: str) -> datetime | None:
    try:
        raw = json.loads(line).get("ts")
        if not raw:
            return None
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    except (ValueError, TypeError):
        return None


def _cutoff_index(rows: list[str], max_lines: int, keep_lines: int, max_age: timedelta,
                  now: datetime) -> int:
    """First index to KEEP (rows are appended chronologically). 0 = no rotation."""
    count_cutoff = len(rows) - keep_lines if len(rows) > max_lines else 0

    age_floor = now - max_age
    timestamps = [(ts, i) for i, line in enumerate(rows)
                  if (ts := _row_ts(line)) is not None]
    age_cutoff = 0
    if timestamps:
        # Everything before the first in-window row is old; if no row is in
        # the window, the whole file is old. Unparseable rows follow their
        # chronological neighbors; a file with no parseable rows never
        # age-rotates (nothing proves it is old).
        age_cutoff = next((i for ts, i in timestamps if ts >= age_floor), len(rows))

    return max(count_cutoff, age_cutoff)


def rotate_file(path: Path, archive_dir: Path, *, max_lines: int, keep_lines: int,
                max_age_days: int, now: datetime | None = None,
                dry_run: bool = False) -> str:
    if not path.exists():
        return f"{path.name}: absent, skipped"
    now = now or datetime.now(timezone.utc)

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    header = [l for l in lines[:1] if l.startswith("#")]
    rows = lines[len(header):]

    cutoff = _cutoff_index(rows, max_lines, keep_lines, timedelta(days=max_age_days), now)
    if cutoff <= 0:
        return f"{path.name}: {len(rows)} rows within bounds, no rotation"

    archived, kept = rows[:cutoff], rows[cutoff:]
    if dry_run:
        return f"{path.name}: DRY RUN would archive {len(archived)}, keep {len(kept)}"

    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y-%m-%d")
    archive_path = archive_dir / f"{path.stem}-rotated-{stamp}.jsonl"
    with archive_path.open("a", encoding="utf-8") as fh:
        fh.writelines(archived)

    tmp = path.with_suffix(path.suffix + ".rotate.tmp")
    tmp.write_text("".join(header + kept), encoding="utf-8")
    os.replace(tmp, path)
    return f"{path.name}: archived {len(archived)} -> {archive_path.name}, kept {len(kept)}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rotate the kill-chain event logs so they cannot grow unboundedly.")
    parser.add_argument("--state-dir", type=Path, default=ROOT_DIR / "state")
    parser.add_argument("--max-lines", type=int, default=DEFAULT_MAX_LINES)
    parser.add_argument("--keep-lines", type=int, default=DEFAULT_KEEP_LINES)
    parser.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    archive_dir = args.state_dir / "logs" / "rotated"
    for name in LOG_NAMES:
        summary = rotate_file(
            args.state_dir / name, archive_dir,
            max_lines=args.max_lines, keep_lines=args.keep_lines,
            max_age_days=args.max_age_days, dry_run=args.dry_run,
        )
        print(f"[rotate_kill_chain_logs] {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
