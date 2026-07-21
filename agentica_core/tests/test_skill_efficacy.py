"""Tests for skill_efficacy compute() function."""
import json
import pytest
from agentica_core import skill_efficacy as eff


def _log(tmp_path, entries: list[dict]) -> "tuple[object, object]":
    """Write exec_log.jsonl and return (log_path, out_path)."""
    log = tmp_path / "exec_log.jsonl"
    log.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")
    return log, tmp_path / "skill_efficacy.json"


# ---------------------------------------------------------------------------
# Warmup period
# ---------------------------------------------------------------------------

def test_warmup_fewer_than_min_runs_returns_aggressive_multiplier(tmp_path):
    log, out = _log(tmp_path, [
        {"skill": "simplify", "improved": True, "status": "done"},
        {"skill": "simplify", "improved": True, "status": "done"},
    ])
    result = eff.compute(log, out)
    assert result["simplify"]["cooldown_multiplier"] == eff._WARMUP_MULTIPLIER


def test_warmup_single_run(tmp_path):
    log, out = _log(tmp_path, [{"skill": "investigate", "improved": False, "status": "done"}])
    result = eff.compute(log, out)
    assert result["investigate"]["cooldown_multiplier"] == eff._WARMUP_MULTIPLIER


# ---------------------------------------------------------------------------
# Low success rate → penalty multiplier
# ---------------------------------------------------------------------------

def test_low_success_rate_applies_penalty(tmp_path):
    # 1 success out of 5 = 20% < 30% threshold → penalty
    entries = [{"skill": "wiki", "improved": (i == 0), "status": "done"} for i in range(5)]
    log, out = _log(tmp_path, entries)
    result = eff.compute(log, out)
    assert result["wiki"]["cooldown_multiplier"] == eff._MULTIPLIER
    assert result["wiki"]["success_rate"] == pytest.approx(0.2, abs=0.01)


def test_success_rate_at_threshold_is_normal(tmp_path):
    # 3 successes out of 10 = 30% — exactly at threshold, should NOT penalize
    entries = [{"skill": "simplify", "improved": (i < 3), "status": "done"} for i in range(10)]
    log, out = _log(tmp_path, entries)
    result = eff.compute(log, out)
    assert result["simplify"]["cooldown_multiplier"] == 1


def test_high_success_rate_is_normal(tmp_path):
    entries = [{"skill": "humanizer", "improved": True, "status": "done"} for _ in range(5)]
    log, out = _log(tmp_path, entries)
    result = eff.compute(log, out)
    assert result["humanizer"]["cooldown_multiplier"] == 1
    assert result["humanizer"]["success_rate"] == 1.0


# ---------------------------------------------------------------------------
# improved vs status fallback
# ---------------------------------------------------------------------------

def test_explicit_improved_false_counts_as_failure(tmp_path):
    entries = [{"skill": "guard", "improved": False, "status": "done"} for _ in range(10)]
    log, out = _log(tmp_path, entries)
    result = eff.compute(log, out)
    assert result["guard"]["success_count"] == 0
    assert result["guard"]["cooldown_multiplier"] == eff._MULTIPLIER


def test_status_done_fallback_when_no_improved_field(tmp_path):
    entries = [{"skill": "guard", "status": "done"} for _ in range(5)]
    log, out = _log(tmp_path, entries)
    result = eff.compute(log, out)
    # status=done → True fallback
    assert result["guard"]["success_count"] == 5


def test_status_error_fallback(tmp_path):
    entries = [{"skill": "guard", "status": "error"} for _ in range(5)]
    log, out = _log(tmp_path, entries)
    result = eff.compute(log, out)
    assert result["guard"]["success_count"] == 0


# ---------------------------------------------------------------------------
# Skill name extraction from command
# ---------------------------------------------------------------------------

def test_skill_name_from_command_string(tmp_path):
    entries = [{"command": "/simplify some args", "improved": True, "status": "done"} for _ in range(5)]
    log, out = _log(tmp_path, entries)
    result = eff.compute(log, out)
    assert "simplify" in result


def test_skill_field_takes_precedence_over_command(tmp_path):
    entries = [{"skill": "humanizer", "command": "/simplify", "improved": True} for _ in range(5)]
    log, out = _log(tmp_path, entries)
    result = eff.compute(log, out)
    assert "humanizer" in result
    assert "simplify" not in result


# ---------------------------------------------------------------------------
# Window capping
# ---------------------------------------------------------------------------

def test_window_cap_uses_only_last_n_runs(tmp_path):
    # First 20 entries fail, last 20 succeed — with window=20 only successes count
    entries = (
        [{"skill": "wiki", "improved": False} for _ in range(20)] +
        [{"skill": "wiki", "improved": True}  for _ in range(20)]
    )
    log, out = _log(tmp_path, entries)
    result = eff.compute(log, out)
    # reversed() means newest (successes) are read first; window stops at 20 → 100% success
    assert result["wiki"]["success_rate"] == 1.0
    assert result["wiki"]["total_runs"] == eff._WINDOW


# ---------------------------------------------------------------------------
# Missing / empty log
# ---------------------------------------------------------------------------

def test_missing_log_returns_empty(tmp_path):
    out = tmp_path / "skill_efficacy.json"
    result = eff.compute(tmp_path / "nonexistent.jsonl", out)
    assert result == {}


def test_output_file_written(tmp_path):
    log, out = _log(tmp_path, [{"skill": "simplify", "improved": True}])
    eff.compute(log, out)
    assert out.exists()
    data = json.loads(out.read_text())
    assert "simplify" in data
