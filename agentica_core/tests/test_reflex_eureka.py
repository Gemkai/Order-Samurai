"""Tests for reflex_eureka helper functions and analyze() core logic."""
import json
from agentica_core import reflex_eureka as eur


# ---------------------------------------------------------------------------
# _was_effective
# ---------------------------------------------------------------------------

def test_was_effective_uses_improved_true():
    assert eur._was_effective({"improved": True, "status": "error"}) is True


def test_was_effective_uses_improved_false():
    assert eur._was_effective({"improved": False, "status": "done"}) is False


def test_was_effective_fallback_status_done():
    assert eur._was_effective({"status": "done"}) is True


def test_was_effective_fallback_status_error():
    assert eur._was_effective({"status": "error"}) is False


def test_was_effective_prefers_improved_over_status():
    assert eur._was_effective({"improved": False, "status": "done"}) is False


# ---------------------------------------------------------------------------
# _parse_reflex_id
# ---------------------------------------------------------------------------

def test_parse_reflex_id_standard_format():
    pillar, metric = eur._parse_reflex_id("metric:bow:Error_Rate")
    assert pillar == "bow"
    assert metric == "Error_Rate"


def test_parse_reflex_id_trajectory_type():
    pillar, metric = eur._parse_reflex_id("trajectory:arts:Slop_Density")
    assert pillar == "arts"
    assert metric == "Slop_Density"


def test_parse_reflex_id_malformed_too_few_parts():
    pillar, metric = eur._parse_reflex_id("only_one")
    assert pillar == "unknown"
    assert metric == "only_one"


def test_parse_reflex_id_only_two_parts():
    pillar, metric = eur._parse_reflex_id("metric:bow")
    assert pillar == "unknown"


# ---------------------------------------------------------------------------
# analyze — empty log
# ---------------------------------------------------------------------------

def test_analyze_empty_log_writes_notice(tmp_path):
    log = tmp_path / "exec_log.jsonl"
    log.write_text("", encoding="utf-8")
    out = tmp_path / "findings.md"
    result = eur.analyze(log, out)
    assert result["total_entries"] == 0
    assert result["gotchas"] == 0
    assert out.exists()
    content = out.read_text()
    assert "No reflex engine exec_log entries found" in content


def test_analyze_missing_log_writes_notice(tmp_path):
    out = tmp_path / "findings.md"
    result = eur.analyze(tmp_path / "nonexistent.jsonl", out)
    assert result["total_entries"] == 0
    assert out.exists()


# ---------------------------------------------------------------------------
# analyze — with data
# ---------------------------------------------------------------------------

def _write_log(tmp_path, entries: list[dict]) -> "object":
    log = tmp_path / "exec_log.jsonl"
    log.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")
    return log


def test_analyze_ignores_non_reflex_engine_entries(tmp_path):
    log = _write_log(tmp_path, [
        {"source": "manual", "reflex_id": "metric:bow:Error_Rate", "improved": True},
        {"source": "reflex_engine", "reflex_id": "metric:bow:Error_Rate", "improved": True},
    ])
    out = tmp_path / "findings.md"
    result = eur.analyze(log, out)
    assert result["total_entries"] == 1  # only reflex_engine entry counted


def test_analyze_gotcha_classification(tmp_path):
    # 1/10 improvement rate (10%) < 30% threshold → GOTCHA
    entries = [
        {"source": "reflex_engine", "reflex_id": "metric:arts:Slop_Density",
         "skill": "humanizer", "improved": i == 0, "timestamp": f"2026-01-0{i+1}T00:00:00Z"}
        for i in range(10)
    ]
    log = _write_log(tmp_path, entries)
    out = tmp_path / "findings.md"
    result = eur.analyze(log, out)
    assert result["gotchas"] == 1
    assert result["rules"] == 0
    assert "GOTCHA" in out.read_text()


def test_analyze_rule_classification(tmp_path):
    # 8/10 improvement rate (80%) >= 70% → RULE
    entries = [
        {"source": "reflex_engine", "reflex_id": "metric:arts:Slop_Density",
         "skill": "humanizer", "improved": i < 8, "timestamp": f"2026-01-{str(i+1).zfill(2)}T00:00:00Z"}
        for i in range(10)
    ]
    log = _write_log(tmp_path, entries)
    out = tmp_path / "findings.md"
    result = eur.analyze(log, out)
    assert result["rules"] == 1
    assert result["gotchas"] == 0
    assert "RULE" in out.read_text()


def test_analyze_context_classification(tmp_path):
    # 5/10 improvement rate (50%) — between 30%-70% → CONTEXT
    entries = [
        {"source": "reflex_engine", "reflex_id": "metric:bow:Error_Rate",
         "skill": "investigate", "improved": i < 5, "timestamp": f"2026-01-{str(i+1).zfill(2)}T00:00:00Z"}
        for i in range(10)
    ]
    log = _write_log(tmp_path, entries)
    out = tmp_path / "findings.md"
    result = eur.analyze(log, out)
    assert result["context"] == 1
    assert result["gotchas"] == 0
    assert result["rules"] == 0


def test_analyze_below_min_runs_not_classified(tmp_path):
    # Only 3 runs — less than _MIN_RUNS (5) → not in gotcha/rule/context
    entries = [
        {"source": "reflex_engine", "reflex_id": "metric:arts:Slop_Density",
         "skill": "humanizer", "improved": False, "timestamp": "2026-01-01T00:00:00Z"}
        for _ in range(3)
    ]
    log = _write_log(tmp_path, entries)
    out = tmp_path / "findings.md"
    result = eur.analyze(log, out)
    assert result["gotchas"] == 0
    assert result["rules"] == 0
    assert result["context"] == 0
    assert result["skill_metric_pairs"] == 1


def test_analyze_skill_name_extracted_from_command(tmp_path):
    entries = [
        {"source": "reflex_engine", "reflex_id": "metric:brush:Chain_Depth_Avg",
         "command": "/subagent-audit", "improved": True,
         "timestamp": f"2026-01-{str(i+1).zfill(2)}T00:00:00Z"}
        for i in range(10)
    ]
    log = _write_log(tmp_path, entries)
    out = tmp_path / "findings.md"
    result = eur.analyze(log, out)
    content = out.read_text()
    assert "subagent-audit" in content


def test_analyze_returns_summary_dict_keys(tmp_path):
    log = _write_log(tmp_path, [])
    out = tmp_path / "findings.md"
    result = eur.analyze(log, out)
    assert set(result.keys()) == {"total_entries", "skill_metric_pairs", "gotchas", "rules", "context"}
