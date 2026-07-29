"""Weakness mining: clusters must be earned, and gaps must stay honest.

The properties that carry the loop's safety: an unattributable record never becomes a data point,
a one-off never becomes a cluster, and two runs that share a terminal cause but differ in agent
behaviour never merge (they would need different fixes).
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin import weakness_mining_scout as scout  # type: ignore[import-not-found]


def _iso(hours_ago=1.0):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")


def _write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def _exec_row(skill="simplify", status="error", metric="metric:arts:Rework_Loops", hours_ago=1.0):
    return {
        "timestamp": _iso(hours_ago),
        "command": f"claude /{skill}",
        "skill": skill,
        "status": status,
        "improved": False,
        "code_modifying": True,
        "kind": "skill",
        "source": "reflex_engine",
        "reflex_id": metric,
    }


def _output_row(metric="metric:arts:Rework_Loops", line="exploring the repo again", hours_ago=1.0):
    return {"timestamp": _iso(hours_ago), "metric": metric, "line": line}


def _stub(mechanism="unbounded_exploration", causal="causal"):
    """Judge stub: the mechanism judge and the causal judge share one generate_fn, so answer by
    which label set the prompt's system instruction advertises."""
    def gen(**kwargs):
        labels = kwargs.get("system_instruction", "")
        if "causal" in labels and "incidental" in labels:
            return json.dumps({"label": causal, "explanation": "agent behaviour drove it"})
        return json.dumps({"label": mechanism, "explanation": "kept exploring without editing"})
    return gen


# --- terminal cause: derived, not judged -----------------------------------------------------

def test_derives_timeout_cause_from_status():
    assert scout.terminal_cause(_exec_row(status="timeout")) == "timeout"


def test_derives_quota_cause_from_detail_text():
    row = _exec_row()
    row["detail"] = "Claude Code CLI usage limit reached"
    assert scout.terminal_cause(row) == "quota"


def test_defaults_to_error_cause_when_detail_is_absent():
    assert scout.terminal_cause(_exec_row()) == "error"


# --- windowing / loading ---------------------------------------------------------------------

def test_loads_only_failed_runs(tmp_path):
    log = _write_jsonl(tmp_path / "exec_log.jsonl", [
        _exec_row(status="done"),
        _exec_row(status="no_change"),
        _exec_row(status="error"),
        _exec_row(status="timeout"),
    ])
    assert [r["status"] for r in scout.load_failures(7, log)] == ["error", "timeout"]


def test_excludes_failures_outside_the_day_window(tmp_path):
    log = _write_jsonl(tmp_path / "exec_log.jsonl", [
        _exec_row(hours_ago=24 * 30),
        _exec_row(hours_ago=1),
    ])
    assert len(scout.load_failures(7, log)) == 1


def test_returns_empty_when_exec_log_absent(tmp_path):
    assert scout.load_failures(7, tmp_path / "nope.jsonl") == []


def test_skips_torn_line_instead_of_raising(tmp_path):
    log = tmp_path / "exec_log.jsonl"
    log.write_text(json.dumps(_exec_row()) + "\n" + '{"timestamp": "trunc\n', encoding="utf-8")
    assert len(scout.load_failures(7, log)) == 1


# --- trace reconstruction --------------------------------------------------------------------

def test_trace_includes_only_lines_at_or_before_the_failure(tmp_path):
    """Output written after the row belongs to a later run, not the one that failed."""
    out = _write_jsonl(tmp_path / "reflex_output.jsonl", [
        _output_row(line="during the run", hours_ago=1.1),
        _output_row(line="after the run", hours_ago=0),
    ])
    traces = scout.load_traces(7, out)
    text = scout.trace_for(_exec_row(hours_ago=1), traces)
    assert "during the run" in text
    assert "after the run" not in text


def test_trace_is_empty_for_a_metric_with_no_captured_output(tmp_path):
    out = _write_jsonl(tmp_path / "reflex_output.jsonl", [_output_row(metric="metric:other:X", hours_ago=2)])
    traces = scout.load_traces(7, out)
    assert scout.trace_for(_exec_row(hours_ago=1), traces) == ""


def test_trace_excludes_lines_from_an_earlier_run_of_the_same_metric(tmp_path):
    """reflex_output rows carry no run id. Without a lookback bound, a previous run's output
    would be spliced into this run's trace and attribute a mechanism it never exhibited."""
    out = _write_jsonl(tmp_path / "reflex_output.jsonl", [
        _output_row(line="previous run output", hours_ago=48),
        _output_row(line="this run output", hours_ago=1.05),
    ])
    traces = scout.load_traces(7, out)
    text = scout.trace_for(_exec_row(hours_ago=1), traces)
    assert "this run output" in text
    assert "previous run output" not in text


# --- attribution honesty ---------------------------------------------------------------------

def test_does_not_attribute_a_record_with_no_trace():
    sig = scout.attribute(
        _exec_row(), "", scout.build_mechanism_judge(_stub()), scout.build_causal_judge(_stub())
    )
    assert sig is None


def test_does_not_attribute_when_the_judge_output_is_unparseable():
    bad = lambda **kw: "I think it probably looped a bit"  # noqa: E731 — not JSON
    sig = scout.attribute(
        _exec_row(), "some trace", scout.build_mechanism_judge(bad), scout.build_causal_judge(bad)
    )
    assert sig is None


def test_does_not_attribute_when_the_judge_invents_a_label():
    invented = lambda **kw: json.dumps({"label": "vibes_were_off", "explanation": "x"})  # noqa: E731
    sig = scout.attribute(
        _exec_row(), "some trace",
        scout.build_mechanism_judge(invented), scout.build_causal_judge(invented),
    )
    assert sig is None


