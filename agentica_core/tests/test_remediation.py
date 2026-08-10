"""Tests for remediation.py helper functions."""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest
from agentica_core import remediation as rem


# ---------------------------------------------------------------------------
# _load_history
# ---------------------------------------------------------------------------

def test_load_history_returns_sorted_by_time(tmp_path):
    path = tmp_path / "hist.jsonl"
    rows = [
        {"ts": "2026-01-10T00:00:00+00:00", "values": {"bow/Activity/Error_Rate": 0.5}},
        {"ts": "2026-01-05T00:00:00+00:00", "values": {"bow/Activity/Error_Rate": 0.3}},
        {"ts": "2026-01-01T00:00:00+00:00", "values": {"bow/Activity/Error_Rate": 0.1}},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    result = rem._load_history(path)
    assert len(result) == 3
    # Sorted ascending — earliest first
    assert result[0][1]["bow/Activity/Error_Rate"] == 0.1
    assert result[1][1]["bow/Activity/Error_Rate"] == 0.3
    assert result[2][1]["bow/Activity/Error_Rate"] == 0.5


def test_load_history_skips_bad_lines(tmp_path):
    path = tmp_path / "hist.jsonl"
    path.write_text(
        '{"ts": "2026-01-01T00:00:00+00:00", "values": {}}\nnot-json\n{"ts": "2026-01-02T00:00:00+00:00", "values": {}}\n',
        encoding="utf-8",
    )
    result = rem._load_history(path)
    assert len(result) == 2


def test_load_history_returns_empty_for_missing_file(tmp_path):
    result = rem._load_history(tmp_path / "nonexistent.jsonl")
    assert result == []


def test_load_history_skips_rows_with_unparseable_ts(tmp_path):
    path = tmp_path / "hist.jsonl"
    path.write_text(
        '{"ts": "not-a-date", "values": {"k": 1}}\n{"ts": "2026-01-01T00:00:00+00:00", "values": {"k": 2}}\n',
        encoding="utf-8",
    )
    result = rem._load_history(path)
    assert len(result) == 1
    assert result[0][1]["k"] == 2


# ---------------------------------------------------------------------------
# _skill_uses — telemetry records only (no exec_log dependency)
# ---------------------------------------------------------------------------

def _make_record(ts: str, skills: list[str]) -> dict:
    return {"timestamp": ts, "skills_used": skills, "status": "success"}


def test_skill_uses_extracts_from_telemetry_records(monkeypatch, tmp_path):
    monkeypatch.setattr(rem, "_EXEC_LOG", tmp_path / "no_exec_log.jsonl")
    records = [
        _make_record("2026-01-01T00:00:00+00:00", ["simplify", "investigate"]),
        _make_record("2026-01-02T00:00:00+00:00", ["simplify"]),
    ]
    uses = rem._skill_uses(records)
    assert "simplify" in uses
    assert len(uses["simplify"]) == 2
    assert all(actor == "human" for _, actor in uses["simplify"])


def test_skill_uses_sorts_by_timestamp(monkeypatch, tmp_path):
    monkeypatch.setattr(rem, "_EXEC_LOG", tmp_path / "no_exec_log.jsonl")
    records = [
        _make_record("2026-01-10T00:00:00+00:00", ["simplify"]),
        _make_record("2026-01-01T00:00:00+00:00", ["simplify"]),
    ]
    uses = rem._skill_uses(records)
    # Sorted ascending — earlier timestamp first
    t1, t2 = uses["simplify"][0][0], uses["simplify"][1][0]
    assert t1 < t2


def test_skill_uses_empty_records(monkeypatch, tmp_path):
    monkeypatch.setattr(rem, "_EXEC_LOG", tmp_path / "no_exec_log.jsonl")
    uses = rem._skill_uses([])
    assert uses == {}


def test_skill_uses_skips_records_without_skills_used(monkeypatch, tmp_path):
    monkeypatch.setattr(rem, "_EXEC_LOG", tmp_path / "no_exec_log.jsonl")
    records = [{"timestamp": "2026-01-01T00:00:00+00:00", "status": "success"}]
    uses = rem._skill_uses(records)
    assert uses == {}


def test_skill_uses_skips_records_with_no_timestamp(monkeypatch, tmp_path):
    monkeypatch.setattr(rem, "_EXEC_LOG", tmp_path / "no_exec_log.jsonl")
    records = [{"skills_used": ["simplify"]}]
    uses = rem._skill_uses(records)
    assert uses == {}


# ---------------------------------------------------------------------------
# efficacy — with controlled history and records (no platform load)
# ---------------------------------------------------------------------------

def test_efficacy_returns_none_success_rate_when_no_events(tmp_path):
    hist = tmp_path / "hist.jsonl"
    hist.write_text("", encoding="utf-8")
    result = rem.efficacy(history_path=hist, records=[], exec_log_path=tmp_path / "empty_exec.jsonl")
    assert result["applied"] == 0
    assert result["success_rate"] is None


def test_efficacy_returns_required_keys(tmp_path):
    hist = tmp_path / "hist.jsonl"
    hist.write_text("", encoding="utf-8")
    result = rem.efficacy(history_path=hist, records=[])
    assert set(result.keys()) >= {
        "applied", "improved", "regressed", "flat", "success_rate", "by_skill",
        "events", "proposed_count", "proposed_improved",
        "proposal_improvement_rate", "proposal_events", "human_correlated",
        "human_correlated_improved", "human_events", "note",
    }


def test_efficacy_note_mentions_correlation(tmp_path):
    hist = tmp_path / "hist.jsonl"
    hist.write_text("", encoding="utf-8")
    result = rem.efficacy(history_path=hist, records=[])
    assert "correlation" in result["note"].lower()


# ---------------------------------------------------------------------------
# efficacy dedup — keyed on snapshot windows, not values
# ---------------------------------------------------------------------------
# Error_Rate (dir=lower) is remediated by /investigate per insights.REMEDIATION.
# Flagged means health < 40, i.e. value at/past the fail threshold.

def _write_history(path: Path, rows: list[tuple[str, float]]) -> None:
    lines = [json.dumps({"ts": ts, "values": {"bow/Activity/Error_Rate": v}}) for ts, v in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _flagged_value() -> float:
    from agentica_core import insights
    return float(insights.METRIC_RULES["Error_Rate"]["fail"]) * 2


def test_efficacy_counts_repeat_uses_in_distinct_windows_with_equal_values(monkeypatch, tmp_path):
    monkeypatch.setattr(rem, "_EXEC_LOG", tmp_path / "no_exec_log.jsonl")
    hist = tmp_path / "hist.jsonl"
    bad = _flagged_value()
    # Two separate flag → use → recover cycles with IDENTICAL before/after values.
    _write_history(hist, [
        ("2026-01-01T00:00:00+00:00", bad),
        ("2026-01-02T00:00:00+00:00", 0.0),
        ("2026-01-03T00:00:00+00:00", bad),
        ("2026-01-04T00:00:00+00:00", 0.0),
    ])
    records = [
        _make_record("2026-01-01T12:00:00+00:00", ["investigate"]),
        _make_record("2026-01-03T12:00:00+00:00", ["investigate"]),
    ]
    result = rem.efficacy(history_path=hist, records=records)
    ev = [e for e in result["human_events"] if e["metric"] == "Error_Rate"]
    assert len(ev) == 2  # value-keyed dedup used to collapse these into one


def test_efficacy_attributes_window_to_reflex_when_exec_log_and_telemetry_both_record_it(monkeypatch, tmp_path):
    # One physical reflex-engine-fired run lands in exec_log (actor=reflex) AND in the
    # headless session's telemetry (actor=human). It must count ONCE, attributed to reflex.
    exec_log = tmp_path / "exec_log.jsonl"
    exec_log.write_text(json.dumps({
        "timestamp": "2026-01-01T12:00:00+00:00",
        "skill": "investigate",
        "status": "done",
        "source": "reflex_engine",
    }) + "\n", encoding="utf-8")
    monkeypatch.setattr(rem, "_EXEC_LOG", exec_log)
    hist = tmp_path / "hist.jsonl"
    bad = _flagged_value()
    _write_history(hist, [
        ("2026-01-01T00:00:00+00:00", bad),
        ("2026-01-02T00:00:00+00:00", 0.0),
    ])
    records = [_make_record("2026-01-01T12:00:05+00:00", ["investigate"])]  # telemetry echo
    result = rem.efficacy(history_path=hist, records=records)
    ev = [e for e in result["events"] if e["metric"] == "Error_Rate"]
    assert len(ev) == 1
    assert ev[0]["actor"] == "reflex"


# ---------------------------------------------------------------------------
# efficacy — attempt counting + fire-time before/after events (§A1)
# ---------------------------------------------------------------------------

def _exec_row(ts: str, status: str, skill: str = "investigate", **extra) -> dict:
    return {"timestamp": ts, "skill": skill, "status": status,
            "source": "reflex_engine", "command": f"/{skill}", **extra}


def _write_exec_log(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_efficacy_counts_non_done_runs_as_attempts(monkeypatch, tmp_path):
    exec_log = tmp_path / "exec_log.jsonl"
    _write_exec_log(exec_log, [
        _exec_row("2026-01-01T00:00:00+00:00", "no_change"),
        _exec_row("2026-01-02T00:00:00+00:00", "error"),
        _exec_row("2026-01-03T00:00:00+00:00", "timeout"),
        _exec_row("2026-01-04T00:00:00+00:00", "done"),
    ])
    monkeypatch.setattr(rem, "_EXEC_LOG", exec_log)
    hist = tmp_path / "hist.jsonl"
    hist.write_text("", encoding="utf-8")
    result = rem.efficacy(history_path=hist, records=[])
    assert result["attempted"] == 4
    assert result["completed"] == 1
    assert result["by_skill"]["investigate"]["attempted"] == 4


def test_efficacy_reports_zero_attempts_when_exec_log_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(rem, "_EXEC_LOG", tmp_path / "no_exec_log.jsonl")
    hist = tmp_path / "hist.jsonl"
    hist.write_text("", encoding="utf-8")
    result = rem.efficacy(history_path=hist, records=[])
    assert result["attempted"] == 0
    assert result["completed"] == 0


def test_efficacy_builds_event_from_fire_time_measurement_without_snapshots(monkeypatch, tmp_path):
    # No metrics_history rows at all — before/after captured at fire time is enough.
    exec_log = tmp_path / "exec_log.jsonl"
    _write_exec_log(exec_log, [
        _exec_row("2026-01-01T00:00:00+00:00", "done",
                  reflex_id="metric:bow:Error_Rate", metric_before=5.0, metric_after=1.0),
    ])
    monkeypatch.setattr(rem, "_EXEC_LOG", exec_log)
    hist = tmp_path / "hist.jsonl"
    hist.write_text("", encoding="utf-8")
    result = rem.efficacy(history_path=hist, records=[])
    ev = [e for e in result["events"] if e["metric"] == "Error_Rate"]
    assert len(ev) == 1
    assert ev[0]["outcome"] == "improved"  # Error_Rate dir=lower, 5.0 -> 1.0
    assert ev[0]["actor"] == "reflex"
    assert ev[0]["before"] == 5.0 and ev[0]["after"] == 1.0
    assert result["applied"] == 1 and result["improved"] == 1


def test_efficacy_keeps_proposal_only_measurement_out_of_live_results(monkeypatch, tmp_path):
    exec_log = tmp_path / "exec_log.jsonl"
    _write_exec_log(exec_log, [
        _exec_row(
            "2026-01-01T00:00:00+00:00",
            "done",
            reflex_id="metric:bow:Error_Rate",
            metric_before=5.0,
            metric_after=1.0,
            applied=False,
            propose_only=True,
        ),
    ])
    monkeypatch.setattr(rem, "_EXEC_LOG", exec_log)
    hist = tmp_path / "hist.jsonl"
    hist.write_text("", encoding="utf-8")

    result = rem.efficacy(history_path=hist, records=[])

    assert result["attempted"] == 0
    assert result["applied"] == 0
    assert result["improved"] == 0
    assert result["improvement_rate"] is None
    assert result["events"] == []
    assert result["proposed_count"] == 1
    assert result["proposed_improved"] == 1
    assert result["proposal_improvement_rate"] == 100.0
    assert result["proposal_events"][0]["outcome"] == "improved"


def test_efficacy_judges_fire_time_no_change_run_as_flat(monkeypatch, tmp_path):
    exec_log = tmp_path / "exec_log.jsonl"
    _write_exec_log(exec_log, [
        _exec_row("2026-01-01T00:00:00+00:00", "no_change",
                  reflex_id="metric:bow:Error_Rate", metric_before=5.0, metric_after=5.0),
    ])
    monkeypatch.setattr(rem, "_EXEC_LOG", exec_log)
    hist = tmp_path / "hist.jsonl"
    hist.write_text("", encoding="utf-8")
    result = rem.efficacy(history_path=hist, records=[])
    ev = [e for e in result["events"] if e["metric"] == "Error_Rate"]
    assert len(ev) == 1
    assert ev[0]["outcome"] == "flat"
    assert result["attempted"] == 1 and result["completed"] == 0


def test_efficacy_fire_time_row_is_not_double_counted_by_snapshot_correlation(monkeypatch, tmp_path):
    # A done run WITH fire-time values, also bracketed by snapshots: one event, not two.
    exec_log = tmp_path / "exec_log.jsonl"
    bad = _flagged_value()
    _write_exec_log(exec_log, [
        _exec_row("2026-01-01T12:00:00+00:00", "done",
                  reflex_id="metric:bow:Error_Rate", metric_before=bad, metric_after=0.0),
    ])
    monkeypatch.setattr(rem, "_EXEC_LOG", exec_log)
    hist = tmp_path / "hist.jsonl"
    _write_history(hist, [
        ("2026-01-01T00:00:00+00:00", bad),
        ("2026-01-02T00:00:00+00:00", 0.0),
    ])
    result = rem.efficacy(history_path=hist, records=[])
    ev = [e for e in result["events"] if e["metric"] == "Error_Rate"]
    assert len(ev) == 1


def test_efficacy_skips_fire_time_row_without_metric_rule(monkeypatch, tmp_path):
    exec_log = tmp_path / "exec_log.jsonl"
    _write_exec_log(exec_log, [
        _exec_row("2026-01-01T00:00:00+00:00", "done",
                  reflex_id="metric:bow:Not_A_Rule_Metric", metric_before=5.0, metric_after=1.0),
    ])
    monkeypatch.setattr(rem, "_EXEC_LOG", exec_log)
    hist = tmp_path / "hist.jsonl"
    hist.write_text("", encoding="utf-8")
    result = rem.efficacy(history_path=hist, records=[])
    assert result["applied"] == 0
    assert result["attempted"] == 1  # still an attempt, just not judgeable


def test_efficacy_dedupes_repeat_uses_within_the_same_snapshot_window(monkeypatch, tmp_path):
    monkeypatch.setattr(rem, "_EXEC_LOG", tmp_path / "no_exec_log.jsonl")
    hist = tmp_path / "hist.jsonl"
    bad = _flagged_value()
    _write_history(hist, [
        ("2026-01-01T00:00:00+00:00", bad),
        ("2026-01-02T00:00:00+00:00", 0.0),
    ])
    # Two sessions log the same use inside one snapshot window — count once.
    records = [
        _make_record("2026-01-01T11:00:00+00:00", ["investigate"]),
        _make_record("2026-01-01T12:00:00+00:00", ["investigate"]),
    ]
    result = rem.efficacy(history_path=hist, records=records)
    ev = [e for e in result["human_events"] if e["metric"] == "Error_Rate"]
    assert len(ev) == 1


def test_human_improvement_cannot_raise_autonomous_rate(monkeypatch, tmp_path):
    monkeypatch.setattr(rem, "_EXEC_LOG", tmp_path / "no_exec_log.jsonl")
    hist = tmp_path / "hist.jsonl"
    bad = _flagged_value()
    _write_history(hist, [
        ("2026-01-01T00:00:00+00:00", bad),
        ("2026-01-02T00:00:00+00:00", 0.0),
    ])
    records = [_make_record("2026-01-01T12:00:00+00:00", ["investigate"])]

    result = rem.efficacy(history_path=hist, records=records)

    assert result["attempted"] == 0
    assert result["improved"] == 0
    assert result["improvement_rate"] is None
    assert result["events"] == []
    assert result["human_correlated"] == 1
    assert result["human_correlated_improved"] == 1


# ---------------------------------------------------------------------------
# FIX A — windowed headline (2026-08-08 seq 9a)
# ---------------------------------------------------------------------------
# A lifetime count froze Self_Correction_Rate at one number: 18 days of ZERO
# autonomous attempts read exactly like a healthy engine. `now` is injected so no
# test ever depends on the wall clock.

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)


def _ts(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def _empty_history(tmp_path: Path) -> Path:
    hist = tmp_path / "hist.jsonl"
    hist.write_text("", encoding="utf-8")
    return hist


def test_efficacy_counts_only_attempts_inside_the_window(tmp_path):
    exec_log = tmp_path / "exec_log.jsonl"
    _write_exec_log(exec_log, [
        _exec_row(_ts(5), "done", reflex_id="metric:bow:Error_Rate"),
        _exec_row(_ts(10), "no_change", reflex_id="metric:bow:Error_Rate"),
        _exec_row(_ts(200), "done", reflex_id="metric:bow:Error_Rate"),   # outside 30d
        _exec_row(_ts(300), "done", reflex_id="metric:bow:Error_Rate"),   # outside 30d
    ])
    result = rem.efficacy(history_path=_empty_history(tmp_path), records=[],
                          exec_log_path=exec_log, window_days=30, now=NOW)
    assert result["attempted"] == 2
    assert result["completed"] == 1
    assert result["window_days"] == 30


def test_efficacy_keeps_lifetime_counts_alongside_the_window(tmp_path):
    exec_log = tmp_path / "exec_log.jsonl"
    _write_exec_log(exec_log, [
        _exec_row(_ts(5), "done", reflex_id="metric:bow:Error_Rate", improved=True),
        _exec_row(_ts(200), "done", reflex_id="metric:bow:Error_Rate", improved=True),
        _exec_row(_ts(300), "done", reflex_id="metric:bow:Error_Rate", improved=False),
    ])
    result = rem.efficacy(history_path=_empty_history(tmp_path), records=[],
                          exec_log_path=exec_log, window_days=30, now=NOW)
    assert (result["attempted"], result["improved"]) == (1, 1)
    assert (result["attempted_lifetime"], result["improved_lifetime"]) == (3, 2)
    assert result["completed_lifetime"] == 3
    assert result["applied_lifetime"] == 3


def test_efficacy_windowed_improvement_rate_is_the_in_window_ratio(tmp_path):
    exec_log = tmp_path / "exec_log.jsonl"
    rows = [_exec_row(_ts(1 + i), "done", reflex_id="metric:bow:Error_Rate",
                      improved=(i == 0)) for i in range(4)]
    rows += [_exec_row(_ts(100 + i), "done", reflex_id="metric:bow:Error_Rate",
                       improved=True) for i in range(6)]
    _write_exec_log(exec_log, rows)
    result = rem.efficacy(history_path=_empty_history(tmp_path), records=[],
                          exec_log_path=exec_log, window_days=30, now=NOW)
    assert result["improvement_rate"] == 25.0            # 1 of 4 in-window
    assert result["attempted_lifetime"] == 10            # lifetime would have said 70%


def test_efficacy_empty_window_reports_a_data_gap_not_a_zero(tmp_path):
    exec_log = tmp_path / "exec_log.jsonl"
    _write_exec_log(exec_log, [
        _exec_row(_ts(60), "done", reflex_id="metric:bow:Error_Rate", improved=True),
    ])
    result = rem.efficacy(history_path=_empty_history(tmp_path), records=[],
                          exec_log_path=exec_log, window_days=30, now=NOW)
    assert result["attempted"] == 0
    assert result["improvement_rate"] is None            # never a fabricated 0%
    assert result["data_gap"] is True
    assert result["data_gap_detail"] == "no autonomous attempts in 30d"
    assert result["attempted_lifetime"] == 1


def test_efficacy_without_a_window_stays_lifetime(tmp_path):
    exec_log = tmp_path / "exec_log.jsonl"
    _write_exec_log(exec_log, [
        _exec_row(_ts(5), "done", reflex_id="metric:bow:Error_Rate"),
        _exec_row(_ts(400), "done", reflex_id="metric:bow:Error_Rate"),
    ])
    result = rem.efficacy(history_path=_empty_history(tmp_path), records=[],
                          exec_log_path=exec_log)
    assert result["attempted"] == 2
    assert result["window_days"] is None
    assert result["data_gap"] is False


def test_window_kill_switch_restores_lifetime_counting(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDIATION_WINDOW", "false")
    exec_log = tmp_path / "exec_log.jsonl"
    _write_exec_log(exec_log, [
        _exec_row(_ts(5), "done", reflex_id="metric:bow:Error_Rate"),
        _exec_row(_ts(400), "done", reflex_id="metric:bow:Error_Rate"),
    ])
    result = rem.efficacy(history_path=_empty_history(tmp_path), records=[],
                          exec_log_path=exec_log, window_days=30, now=NOW)
    assert result["attempted"] == 2
    assert result["data_gap"] is False


# ---------------------------------------------------------------------------
# FIX B — tier 2, the engine's own `improved` verdict (2026-08-08 seq 9b)
# ---------------------------------------------------------------------------

def test_engine_verdict_rows_become_direct_improved_events(tmp_path):
    # Mirrors the live distribution: many autonomous rows carrying a boolean verdict,
    # a small minority true. The headline read 3 while the log recorded 11.
    exec_log = tmp_path / "exec_log.jsonl"
    rows = [_exec_row(_ts(1 + i), "done", skill="simplify",
                      reflex_id="metric:arts:Simplify_Age", improved=(i < 2))
            for i in range(10)]
    _write_exec_log(exec_log, rows)
    result = rem.efficacy(history_path=_empty_history(tmp_path), records=[],
                          exec_log_path=exec_log, window_days=30, now=NOW)
    assert result["applied"] == 10
    assert result["improved"] == 2
    assert result["flat"] == 8            # a false boolean is flat, never "regressed"
    assert result["regressed"] == 0
    assert all(e["evidence"] == "engine_verdict" for e in result["events"])


def test_engine_verdict_row_without_a_metric_scoped_reflex_id_still_counts(tmp_path):
    # correlation:* ids name no metric, but the engine's verdict is still real.
    exec_log = tmp_path / "exec_log.jsonl"
    _write_exec_log(exec_log, [
        _exec_row(_ts(1), "done", skill="model-selector",
                  reflex_id="correlation:cost_and_quality_tradeoff", improved=True),
    ])
    result = rem.efficacy(history_path=_empty_history(tmp_path), records=[],
                          exec_log_path=exec_log, window_days=30, now=NOW)
    assert result["improved"] == 1
    assert result["events"][0]["metric"] == "correlation:cost_and_quality_tradeoff"


def test_engine_verdict_never_counts_a_row_that_has_a_numeric_pair(tmp_path):
    # Double-count guard: one physical run with BOTH kinds of evidence is ONE event,
    # and the numeric measurement wins over the boolean (which disagrees here).
    exec_log = tmp_path / "exec_log.jsonl"
    _write_exec_log(exec_log, [
        _exec_row(_ts(1), "done", reflex_id="metric:bow:Error_Rate",
                  metric_before=5.0, metric_after=1.0, improved=False),
    ])
    result = rem.efficacy(history_path=_empty_history(tmp_path), records=[],
                          exec_log_path=exec_log, window_days=30, now=NOW)
    assert result["applied"] == 1
    assert result["improved"] == 1                       # numeric 5.0 -> 1.0 wins
    assert result["events"][0]["evidence"] == "fire_time"


def test_engine_verdict_row_is_excluded_from_snapshot_correlation(tmp_path):
    # The same run must not be counted again by the flag -> use -> next-snapshot path.
    bad = _flagged_value()
    exec_log = tmp_path / "exec_log.jsonl"
    _write_exec_log(exec_log, [
        _exec_row(_ts(2), "done", reflex_id="metric:bow:Error_Rate", improved=True),
    ])
    hist = tmp_path / "hist.jsonl"
    _write_history(hist, [(_ts(3), bad), (_ts(1), 0.0)])
    result = rem.efficacy(history_path=hist, records=[], exec_log_path=exec_log,
                          window_days=30, now=NOW)
    assert result["applied"] == 1
    assert result["events"][0]["evidence"] == "engine_verdict"


def test_engine_verdict_kill_switch_restores_two_tier_behaviour(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDIATION_ENGINE_VERDICT", "false")
    exec_log = tmp_path / "exec_log.jsonl"
    _write_exec_log(exec_log, [
        _exec_row(_ts(1), "done", reflex_id="metric:bow:Error_Rate", improved=True),
    ])
    result = rem.efficacy(history_path=_empty_history(tmp_path), records=[],
                          exec_log_path=exec_log, window_days=30, now=NOW)
    assert result["attempted"] == 1
    assert result["applied"] == 0        # no direct event, no snapshots to correlate
    assert result["improved"] == 0


def test_engine_verdict_ignores_proposal_only_and_readonly_channels(tmp_path):
    # A direct event must never sit outside the `attempted` denominator it divides into.
    exec_log = tmp_path / "exec_log.jsonl"
    _write_exec_log(exec_log, [
        _exec_row(_ts(1), "done", reflex_id="metric:bow:Error_Rate",
                  improved=True, propose_only=True),
        _exec_row(_ts(2), "done", reflex_id="metric:arts:Raw_Pending",
                  improved=True, kind="mechanism", read_only=True),
    ])
    result = rem.efficacy(history_path=_empty_history(tmp_path), records=[],
                          exec_log_path=exec_log, window_days=30, now=NOW)
    assert result["attempted"] == 0
    assert result["applied"] == 0
    assert result["improved"] == 0


def test_engine_verdict_row_from_a_human_dashboard_click_is_not_counted(tmp_path):
    exec_log = tmp_path / "exec_log.jsonl"
    row = _exec_row(_ts(1), "done", reflex_id="metric:bow:Error_Rate", improved=True)
    row["source"] = "dashboard"
    _write_exec_log(exec_log, [row])
    result = rem.efficacy(history_path=_empty_history(tmp_path), records=[],
                          exec_log_path=exec_log, window_days=30, now=NOW)
    assert result["attempted"] == 0
    assert result["improved"] == 0


# ---------------------------------------------------------------------------
# FIX B, settled-status gate — `improved` on an error/timeout row is the default
# written by a run that never finished, not a verdict. Grading it "flat" would
# claim the metric was observed and did not move.
# ---------------------------------------------------------------------------

def test_engine_verdict_does_not_grade_a_crashed_run(tmp_path):
    exec_log = tmp_path / "exec_log.jsonl"
    _write_exec_log(exec_log, [
        _exec_row(_ts(1), "error", reflex_id="metric:bow:Error_Rate", improved=False),
    ])
    result = rem.efficacy(history_path=_empty_history(tmp_path), records=[],
                          exec_log_path=exec_log, window_days=30, now=NOW)
    assert result["attempted"] == 1      # a crashed remediation IS a failed attempt
    assert result["applied"] == 0        # ...but it is not a measured event
    assert result["flat"] == 0
    assert result["events"] == []


def test_engine_verdict_does_not_grade_a_timed_out_run(tmp_path):
    exec_log = tmp_path / "exec_log.jsonl"
    _write_exec_log(exec_log, [
        _exec_row(_ts(1), "timeout", reflex_id="metric:bow:Error_Rate", improved=False),
    ])
    result = rem.efficacy(history_path=_empty_history(tmp_path), records=[],
                          exec_log_path=exec_log, window_days=30, now=NOW)
    assert result["attempted"] == 1
    assert result["applied"] == 0
    assert result["flat"] == 0


def test_engine_verdict_does_not_grade_a_crashed_run_claiming_improvement(tmp_path):
    # An unsettled run cannot certify a success either — the gate is on the run
    # reaching an outcome, not on which way the boolean happens to point.
    exec_log = tmp_path / "exec_log.jsonl"
    _write_exec_log(exec_log, [
        _exec_row(_ts(1), "error", reflex_id="metric:bow:Error_Rate", improved=True),
    ])
    result = rem.efficacy(history_path=_empty_history(tmp_path), records=[],
                          exec_log_path=exec_log, window_days=30, now=NOW)
    assert result["attempted"] == 1
    assert result["improved"] == 0
    assert result["applied"] == 0


def test_engine_verdict_grades_a_settled_no_change_run_as_flat(tmp_path):
    # no_change is a terminal outcome, not a crash: the engine ran to completion and
    # judged the metric unmoved. That IS a verdict and stays a graded event.
    exec_log = tmp_path / "exec_log.jsonl"
    _write_exec_log(exec_log, [
        _exec_row(_ts(1), "no_change", reflex_id="metric:bow:Error_Rate", improved=False),
    ])
    result = rem.efficacy(history_path=_empty_history(tmp_path), records=[],
                          exec_log_path=exec_log, window_days=30, now=NOW)
    assert result["applied"] == 1
    assert result["flat"] == 1
    assert result["events"][0]["evidence"] == "engine_verdict"


def test_engine_verdict_denominator_keeps_unsettled_runs_out_of_the_numerator(tmp_path):
    # Mixed distribution: the graded channel sees only the settled runs, while every
    # run — settled or not — stays in the improvement_rate denominator.
    exec_log = tmp_path / "exec_log.jsonl"
    _write_exec_log(exec_log, [
        _exec_row(_ts(1), "done", reflex_id="metric:bow:Error_Rate", improved=True),
        _exec_row(_ts(2), "no_change", reflex_id="metric:bow:Error_Rate", improved=False),
        _exec_row(_ts(3), "error", reflex_id="metric:bow:Error_Rate", improved=False),
        _exec_row(_ts(4), "timeout", reflex_id="metric:bow:Error_Rate", improved=False),
    ])
    result = rem.efficacy(history_path=_empty_history(tmp_path), records=[],
                          exec_log_path=exec_log, window_days=30, now=NOW)
    assert result["attempted"] == 4
    assert result["applied"] == 2
    assert result["improved"] == 1
    assert result["flat"] == 1
    assert result["improvement_rate"] == 25.0    # 1 improved of 4 attempts
    assert result["success_rate"] == 50.0        # 1 improved of 2 graded events
