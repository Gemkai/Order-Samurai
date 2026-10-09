"""Mechanism_Liveness: by-design idle is a reported state, not a FAIL (2026-10-07).

After remediation-loops F4 the ReflexEngine writes no exec_log row on a cycle where
nothing is eligible, so the mechanism_run bridge stays silent by design. The reflex
loop still writes a "[starved] 0 eligible ..." heartbeat every cycle. Zero runs WITH
those heartbeats in the target week means "alive, correctly idle": the reducer reports
that state ungraded. Zero runs WITHOUT heartbeats keeps the 0 -> FAIL, because that is
the dead-loop signal this metric was graded to catch (2026-08-14 exec_log silence).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from agentica_core import aggregate as agg
from agentica_core import backfill_history, insights

# 2026-W41: Monday 2026-10-05 .. Sunday 2026-10-11.
_RECORDS = [{"timestamp": "2026-10-07T13:00:00Z"}]
_THIS_WEEK = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
_LAST_WEEK = _THIS_WEEK - timedelta(days=7)


def _ts(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


@pytest.fixture
def sources(tmp_path, monkeypatch):
    events = tmp_path / "autonomic_events.jsonl"
    heartbeat = tmp_path / "reflex_output.jsonl"
    events.write_text("", encoding="utf-8")
    monkeypatch.setattr(agg, "default_events_path", lambda: events)
    monkeypatch.setattr(agg, "_REFLEX_HEARTBEAT_LOG", heartbeat)
    monkeypatch.setattr(agg, "_REFLEX_EXEC_LOG", tmp_path / "exec_log.jsonl", raising=False)

    def write(path, rows):
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    return events, heartbeat, write


def _starved(dt: datetime) -> dict:
    return {"timestamp": _ts(dt), "metric": "reflex",
            "line": "[starved] 0 eligible with 6 CRITICAL/HIGH entries in the payload"}


def _graded(env: dict) -> dict:
    pillars = {"bow": {"Autonomic": {"Mechanism_Liveness": dict(env)}}}
    insights.annotate(pillars)
    return pillars["bow"]["Autonomic"]["Mechanism_Liveness"]


def test_zero_runs_with_starved_heartbeats_reports_idle_by_design(sources):
    _, heartbeat, write = sources
    write(heartbeat, [_starved(_THIS_WEEK), _starved(_THIS_WEEK + timedelta(hours=1))])

    out = agg._mechanism_liveness(_RECORDS)

    assert out["val"] is None
    assert out["state"] == "idle_by_design"
    assert out["data_gap"] is True
    assert "2 starved heartbeat" in out["detail"]
    graded = _graded(out)
    assert "status" not in graded and not graded.get("flagged")


def test_zero_runs_without_heartbeats_still_fails(sources):
    """The dead-loop case: no runs and no heartbeat this week stays a graded 0."""
    _, heartbeat, write = sources
    write(heartbeat, [_starved(_LAST_WEEK)])  # loop stopped before this week

    out = agg._mechanism_liveness(_RECORDS)

    assert out["val"] == 0
    assert "state" not in out
    assert _graded(out)["status"] == "FAIL"


def test_zero_runs_with_missing_heartbeat_file_still_fails(sources):
    out = agg._mechanism_liveness(_RECORDS)

    assert out["val"] == 0
    assert _graded(out)["status"] == "FAIL"


def test_non_starved_reflex_lines_do_not_count_as_idle(sources):
    _, heartbeat, write = sources
    write(heartbeat, [{"timestamp": _ts(_THIS_WEEK), "metric": "metric:arts:Slop_Density",
                       "line": "Slop-Density Triage"}])

    assert agg._mechanism_liveness(_RECORDS)["val"] == 0


def test_real_runs_are_counted_unchanged(sources):
    events, heartbeat, write = sources
    write(events, [{"event": "mechanism_run", "timestamp": _ts(_THIS_WEEK)}] * 3)
    write(heartbeat, [_starved(_THIS_WEEK)])

    out = agg._mechanism_liveness(_RECORDS)

    assert out["val"] == 3
    assert "state" not in out


def test_previous_week_runs_do_not_block_idle_this_week(sources):
    events, heartbeat, write = sources
    write(events, [{"event": "mechanism_run", "timestamp": _ts(_LAST_WEEK)}])
    write(heartbeat, [_starved(_THIS_WEEK)])

    assert agg._mechanism_liveness(_RECORDS)["state"] == "idle_by_design"


def test_same_week_engine_runs_without_bridged_events_still_fail(sources, tmp_path):
    """A run in exec_log that never became a mechanism_run is a broken bridge, not idle."""
    _, heartbeat, write = sources
    write(heartbeat, [_starved(_THIS_WEEK)])
    write(tmp_path / "exec_log.jsonl", [{"timestamp": _ts(_THIS_WEEK), "source": "reflex_engine",
                                         "skill": "pip-safe-upgrade"}])

    out = agg._mechanism_liveness(_RECORDS)

    assert out["val"] == 0
    assert _graded(out)["status"] == "FAIL"


def test_backfill_does_not_carry_a_number_into_an_idle_week(sources):
    key = "bow/Autonomic/Mechanism_Liveness"
    prior = [{"ts": "2026-09-28T00:00:00+00:00", "values": {key: 0}}]
    weekly = [{"ts": "2026-10-05T00:00:00+00:00", "week": "2026-W41", "values": {},
               "idle": [key]}]

    out = backfill_history._carry_forward(weekly, prior)

    assert key not in out[0]["values"]


def test_unreadable_exec_log_is_not_evidence_of_idle(sources, tmp_path, monkeypatch):
    _, heartbeat, write = sources
    write(heartbeat, [_starved(_THIS_WEEK)])
    unreadable = tmp_path / "exec_log.jsonl"
    unreadable.mkdir()  # reading a directory raises an OSError that is not "missing"
    monkeypatch.setattr(agg, "_REFLEX_EXEC_LOG", unreadable)

    out = agg._mechanism_liveness(_RECORDS)

    assert out["val"] == 0
    assert "state" not in out


_KEY = "bow/Autonomic/Mechanism_Liveness"


def test_rebuild_keeps_a_recorded_idle_week_when_heartbeats_rotated_away(sources):
    prior = [{"ts": "2026-10-05T00:00:00+00:00", "week": "2026-W41", "kind": "weekly",
              "values": {}, "idle": [_KEY]}]
    rebuilt = [{"ts": "2026-10-05T00:00:00+00:00", "week": "2026-W41", "kind": "weekly",
                "values": {_KEY: 0}}]

    out = backfill_history._carry_forward(rebuilt, prior)

    assert _KEY not in out[0]["values"]
    assert out[0]["idle"] == [_KEY]


def test_rebuild_keeps_a_recorded_idle_week_when_the_events_source_is_missing(sources):
    prior = [{"ts": "2026-09-28T00:00:00+00:00", "values": {_KEY: 0}},
             {"ts": "2026-10-05T00:00:00+00:00", "week": "2026-W41", "kind": "weekly",
              "values": {}, "idle": [_KEY]}]
    rebuilt = [{"ts": "2026-10-05T00:00:00+00:00", "week": "2026-W41", "kind": "weekly",
                "values": {}}]

    out = backfill_history._carry_forward(rebuilt, prior)

    assert _KEY not in out[0]["values"]


def test_real_runs_in_a_rebuild_supersede_a_recorded_idle_week(sources):
    prior = [{"ts": "2026-10-05T00:00:00+00:00", "week": "2026-W41", "kind": "weekly",
              "values": {}, "idle": [_KEY]}]
    rebuilt = [{"ts": "2026-10-05T00:00:00+00:00", "week": "2026-W41", "kind": "weekly",
                "values": {_KEY: 4}}]

    out = backfill_history._carry_forward(rebuilt, prior)

    assert out[0]["values"][_KEY] == 4
    assert "idle" not in out[0]


def test_same_week_engine_runs_override_a_recorded_idle_week(sources, tmp_path):
    """Recorded mid-week as idle, then an unbridged engine run landed: the rebuild's 0
    is a broken-bridge FAIL and must not be turned back into idle."""
    _, _, write = sources
    write(tmp_path / "exec_log.jsonl", [{"timestamp": _ts(_THIS_WEEK), "source": "reflex_engine"}])
    prior = [{"ts": "2026-10-05T00:00:00+00:00", "week": "2026-W41", "kind": "weekly",
              "values": {}, "idle": [_KEY]}]
    rebuilt = [{"ts": "2026-10-05T00:00:00+00:00", "week": "2026-W41", "kind": "weekly",
                "values": {_KEY: 0}}]

    out = backfill_history._carry_forward(rebuilt, prior)

    assert out[0]["values"][_KEY] == 0
    assert "idle" not in out[0]
