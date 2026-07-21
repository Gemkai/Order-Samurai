"""Tests for skill_no_impact analysis module."""
import json
from agentica_core import skill_no_impact as sni


# ---------------------------------------------------------------------------
# get_failure_mode
# ---------------------------------------------------------------------------

def test_skill_metric_override_takes_precedence():
    assert sni.get_failure_mode("simplify", "Rework_Loops") == "behavioral"


def test_skill_metric_override_auto_fixable():
    assert sni.get_failure_mode("simplify", "Simplify_Age") == "auto_fixable"


def test_skill_level_fallback_used_when_no_per_metric_override():
    assert sni.get_failure_mode("wiki", "Doc_Parity_Issues") == "accumulation"


def test_unknown_skill_defaults_to_auto_fixable():
    assert sni.get_failure_mode("nonexistent-skill", "SomeMetric") == "auto_fixable"


def test_audit_only_skill():
    assert sni.get_failure_mode("security-audit", "Secrets_Detected") == "audit_only"


def test_behavioral_skill():
    assert sni.get_failure_mode("model-selector", "Local_Routing_Share") == "behavioral"


# ---------------------------------------------------------------------------
# get_recommendation
# ---------------------------------------------------------------------------

def test_specific_recommendation_returned():
    rec = sni.get_recommendation("simplify", "Rework_Loops")
    assert "workflow" in rec.lower()  # the specific entry mentions "workflow"


def test_default_recommendation_returned_for_unknown_metric():
    rec = sni.get_recommendation("wiki", "SomeOtherMetric")
    assert "default" not in rec  # default value should be prose, not the key word
    assert len(rec) > 30  # non-trivial content


def test_generic_fallback_for_unknown_skill_and_metric():
    rec = sni.get_recommendation("mystery-skill", "SomeMetric")
    assert "mystery-skill" in rec
    assert "SomeMetric" in rec
    assert str(sni.LOOP_BREAKER_LIMIT) in rec


def test_recommendation_for_all_known_skills():
    for skill in sni.RECOMMENDATIONS:
        rec = sni.get_recommendation(skill, "NonExistentMetric")
        assert len(rec) > 20, f"Recommendation for {skill} is too short"


# ---------------------------------------------------------------------------
# analyze — no state / empty
# ---------------------------------------------------------------------------

def test_analyze_returns_empty_when_state_file_missing(tmp_path):
    result = sni.analyze(tmp_path / "exec_log.jsonl", tmp_path / "nostate.json")
    assert result == []


def test_analyze_returns_empty_when_no_stuck_entries(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"noImprovement": {"metric:bow:Error_Rate::/investigate": {"stuck": False}}}))
    result = sni.analyze(tmp_path / "exec_log.jsonl", state)
    assert result == []


def test_analyze_returns_empty_for_empty_no_improvement(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"noImprovement": {}}))
    result = sni.analyze(tmp_path / "exec_log.jsonl", state)
    assert result == []


# ---------------------------------------------------------------------------
# analyze — stuck entries
# ---------------------------------------------------------------------------

def _write_state(tmp_path, stuck_keys: list[str]) -> "object":
    state = tmp_path / "state.json"
    state.write_text(json.dumps({
        "noImprovement": {k: {"stuck": True} for k in stuck_keys}
    }))
    return state


def _write_log(tmp_path, entries: list[dict]) -> "object":
    log = tmp_path / "exec_log.jsonl"
    log.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")
    return log


def test_analyze_stuck_entry_has_correct_fields(tmp_path):
    state = _write_state(tmp_path, ["metric:bow:Error_Rate::/investigate"])
    log = _write_log(tmp_path, [
        {"reflex_id": "metric:bow:Error_Rate", "command": "/investigate", "improved": False,
         "status": "error", "timestamp": "2026-06-11T00:00:00Z"},
        {"reflex_id": "metric:bow:Error_Rate", "command": "/investigate", "improved": False,
         "status": "error", "timestamp": "2026-06-11T01:00:00Z"},
    ])
    result = sni.analyze(log, state)
    assert len(result) == 1
    r = result[0]
    assert r["reflex_id"] == "metric:bow:Error_Rate"
    assert r["pillar"] == "bow"
    assert r["metric"] == "Error_Rate"
    assert r["skill"] == "investigate"
    assert r["command"] == "/investigate"
    assert r["runs_attempted"] == 2
    assert r["improved_count"] == 0
    assert r["impact_rate"] == 0.0
    assert r["last_status"] == "error"
    assert "investigate" in r["unstick_endpoint"] or "Error_Rate" in r["unstick_endpoint"]


