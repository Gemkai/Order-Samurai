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


def test_tz_naive_stamp_is_graded_utc_not_a_crash(tmp_path):
    """The stamp is hand-maintained; datetime.now().isoformat() or a bare date
    produces a naive value. The module's own docstring rule is that collecting
    the observation can never halt doctor — a TypeError here aborts every
    check that runs after _run_schema_violation_checks in doctor's main."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    naive_since = (_NOW - timedelta(days=3)).replace(tzinfo=None).isoformat()
    (tmp_path / "schema_violations_clean_since.json").write_text(
        json.dumps({"clean_since": naive_since}), encoding="utf-8")
    _violation(tmp_path, days_ago=1)
    r = _one(_run_schema_violation_checks(state_dir=tmp_path, now=_NOW))
    assert r["status"] == "WARN"
    assert "reset the streak" in r["detail"]


def test_never_returns_a_fail_status(tmp_path):
    """The whole family is WARN-only: a violation is the observation A3 wants,
    so surfacing one must not gate doctor's exit code."""
    _stamp(tmp_path, days_ago=30)
    _violation(tmp_path, days_ago=1)
    results = _run_schema_violation_checks(state_dir=tmp_path, now=_NOW)
    assert [r["status"] for r in results] == ["WARN"]


# ── instance-keyed streak (audit B4, 2026-09-02) ────────────────────────────────
#
# reflex-engine re-validates its history on every API restart and re-appends a row
# per pre-existing bad record. One 2026-08-06 SENSEI_LEDGER row had been re-recorded
# 14 times, most recently 2026-08-29, and reading the ROW ts restarted the 7-day
# streak on every restart — A3 was unflippable from 2026-07-27 while no new violation
# had occurred since 08-07.

def _reobservation(state_dir: Path, *, instance_days_ago: float, row_days_ago: float,
                   reflex_id: str = "correlation:vault_health_crisis") -> None:
    """A reflex-engine:startup row re-recording an OLD instance at a RECENT row ts."""
    with (state_dir / "schema_violations.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "event": "schema_violation",
            "sink": "SENSEI_LEDGER.jsonl",
            "observer": "reflex-engine:startup",
            "schema": "sensei_ledger_row",
            "violations": ["data.pillar should be equal to one of the allowed values"],
            "instance": {"ts": (_NOW - timedelta(days=instance_days_ago)).isoformat(),
                         "reflex_id": reflex_id},
            "ts": (_NOW - timedelta(days=row_days_ago)).isoformat(),
        }) + "\n")


def test_a_re_observed_instance_does_not_restart_the_streak(tmp_path):
    """The B4 case: an 08-06 instance re-recorded at an 08-29 row ts. The streak is
    counted from when the violation HAPPENED, not from when a restart noticed it."""
    _stamp(tmp_path, days_ago=40)
    _reobservation(tmp_path, instance_days_ago=30, row_days_ago=30)
    _reobservation(tmp_path, instance_days_ago=30, row_days_ago=1)

    r = _one(_run_schema_violation_checks(state_dir=tmp_path, now=_NOW))
    assert "30.0d" in r["detail"]
    assert "1.0d" not in r["detail"]
    assert "1 distinct violation(s)" in r["detail"]
    assert "1 re-observation(s)" in r["detail"]


def test_distinct_instances_each_count_even_from_the_same_observer(tmp_path):
    """De-duplication keys on the instance, not on the observer. Two genuinely
    different bad records re-observed by the same restart are two violations."""
    _stamp(tmp_path, days_ago=40)
    _reobservation(tmp_path, instance_days_ago=30, row_days_ago=1, reflex_id="a")
    _reobservation(tmp_path, instance_days_ago=20, row_days_ago=1, reflex_id="b")

    r = _one(_run_schema_violation_checks(state_dir=tmp_path, now=_NOW))
    assert "2 distinct violation(s)" in r["detail"]
    assert "20.0d" in r["detail"]


