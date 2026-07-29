"""Tests for the doctor's schema-violation clean-day counter (execution/doctor.py).

A3 of docs/plans/2026-07-27-meta-harness-uplift.md flips warn-only validation to
enforce after 7 clean days. These tests pin the two properties that gate is worth
anything for: the streak restarts from a violation newer than the stamp, and the
whole family stays WARN-only so collecting the observation can never halt doctor.
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

from execution.doctor import _run_schema_violation_checks  # noqa: E402

_NOW = datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc)


def _stamp(state_dir: Path, days_ago: float) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    since = (_NOW - timedelta(days=days_ago)).isoformat()
    path = state_dir / "schema_violations_clean_since.json"
    path.write_text(json.dumps({"clean_since": since}), encoding="utf-8")
    return path


def _violation(state_dir: Path, days_ago: float) -> None:
    ts = (_NOW - timedelta(days=days_ago)).isoformat()
    with (state_dir / "schema_violations.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": "schema_violation", "ts": ts,
                             "schema": "sensei_ledger_row", "violations": ["pillar: bad"]}) + "\n")


def _one(results: list[dict]) -> dict:
    assert len(results) == 1
    return results[0]


def test_reports_clean_days_when_no_sink_exists(tmp_path):
    _stamp(tmp_path, days_ago=3)
    r = _one(_run_schema_violation_checks(state_dir=tmp_path, now=_NOW))
    assert r["status"] == "OK"
    assert "3.0 clean day(s)" in r["detail"]


def test_names_the_gate_as_unmet_below_seven_days(tmp_path):
    _stamp(tmp_path, days_ago=3)
    r = _one(_run_schema_violation_checks(state_dir=tmp_path, now=_NOW))
    assert "A3 gate: 7d" in r["detail"]


def test_declares_flip_eligible_at_seven_days(tmp_path):
    _stamp(tmp_path, days_ago=7)
    r = _one(_run_schema_violation_checks(state_dir=tmp_path, now=_NOW))
    assert r["status"] == "OK"
    assert "A3 flip-eligible" in r["detail"]


def test_violation_newer_than_stamp_restarts_the_streak(tmp_path):
    _stamp(tmp_path, days_ago=30)
    _violation(tmp_path, days_ago=2)
    r = _one(_run_schema_violation_checks(state_dir=tmp_path, now=_NOW))
    assert r["status"] == "WARN"
    assert "2.0d" in r["detail"]


def test_violation_older_than_stamp_leaves_the_streak_intact(tmp_path):
    _stamp(tmp_path, days_ago=8)
    _violation(tmp_path, days_ago=20)
    r = _one(_run_schema_violation_checks(state_dir=tmp_path, now=_NOW))
    assert r["status"] == "OK"
    assert "A3 flip-eligible" in r["detail"]


def test_missing_stamp_warns_rather_than_reporting_a_clean_streak(tmp_path):
    r = _one(_run_schema_violation_checks(state_dir=tmp_path, now=_NOW))
    assert r["status"] == "WARN"
    assert "no clean-since stamp" in r["detail"]


def test_unparseable_stamp_warns(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "schema_violations_clean_since.json").write_text("{not json", encoding="utf-8")
    r = _one(_run_schema_violation_checks(state_dir=tmp_path, now=_NOW))
    assert r["status"] == "WARN"


def test_unparseable_violation_rows_are_skipped_not_counted(tmp_path):
    _stamp(tmp_path, days_ago=9)
    (tmp_path / "schema_violations.jsonl").write_text("{broken\n\n", encoding="utf-8")
    r = _one(_run_schema_violation_checks(state_dir=tmp_path, now=_NOW))
    assert r["status"] == "OK"


def test_never_returns_a_fail_status(tmp_path):
    """The whole family is WARN-only: a violation is the observation A3 wants,
    so surfacing one must not gate doctor's exit code."""
    _stamp(tmp_path, days_ago=30)
    _violation(tmp_path, days_ago=1)
    results = _run_schema_violation_checks(state_dir=tmp_path, now=_NOW)
    assert [r["status"] for r in results] == ["WARN"]