def test_attributes_a_record_with_a_trace_and_valid_judgments():
    sig = scout.attribute(
        _exec_row(), "kept exploring",
        scout.build_mechanism_judge(_stub()), scout.build_causal_judge(_stub()),
    )
    assert sig is not None
    assert sig["mechanism"] == "unbounded_exploration"
    assert sig["causal_status"] == "causal"
    assert sig["terminal_cause"] == "error"


# --- clustering ------------------------------------------------------------------------------

def _sig(mechanism="unbounded_exploration", cause="error", causal="causal", skill="simplify"):
    return {
        "terminal_cause": cause, "causal_status": causal, "mechanism": mechanism,
        "_explanation": "kept exploring", "_skill": skill, "_reflex_id": "metric:arts:Rework_Loops",
        "_timestamp": _iso(), "_harness_fingerprint": "abc123",
    }


def test_groups_identical_signatures_into_one_cluster():
    clusters = scout.cluster([_sig(), _sig(), _sig()])
    assert len(clusters) == 1
    assert clusters[0]["count"] == 3


def test_keeps_same_terminal_cause_apart_when_the_mechanism_differs():
    """Both time out, but a retry loop and a truncated context need different fixes."""
    clusters = scout.cluster([
        _sig(mechanism="identical_retry", cause="timeout"),
        _sig(mechanism="context_truncation", cause="timeout"),
    ])
    assert len(clusters) == 2


def test_a_one_off_is_not_actionable():
    clusters = scout.cluster([_sig()])
    assert clusters[0]["actionable"] is False
    assert "not yet recurrent" in clusters[0]["actionability_reason"]


def test_recurrent_and_addressable_mechanism_is_actionable():
    clusters = scout.cluster([_sig() for _ in range(3)])
    assert clusters[0]["actionable"] is True
    assert "loop_breaker_limit" in clusters[0]["candidate_surface_keys"]


def test_recurrent_but_unaddressable_mechanism_is_not_actionable():
    clusters = scout.cluster([_sig(mechanism="missing_artifact") for _ in range(5)])
    assert clusters[0]["actionable"] is False
    assert "surface needs widening" in clusters[0]["actionability_reason"]


def test_incidental_failures_are_never_actionable_however_often_they_recur():
    clusters = scout.cluster([_sig(causal="incidental") for _ in range(10)])
    assert clusters[0]["actionable"] is False
    assert "environmental" in clusters[0]["actionability_reason"]


def test_ranks_actionable_clusters_above_larger_unactionable_ones():
    sigs = [_sig(mechanism="missing_artifact") for _ in range(9)] + [_sig() for _ in range(3)]
    clusters = scout.cluster(sigs)
    assert clusters[0]["actionable"] is True
    assert clusters[0]["signature"]["mechanism"] == "unbounded_exploration"


# --- end to end ------------------------------------------------------------------------------

def test_run_scout_reports_unattributed_records_rather_than_dropping_them(tmp_path):
    log = _write_jsonl(tmp_path / "exec_log.jsonl", [_exec_row(hours_ago=1)])
    out = _write_jsonl(tmp_path / "reflex_output.jsonl", [])  # no trace -> unattributable
    payload = scout.run_scout(7, generate_fn=_stub(), exec_log=log, output_log=out)

    assert payload["records_failed"] == 1
    assert payload["records_attributed"] == 0
    assert payload["records_unattributed"] == 1
    assert payload["clusters"] == []


def test_run_scout_separates_missing_traces_from_judge_gaps(tmp_path):
    """A coverage gap and a judgment gap have different fixes; one number would hide which."""
    log = _write_jsonl(tmp_path / "exec_log.jsonl", [_exec_row(hours_ago=1), _exec_row(hours_ago=2)])
    out = _write_jsonl(tmp_path / "reflex_output.jsonl", [_output_row(hours_ago=1.05)])
    unparseable = lambda **kw: "not json at all"  # noqa: E731

    payload = scout.run_scout(7, generate_fn=unparseable, exec_log=log, output_log=out)

    assert payload["unattributed_no_trace"] == 1   # the 2-hours-ago run has no captured output
    assert payload["unattributed_judge_gap"] == 1  # the 1-hour-ago run has a trace, judge failed
    assert payload["trace_coverage"] == 0.5


def test_run_scout_produces_an_actionable_cluster_from_recurring_failures(tmp_path):
    rows = [_exec_row(hours_ago=h) for h in (1, 2, 3)]
    log = _write_jsonl(tmp_path / "exec_log.jsonl", rows)
    out = _write_jsonl(tmp_path / "reflex_output.jsonl", [
        _output_row(line="exploring", hours_ago=h + 0.1) for h in (1, 2, 3)
    ])
    payload = scout.run_scout(7, generate_fn=_stub(), exec_log=log, output_log=out)

    assert payload["records_attributed"] == 3
    assert payload["actionable_count"] == 1
    assert payload["top_cluster_support"] == 3


def test_run_scout_reports_zero_top_support_when_nothing_is_actionable(tmp_path):
    log = _write_jsonl(tmp_path / "exec_log.jsonl", [_exec_row(hours_ago=1)])
    out = _write_jsonl(tmp_path / "reflex_output.jsonl", [_output_row(hours_ago=1.1)])
    payload = scout.run_scout(7, generate_fn=_stub(), exec_log=log, output_log=out)
    assert payload["top_cluster_support"] == 0


def test_every_mechanism_has_an_addressability_entry():
    """A mechanism with no entry would silently read as unaddressable."""
    assert set(scout.MECHANISMS) == set(scout.ADDRESSABLE)