def test_a_new_violation_after_a_re_observation_still_resets_the_streak(tmp_path):
    """The fix must not make the counter blind to the thing it is counting."""
    _stamp(tmp_path, days_ago=40)
    _reobservation(tmp_path, instance_days_ago=30, row_days_ago=1)
    _violation(tmp_path, days_ago=2)

    r = _one(_run_schema_violation_checks(state_dir=tmp_path, now=_NOW))
    assert r["status"] == "WARN"
    assert "2.0d" in r["detail"]


def test_rows_without_an_instance_are_never_de_duplicated(tmp_path):
    """Only a row that identifies its instance can be recognised as a repeat. Two
    identical-looking rows with no instance are two occurrences."""
    _stamp(tmp_path, days_ago=40)
    _violation(tmp_path, days_ago=5)
    _violation(tmp_path, days_ago=5)

    r = _one(_run_schema_violation_checks(state_dir=tmp_path, now=_NOW))
    assert "2 distinct violation(s)" in r["detail"]


def test_a_streak_past_seven_days_does_not_claim_the_gate_is_unmet(tmp_path):
    """With the streak keyed on instance.ts it routinely exceeds 7d while the stamp
    lags. Saying "cannot flip until it reaches 7d" at 30d would be exactly the kind
    of untrue health claim this family exists to prevent."""
    _stamp(tmp_path, days_ago=40)
    _reobservation(tmp_path, instance_days_ago=30, row_days_ago=1)

    r = _one(_run_schema_violation_checks(state_dir=tmp_path, now=_NOW))
    assert "cannot flip until it reaches 7d" not in r["detail"]
    assert "already past the 7d gate" in r["detail"]


def test_a_re_observation_still_never_returns_a_fail(tmp_path):
    _stamp(tmp_path, days_ago=40)
    _reobservation(tmp_path, instance_days_ago=30, row_days_ago=1)
    results = _run_schema_violation_checks(state_dir=tmp_path, now=_NOW)
    assert [r["status"] for r in results] == ["WARN"]


def test_two_distinct_violations_without_an_instance_ts_are_not_collapsed(tmp_path):
    """Regression: keying de-duplication on (sink, reflex_id) alone collapses every
    violation of one schema on one sink into a single occurrence, and drops the newest
    — hiding a brand-new violation behind an old date. That is this check's own
    failure mode, inverted. Only a real instance.ts identifies a repeat."""
    _stamp(tmp_path, days_ago=40)
    for cycle, days in (("a", 30), ("b", 1)):
        with (tmp_path / "schema_violations.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "event": "schema_violation", "sink": "SENSEI_LEDGER.jsonl",
                "observer": "reflex-engine:startup", "reflex_id": "r",
                "instance": {"cycle_id": cycle},          # no instance.ts
                "ts": (_NOW - timedelta(days=days)).isoformat(),
            }) + "\n")

    r = _one(_run_schema_violation_checks(state_dir=tmp_path, now=_NOW))
    assert "2 distinct violation(s)" in r["detail"]
    assert "re-observation" not in r["detail"]
    assert "1.0d" in r["detail"]


def test_an_unparseable_instance_ts_falls_back_to_the_row_ts(tmp_path):
    """`ts is not a string` is itself one of the violations this sink records, so a row
    whose instance.ts is a number must still be counted and dated. Dropping it reported
    a clean streak at the exact moment a real violation had just been observed."""
    _stamp(tmp_path, days_ago=40)
    with (tmp_path / "schema_violations.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "event": "schema_violation", "sink": "SENSEI_LEDGER.jsonl",
            "schema": "sensei_ledger_row",
            "violations": ["data.ts should be string"],
            "instance": {"ts": 1756800000000, "reflex_id": "r"},
            "ts": (_NOW - timedelta(hours=2)).isoformat(),
        }) + "\n")

    r = _one(_run_schema_violation_checks(state_dir=tmp_path, now=_NOW))
    assert r["status"] == "WARN"
    assert "1 distinct violation(s)" in r["detail"]
    assert "0.1d" in r["detail"]


