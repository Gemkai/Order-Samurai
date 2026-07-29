"""Tests for the doctor's Claude-telemetry recency gate (execution/doctor.py).

The SessionEnd emitter swallows all errors by design, so record recency is the
pipeline's only liveness signal. These tests pin the gate semantics: a missing
sink, an empty/unparseable sink, or a stale newest record is a FAIL (which
main() folds into doctor's exit code), never a WARN.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
_GOVERNANCE = Path(__file__).resolve().parents[2]
if str(_GOVERNANCE) not in sys.path:
    sys.path.insert(0, str(_GOVERNANCE))

from execution.doctor import _run_claude_telemetry_checks  # noqa: E402

_NOW = datetime(2026, 7, 6, 12, 0, 0, tzinfo=timezone.utc)


def _record(age_hours: float) -> str:
    ts = (_NOW - timedelta(hours=age_hours)).isoformat()
    return json.dumps({"timestamp": ts, "task_name": "session"})


def _one(results: list[dict]) -> dict:
    assert len(results) == 1
    return results[0]


def test_missing_file_fails(tmp_path):
    r = _one(_run_claude_telemetry_checks(source=tmp_path / "telemetry.jsonl", now=_NOW))
    assert r["status"] == "FAIL"
    assert "no telemetry file" in r["detail"]


def test_no_parseable_records_fails(tmp_path):
    src = tmp_path / "telemetry.jsonl"
    src.write_text("not json\n{\"no_ts\": true}\n", encoding="utf-8")
    r = _one(_run_claude_telemetry_checks(source=src, now=_NOW))
    assert r["status"] == "FAIL"
    assert "no parseable" in r["detail"]


def test_stale_newest_record_fails(tmp_path):
    src = tmp_path / "telemetry.jsonl"
    src.write_text(_record(90) + "\n" + _record(49) + "\n", encoding="utf-8")
    r = _one(_run_claude_telemetry_checks(source=src, now=_NOW))
    assert r["status"] == "FAIL"
    assert "silently dead" in r["detail"]


def test_newest_wins_even_out_of_order(tmp_path):
    # append-only files can carry backfilled/out-of-order lines; the gate must
    # judge the NEWEST record, not the last line
    src = tmp_path / "telemetry.jsonl"
    src.write_text(_record(2) + "\n" + _record(200) + "\n", encoding="utf-8")
    r = _one(_run_claude_telemetry_checks(source=src, now=_NOW))
    assert r["status"] == "OK"


def test_fresh_record_ok_and_reports_age(tmp_path):
    src = tmp_path / "telemetry.jsonl"
    src.write_text(_record(3) + "\n", encoding="utf-8")
    r = _one(_run_claude_telemetry_checks(source=src, now=_NOW))
    assert r["status"] == "OK"
    assert "3.0h" in r["detail"]


def test_threshold_is_parameterized(tmp_path):
    src = tmp_path / "telemetry.jsonl"
    src.write_text(_record(10) + "\n", encoding="utf-8")
    assert _one(_run_claude_telemetry_checks(max_age_hours=8, source=src, now=_NOW))["status"] == "FAIL"
    assert _one(_run_claude_telemetry_checks(max_age_hours=12, source=src, now=_NOW))["status"] == "OK"


def test_truncated_multibyte_line_does_not_mask_fresh_records(tmp_path):
    """A killed write can leave a truncated multibyte sequence at the tail of the
    JSONL sink. That single bad byte must not raise UnicodeDecodeError and false-FAIL
    the gate ("telemetry file unreadable") while fresh, parseable records exist —
    the sibling verify_telemetry_freshness reads the same file with errors="ignore".
    """
    src = tmp_path / "telemetry.jsonl"
    good = (_record(1) + "\n").encode("utf-8")
    truncated = '{"timestamp": "café'.encode("utf-8")[:-1]  # cut mid-multibyte char
    src.write_bytes(good + truncated)
    r = _one(_run_claude_telemetry_checks(source=src, now=_NOW))
    assert r["status"] == "OK", r["detail"]


def test_mixed_naive_and_aware_timestamps_do_not_mask_fresh_records(tmp_path):
    """A file holding both tz-aware and naive (offset-less) timestamps must not
    raise TypeError on the newest-record comparison and false-FAIL the gate as
    "telemetry file unreadable" — naive stamps are graded as UTC, matching the
    fix-up the gate already applies to `newest` after the loop.
    """
    src = tmp_path / "telemetry.jsonl"
    naive = json.dumps({"timestamp": (_NOW - timedelta(hours=2)).replace(tzinfo=None).isoformat(),
                        "task_name": "session"})
    src.write_text(_record(3) + "\n" + naive + "\n", encoding="utf-8")
    r = _one(_run_claude_telemetry_checks(source=src, now=_NOW))
    assert r["status"] == "OK", r["detail"]
    assert "2.0h" in r["detail"]  # the naive record is the newest and graded as UTC