def test_analyze_impact_rate_computed_correctly(tmp_path):
    state = _write_state(tmp_path, ["metric:arts:Slop_Density::/humanizer"])
    log = _write_log(tmp_path, [
        {"reflex_id": "metric:arts:Slop_Density", "command": "/humanizer", "improved": True},
        {"reflex_id": "metric:arts:Slop_Density", "command": "/humanizer", "improved": False},
        {"reflex_id": "metric:arts:Slop_Density", "command": "/humanizer", "improved": False},
        {"reflex_id": "metric:arts:Slop_Density", "command": "/humanizer", "improved": False},
    ])
    result = sni.analyze(log, state)
    assert len(result) == 1
    assert result[0]["impact_rate"] == 0.25
    assert result[0]["improved_count"] == 1


def test_analyze_no_log_file_gives_none_impact_rate(tmp_path):
    state = _write_state(tmp_path, ["metric:bow:Error_Rate::/investigate"])
    result = sni.analyze(tmp_path / "missing_log.jsonl", state)
    assert len(result) == 1
    assert result[0]["runs_attempted"] == 0
    assert result[0]["impact_rate"] is None


def test_analyze_failure_mode_injected(tmp_path):
    state = _write_state(tmp_path, ["metric:arts:Rework_Loops::/simplify"])
    result = sni.analyze(tmp_path / "no_log.jsonl", state)
    assert result[0]["failure_mode"] == "behavioral"


def test_analyze_recommendation_injected(tmp_path):
    state = _write_state(tmp_path, ["metric:arts:Slop_Density::/humanizer"])
    result = sni.analyze(tmp_path / "no_log.jsonl", state)
    assert len(result[0]["recommendation"]) > 30


def test_analyze_skips_malformed_key(tmp_path):
    state = _write_state(tmp_path, ["badkey", "metric:bow:Error_Rate::/investigate"])
    result = sni.analyze(tmp_path / "no_log.jsonl", state)
    # "badkey" splits to 1 part → skipped; valid entry survives
    assert len(result) == 1
    assert result[0]["metric"] == "Error_Rate"


def test_analyze_creates_backlog_ticket(tmp_path):
    # Set up directory structure under tmp_path
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    
    state = state_dir / "state.json"
    state.write_text(json.dumps({
        "noImprovement": {"metric:bow:Error_Rate::/investigate": {"stuck": True}}
    }))
    
    log = state_dir / "exec_log.jsonl"
    log.write_text(json.dumps({
        "reflex_id": "metric:bow:Error_Rate", "command": "/investigate", "improved": False,
        "status": "error", "timestamp": "2026-06-11T00:00:00Z"
    }), encoding="utf-8")
    
    # Create the dummy bushido_check.py
    bushido_check = bin_dir / "bushido_check.py"
    bushido_content = """import sys
import json
from pathlib import Path
Path("bushido_check_args.json").write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
"""
    bushido_check.write_text(bushido_content, encoding="utf-8")
    
    result = sni.analyze(log, state)
    assert len(result) == 1
    
    # Check that dummy script was called and captured the args
    args_file = tmp_path / "bushido_check_args.json"
    assert args_file.exists()
    args = json.loads(args_file.read_text(encoding="utf-8"))
    
    assert "--skill" in args
    assert "investigate" in args
    assert "--pillar" in args
    assert "bow" in args
    assert "--metric" in args
    assert "metric:bow:Error_Rate" in args
    assert "--stuck" in args
    assert "--command" in args
    assert "/investigate" in args