def test_a_non_dict_json_line_does_not_abort_the_family(tmp_path):
    """`null`, `[]` and `"x"` are valid JSON and not rows. This is the FIRST family in
    doctor's registry, so an exception here aborts every later check in the run — and,
    under the launchd job, the state write with it."""
    _stamp(tmp_path, days_ago=40)
    (tmp_path / "schema_violations.jsonl").write_text(
        'null\n[]\n"x"\n123\n' + json.dumps({
            "event": "schema_violation", "sink": "S",
            "ts": (_NOW - timedelta(days=3)).isoformat()}) + "\n",
        encoding="utf-8")

    r = _one(_run_schema_violation_checks(state_dir=tmp_path, now=_NOW))
    assert r["status"] == "WARN"
    assert "1 distinct violation(s)" in r["detail"]
    assert "3.0d" in r["detail"]


def test_rows_sharing_one_ts_and_reflex_id_are_still_distinct_violations(tmp_path):
    """The live SENSEI_LEDGER sink holds several rows sharing one (instance.ts,
    reflex_id) pair, so that pair is not a row identity. Keying de-duplication on it
    would report genuinely different violations as one."""
    _stamp(tmp_path, days_ago=40)
    shared_ts = (_NOW - timedelta(days=10)).isoformat()
    for cycle, violation in (("a", "data.pillar bad"), ("b", "data.reflex_id bad")):
        with (tmp_path / "schema_violations.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "event": "schema_violation", "sink": "SENSEI_LEDGER.jsonl",
                "observer": "reflex-engine:startup", "violations": [violation],
                "instance": {"ts": shared_ts, "reflex_id": "metric:sword:Open_CVEs",
                             "cycle_id": cycle},
                "ts": (_NOW - timedelta(days=1)).isoformat(),
            }) + "\n")

    r = _one(_run_schema_violation_checks(state_dir=tmp_path, now=_NOW))
    assert "2 distinct violation(s)" in r["detail"]
    assert "re-observation" not in r["detail"]


def test_a_byte_identical_re_observation_is_still_folded(tmp_path):
    """The wider identity key must not stop folding the rows it was built for: a
    reflex-engine restart re-serialises an identical instance and violation list."""
    _stamp(tmp_path, days_ago=40)
    instance = {"ts": (_NOW - timedelta(days=10)).isoformat(), "reflex_id": "r",
                "cycle_id": "same"}
    for days in (10, 1):
        with (tmp_path / "schema_violations.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "event": "schema_violation", "sink": "SENSEI_LEDGER.jsonl",
                "observer": "reflex-engine:startup", "violations": ["data.pillar bad"],
                "instance": instance,
                "ts": (_NOW - timedelta(days=days)).isoformat(),
            }) + "\n")

    r = _one(_run_schema_violation_checks(state_dir=tmp_path, now=_NOW))
    assert "1 distinct violation(s)" in r["detail"]
    assert "1 re-observation(s)" in r["detail"]
    assert "10.0d" in r["detail"]


def test_a_tz_naive_instance_ts_is_graded_utc_rather_than_aborting_the_run(tmp_path):
    """A naive instance.ts produced a naive datetime that then compared against the
    aware clean-since stamp and raised TypeError out of this family — the FIRST in
    doctor's registry, so the whole run ended and, under the launchd job, the state
    file was never written. Same grading rule the stamp itself already uses."""
    _stamp(tmp_path, days_ago=40)
    with (tmp_path / "schema_violations.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "event": "schema_violation", "sink": "SENSEI_LEDGER.jsonl",
            "violations": ["data.pillar bad"],
            "instance": {"ts": (_NOW - timedelta(days=2)).replace(tzinfo=None).isoformat(),
                         "reflex_id": "r"},
            "ts": (_NOW - timedelta(days=1)).isoformat(),
        }) + "\n")

    r = _one(_run_schema_violation_checks(state_dir=tmp_path, now=_NOW))
    assert r["status"] == "WARN"
    assert "2.0d" in r["detail"]


def test_a_tz_naive_row_ts_is_graded_utc_too(tmp_path):
    _stamp(tmp_path, days_ago=40)
    with (tmp_path / "schema_violations.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "event": "schema_violation", "sink": "S", "violations": ["x"],
            "ts": (_NOW - timedelta(days=4)).replace(tzinfo=None).isoformat(),
        }) + "\n")

    r = _one(_run_schema_violation_checks(state_dir=tmp_path, now=_NOW))
    assert r["status"] == "WARN"
    assert "4.0d" in r["detail"]
