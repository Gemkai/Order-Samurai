"""Tests for reflex_eureka helper functions and analyze() core logic."""
import json
import pytest

from agentica_core import reflex_eureka as eur


@pytest.fixture(autouse=True)
def _runtime_home_under_tmp(tmp_path, monkeypatch):
    """analyze() exports classified findings into the Claude runtime home as a
    side-effect (the lesson pipeline globs ~/.claude/.tmp/intelligence/). Point
    that at tmp for every test here: before this fixture the suite wrote into the
    REAL ~/.claude of every machine that ran it, and on the public export's CI
    runner that half-present home made doctor report a dead telemetry emitter."""
    home = tmp_path / "claude-home"
    monkeypatch.setenv("CLAUDE_RUNTIME_ROOT", str(home))
    return home


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


def test_analyze_zero_pct_effective_is_gotcha(tmp_path):
    # 0/5 improvement rate (0%) is the WORST offender — a skill that NEVER resolves
    # its metric — and must classify as GOTCHA, not be silently excluded because a
    # falsy 0.0 rate falls through to the `or 1` fallback in the classifier filter.
    entries = [
        {"source": "reflex_engine", "reflex_id": "metric:arts:Slop_Density",
         "skill": "humanizer", "improved": False, "timestamp": f"2026-01-0{i+1}T00:00:00Z"}
        for i in range(5)
    ]
    log = _write_log(tmp_path, entries)
    out = tmp_path / "findings.md"
    result = eur.analyze(log, out)
    assert result["gotchas"] == 1
    assert result["rules"] == 0
    assert result["context"] == 0


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
    eur.analyze(log, out)
    content = out.read_text()
    assert "subagent-audit" in content


def test_analyze_returns_summary_dict_keys(tmp_path):
    log = _write_log(tmp_path, [])
    out = tmp_path / "findings.md"
    result = eur.analyze(log, out)
    assert set(result.keys()) == {"total_entries", "skill_metric_pairs", "gotchas", "rules", "context"}


# ---------------------------------------------------------------------------
# side-effects stay inside the runtime home (export gate, 2026-09-06)
# ---------------------------------------------------------------------------

def test_notice_only_analyze_does_not_create_the_runtime_home(tmp_path, _runtime_home_under_tmp):
    """An analyze() with nothing to classify writes only its own out_path; it must
    not mkdir ~/.claude/data as a side-effect (that is what left a half-present
    home behind on every machine that ran this suite)."""
    out = tmp_path / "findings.md"
    eur.analyze(tmp_path / "nonexistent.jsonl", out)
    assert out.exists()
    assert not _runtime_home_under_tmp.exists()


def test_classified_findings_export_under_the_runtime_root(tmp_path, _runtime_home_under_tmp):
    """The lesson-pipeline export honours CLAUDE_RUNTIME_ROOT, so a redirected
    home receives the export and the real ~/.claude is never touched."""
    # Same shape as test_analyze_gotcha_classification: 0/10 improved -> GOTCHA.
    entries = [
        {"source": "reflex_engine", "reflex_id": "metric:arts:Slop_Density",
         "skill": "humanizer", "improved": False, "timestamp": f"2026-01-0{i+1}T00:00:00Z"}
        for i in range(10)
    ]
    log = _write_log(tmp_path, entries)
    out = tmp_path / "findings.md"
    result = eur.analyze(log, out)
    assert result["gotchas"] >= 1
    exported = list((_runtime_home_under_tmp / ".tmp" / "intelligence").glob("auto_eureka_*.md"))
    assert len(exported) == 1
    assert (_runtime_home_under_tmp / "data" / "auto_eureka_skills_export.json").exists()
