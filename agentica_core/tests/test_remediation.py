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
    assert set(result.keys()) >= {"applied", "improved", "regressed", "flat", "success_rate", "by_skill", "events", "note"}


def test_efficacy_note_mentions_correlation(tmp_path):
    hist = tmp_path / "hist.jsonl"
    hist.write_text("", encoding="utf-8")
    result = rem.efficacy(history_path=hist, records=[])
    assert "correlation" in result["note"].lower()


# ---------------------------------------------------------------------------
# efficacy — attempt counting + fire-time before/after events (§A1)
# ---------------------------------------------------------------------------

def _write_history(path: Path, rows: list[tuple[str, float]]) -> None:
    lines = [json.dumps({"ts": ts, "values": {"bow/Activity/Error_Rate": v}}) for ts, v in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _flagged_value() -> float:
    from agentica_core import insights
    return float(insights.METRIC_RULES["Error_Rate"]["fail"]) * 2


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
    assert ev[0]["actor"] == "ronin"
    assert ev[0]["before"] == 5.0 and ev[0]["after"] == 1.0
    assert result["applied"] == 1 and result["improved"] == 1


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


