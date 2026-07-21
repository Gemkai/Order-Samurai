"""Tests for metrics added recently with no dedicated coverage:
Gate_Canary_Fault, Local_Routing_Share, Canary_Failures (graded),
Avg_Session_Turns (graded). (Loop_Breaker_Fires retired 2026-07-19 —
dead emitter, metric-surface review Part E item 3.)
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from agentica_core import aggregate as agg, scouts
from agentica_core.insights import _health


# ── helpers ──────────────────────────────────────────────────────────────────

def _sig(tmp_path, **files):
    """Write data files and return security_signals(tmp_path)."""
    d = tmp_path / "data"
    d.mkdir(exist_ok=True)
    for name, content in files.items():
        (d / name).write_text(json.dumps(content), encoding="utf-8")
    return scouts.security_signals(tmp_path)


# ── Local_Routing_Share ───────────────────────────────────────────────────────

class TestLocalRoutingShare:
    def test_mixed_tiers_returns_correct_percentage(self):
        recs = [
            {"model_tier": "LOCAL"},
            {"model_tier": "LOCAL"},
            {"model_tier": "FAST"},
            {"model_tier": "FREE"},
        ]
        assert agg.r_local_routing(recs) == 50.0

    def test_all_local_returns_100(self):
        recs = [{"model_tier": "LOCAL"}, {"model_tier": "local"}]
        assert agg.r_local_routing(recs) == 100.0

    def test_no_local_returns_0(self):
        recs = [{"model_tier": "FAST"}, {"model_tier": "FREE"}]
        assert agg.r_local_routing(recs) == 0.0

    def test_empty_records_returns_none(self):
        assert agg.r_local_routing([]) is None

    def test_records_without_model_tier_returns_none(self):
        assert agg.r_local_routing([{"status": "success"}]) is None

    def test_local_tier_case_insensitive(self):
        recs = [{"model_tier": "local"}, {"model_tier": "FAST"}]
        assert agg.r_local_routing(recs) == 50.0


# ── Avg_Session_Turns ─────────────────────────────────────────────────────────

class TestAvgSessionTurns:
    def test_two_sessions_returns_average(self):
        recs = [
            {"session_id": "s1"}, {"session_id": "s1"}, {"session_id": "s1"},
            {"session_id": "s2"}, {"session_id": "s2"},
        ]
        # s1=3 turns, s2=2 turns → avg=2.5
        assert agg.r_avg_session_turns(recs) == 2.5

    def test_single_session_returns_turn_count(self):
        recs = [{"session_id": "s1"}] * 4
        assert agg.r_avg_session_turns(recs) == 4.0

    def test_empty_records_returns_none(self):
        assert agg.r_avg_session_turns([]) is None

    def test_records_without_session_id_returns_none(self):
        assert agg.r_avg_session_turns([{"status": "success"}]) is None

    def test_graded_healthy_below_warn(self):
        rule = {"dir": "lower", "warn": 8, "fail": 15}
        # 5 turns → well below warn=8 → should score 100
        assert _health(5.0, rule) == 100.0

    def test_graded_at_fail_threshold(self):
        rule = {"dir": "lower", "warn": 8, "fail": 15}
        # 15 turns → at fail=15 → should score ~40
        score = _health(15.0, rule)
        assert score == pytest.approx(40.0, abs=1.0)

    def test_graded_between_warn_and_fail(self):
        rule = {"dir": "lower", "warn": 8, "fail": 15}
        score = _health(11.5, rule)  # midpoint
        assert 40.0 < score < 100.0


# ── Canary_Failures (graded) ──────────────────────────────────────────────────

class TestCanaryFailures:
    def test_missing_file_absent_from_output(self, tmp_path):
        sig = scouts.security_signals(tmp_path)
        assert "canary_failures" not in sig

    def test_zero_failures_returns_0(self, tmp_path):
        sig = _sig(tmp_path, **{"canary_status.json": {"failed": 0}})
        assert sig["canary_failures"] == 0

    def test_positive_failures_counted(self, tmp_path):
        sig = _sig(tmp_path, **{"canary_status.json": {"failed": 3}})
        assert sig["canary_failures"] == 3

    def test_all_harness_faults_suppressed(self, tmp_path):
        # Every canary failed to even execute (harness/spawn fault, e.g. exit 0xC0000142):
        # not a skill verdict → metric must stay SIMULATED (absent), not a false all-fail.
        sig = _sig(tmp_path, **{"canary_status.json": {
            "total": 5, "passed": 0, "failed": 0, "could_not_run": 5}})
        assert "canary_failures" not in sig

    def test_partial_harness_fault_still_counts_real_failures(self, tmp_path):
        # Some canaries ran; report the genuine skill failures among them.
        sig = _sig(tmp_path, **{"canary_status.json": {
            "total": 5, "passed": 2, "failed": 1, "could_not_run": 2}})
        assert sig["canary_failures"] == 1

    def test_graded_zero_failures_scores_100(self):
        rule = {"dir": "lower", "warn": 0, "fail": 1}
        assert _health(0.0, rule) == 100.0

    def test_graded_one_failure_scores_at_most_40(self):
        rule = {"dir": "lower", "warn": 0, "fail": 1}
        assert _health(1.0, rule) <= 40.0

    def test_graded_large_count_scores_0(self):
        rule = {"dir": "lower", "warn": 0, "fail": 1}
        assert _health(10.0, rule) == 0.0


# ── Gate_Canary_Fault ─────────────────────────────────────────────────────────

class TestGateCanaryFault:
    def test_missing_file_absent_from_output(self, tmp_path):
        sig = scouts.security_signals(tmp_path)
        assert "gate_canary_fault" not in sig

    def test_working_fresh_canary_returns_0(self, tmp_path):
        now = datetime.now(timezone.utc).isoformat()
        sig = _sig(tmp_path, **{
            "security_gate_canary.json": {
                "gate_working": True,
                "last_run": now,
                "max_age_days": 7,
            }
        })
        assert sig["gate_canary_fault"] == 0

    def test_gate_not_working_returns_1(self, tmp_path):
        now = datetime.now(timezone.utc).isoformat()
        sig = _sig(tmp_path, **{
            "security_gate_canary.json": {
                "gate_working": False,
                "last_run": now,
                "max_age_days": 7,
            }
        })
        assert sig["gate_canary_fault"] == 1

    def test_stale_canary_returns_1(self, tmp_path):
        stale = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        sig = _sig(tmp_path, **{
            "security_gate_canary.json": {
                "gate_working": True,
                "last_run": stale,
                "max_age_days": 7,
            }
        })
        assert sig["gate_canary_fault"] == 1

    def test_fresh_within_budget_returns_0(self, tmp_path):
        recent = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        sig = _sig(tmp_path, **{
            "security_gate_canary.json": {
                "gate_working": True,
                "last_run": recent,
                "max_age_days": 7,
            }
        })
        assert sig["gate_canary_fault"] == 0

    def test_exactly_at_budget_boundary_passes(self, tmp_path):
        # days > max_age triggers fault; days == max_age does not
        exact = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        sig = _sig(tmp_path, **{
            "security_gate_canary.json": {
                "gate_working": True,
                "last_run": exact,
                "max_age_days": 7,
            }
        })
        assert sig["gate_canary_fault"] == 0


# Loop_Breaker_Fires tests removed 2026-07-19 with the metric's retirement
# (dead emitter — see agentica_core/insights.py retirement comment).


# ── False-zero prevention (Task 2 bug fixes) ──────────────────────────────────

class TestReducerFalseZero:
    def test_total_cost_returns_none_when_field_absent(self):
        recs = [{"status": "success"}, {"status": "error"}]
        assert agg.r_total_cost(recs) is None

    def test_token_spend_returns_none_when_fields_absent(self):
        recs = [{"status": "success"}, {"status": "error"}]
        assert agg.r_token_spend(recs) is None

    def test_cost_per_task_returns_none_when_cost_absent(self):
        recs = [{"status": "success"}, {"status": "error"}]
        assert agg.r_cost_per_task(recs) is None

    def test_total_cost_returns_value_when_present(self):
        recs = [{"total_cost": 0.01}, {"total_cost": 0.02}]
        assert agg.r_total_cost(recs) == pytest.approx(0.03, abs=1e-6)

    def test_token_spend_returns_value_when_present(self):
        recs = [{"tokens_prompt": 100, "tokens_completion": 50}]
        assert agg.r_token_spend(recs) == 150
