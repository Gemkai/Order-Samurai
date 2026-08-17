"""Tests for the telemetry-freshness gate (execution/verify_telemetry_freshness.py).

The failure class under test: the June 2026 SessionEnd-emitter death, where
~/.claude/telemetry/telemetry.jsonl existed but stopped growing for 15 days and
every existence-only check stayed green. The gate must FAIL (exit-code 1) on a
stale/missing/corrupt stream, and stay OK on a fresh one.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from execution import verify_telemetry_freshness as vtf  # noqa: E402

_NOW = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)


def _write_stream(path: Path, ages_hours: list[float]) -> Path:
    lines = [
        json.dumps({"timestamp": (_NOW - timedelta(hours=h)).isoformat(),
                    "project": "X", "status": "success"})
        for h in ages_hours
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ── FAIL paths ────────────────────────────────────────────────────────────────

def test_missing_file_fails(tmp_path):
    results = vtf.run_checks(path=tmp_path / "nope.jsonl", now=_NOW)
    assert [r["status"] for r in results] == ["FAIL"]
    _, exit_code = vtf.summarize(results)
    assert exit_code == 1


def test_stale_stream_fails(tmp_path):
    stream = _write_stream(tmp_path / "t.jsonl", ages_hours=[400, 72, 49])
    results = vtf.run_checks(path=stream, now=_NOW)
    assert [r["status"] for r in results] == ["FAIL"]
    assert "49h old" in results[0]["detail"]
    _, exit_code = vtf.summarize(results)
    assert exit_code == 1


def test_corrupt_stream_fails(tmp_path):
    stream = tmp_path / "t.jsonl"
    stream.write_text("not json\n{\"timestamp\": \"garbage\"}\n", encoding="utf-8")
    results = vtf.run_checks(path=stream, now=_NOW)
    assert [r["status"] for r in results] == ["FAIL"]
    assert "no parseable" in results[0]["detail"]


def test_empty_stream_fails(tmp_path):
    stream = tmp_path / "t.jsonl"
    stream.write_text("", encoding="utf-8")
    results = vtf.run_checks(path=stream, now=_NOW)
    assert [r["status"] for r in results] == ["FAIL"]


def test_future_timestamped_record_does_not_mask_a_stale_stream(tmp_path):
    # A bogus/clock-skewed record with a future timestamp must not win "newest"
    # over the real, genuinely stale record -- otherwise (now - newest) goes
    # negative, age_hours > max_age_hours is False, and a dead emitter (the
    # exact June 2026 failure this gate exists to catch) reports OK.
    stream = _write_stream(tmp_path / "t.jsonl", ages_hours=[60, -1000])
    results = vtf.run_checks(path=stream, now=_NOW)
    assert [r["status"] for r in results] == ["FAIL"]
    assert "60h old" in results[0]["detail"]


# ── OK paths ──────────────────────────────────────────────────────────────────

def test_fresh_stream_ok(tmp_path):
    stream = _write_stream(tmp_path / "t.jsonl", ages_hours=[100, 2])
    results = vtf.run_checks(path=stream, now=_NOW)
    assert [r["status"] for r in results] == ["OK"]
    _, exit_code = vtf.summarize(results)
    assert exit_code == 0


def test_newest_record_wins_even_when_not_last_line(tmp_path):
    # Backfill appends historical records AFTER live ones — the gate must take
    # the max timestamp, not the last line.
    stream = _write_stream(tmp_path / "t.jsonl", ages_hours=[1, 500])
    results = vtf.run_checks(path=stream, now=_NOW)
    assert [r["status"] for r in results] == ["OK"]


def test_boundary_just_under_gate_is_ok(tmp_path):
    stream = _write_stream(tmp_path / "t.jsonl", ages_hours=[47.9])
    results = vtf.run_checks(path=stream, now=_NOW)
    assert [r["status"] for r in results] == ["OK"]


def test_malformed_lines_skipped_but_valid_ts_found(tmp_path):
    stream = tmp_path / "t.jsonl"
    good = json.dumps({"timestamp": (_NOW - timedelta(hours=1)).isoformat()})
    stream.write_text(f"garbage line\n{good}\n", encoding="utf-8")
    results = vtf.run_checks(path=stream, now=_NOW)
    assert [r["status"] for r in results] == ["OK"]


def test_z_suffix_and_naive_timestamps_parse(tmp_path):
    stream = tmp_path / "t.jsonl"
    z_ts = (_NOW - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    naive_ts = (_NOW - timedelta(hours=2)).replace(tzinfo=None).isoformat()
    stream.write_text(
        json.dumps({"timestamp": z_ts}) + "\n" + json.dumps({"timestamp": naive_ts}) + "\n",
        encoding="utf-8")
    results = vtf.run_checks(path=stream, now=_NOW)
    assert [r["status"] for r in results] == ["OK"]


# ── default path resolution ───────────────────────────────────────────────────

def test_default_path_matches_platform_registry():
    # Single-source-of-truth guard: the gate must watch the SAME file the
    # aggregator reads (platforms.json), not a drifted copy of the path.
    resolved = vtf._default_telemetry_path()
    assert resolved.name == "telemetry.jsonl"
    assert ".claude" in str(resolved)
