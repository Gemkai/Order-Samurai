"""Tests for the governance opt-in grant maturity ladder.

Verification matrix (per the plan's Phase 3 acceptance):
  - APPLY seed + no grading data        → APPLY / ready=true
  - APPLY seed + GOTCHA grade           → demoted to OBSERVE / ready=false
  - APPLY seed + RULE grade             → stays APPLY / ready=true
  - APPLY seed + ERROR (H2) grade       → demoted to OBSERVE / ready=false
  - OBSERVE seed + mechanism ungraded   → DRY-RUN-GRADED / ready=false
  - OBSERVE seed + RULE grade           → promoted to APPLY / ready=true
  - LLM-only metric (no mechanism)      → mechanism_status='no_mechanism'
"""
from agentica_core import maturity as m


def _metric(skill: str, *, seed: str = m.APPLY, with_mechanism: bool = True) -> dict:
    cfg: dict = {"skill": skill, "maturity": seed}
    if with_mechanism:
        cfg["mechanism"] = {"script": "x.py", "args": [], "read_only": True, "timeout_s": 60}
    return {"X_Metric": cfg}


# ---------------------------------------------------------------------------
# Seed preservation (Phase 1 backward-compat: no grading data ⇒ APPLY/ready)
# ---------------------------------------------------------------------------

def test_apply_seed_no_data_stays_apply():
    out = m.resolve_maturity("X_Metric",
                             metric_config=_metric("foo"),
                             efficacy={})
    assert out["maturity"] == m.APPLY
    assert out["reflex_ready"] is True
    assert out["mechanism_status"] == m.MECH_UNGRADED


def test_apply_seed_no_mechanism_stays_apply():
    out = m.resolve_maturity("X_Metric",
                             metric_config=_metric("foo", with_mechanism=False),
                             efficacy={})
    assert out["maturity"] == m.APPLY
    assert out["mechanism_status"] == m.MECH_NONE


def test_unknown_metric_returns_legacy_apply():
    out = m.resolve_maturity("NotInConfig", metric_config={}, efficacy={})
    assert out["maturity"] == m.APPLY  # legacy default
    assert out["reflex_ready"] is True
    assert out["mechanism_status"] == m.MECH_NONE


# ---------------------------------------------------------------------------
# Grading paths
# ---------------------------------------------------------------------------

def test_apply_seed_demoted_to_observe_on_gotcha():
    # 6 runs, 1 success (rate 0.167 < 0.30) ⇒ GOTCHA ⇒ demote.
    efficacy = {"foo::mechanism": {"total_runs": 6, "success_count": 1, "success_rate": 0.167}}
    out = m.resolve_maturity("X_Metric",
                             metric_config=_metric("foo"),
                             efficacy=efficacy)
    assert out["maturity"] == m.OBSERVE
    assert out["reflex_ready"] is False
    assert out["mechanism_status"] == m.MECH_GOTCHA


def test_apply_seed_stays_on_rule():
    # 10 runs, 8 success (rate 0.80 >= 0.70) ⇒ RULE ⇒ APPLY/ready.
    efficacy = {"foo::mechanism": {"total_runs": 10, "success_count": 8, "success_rate": 0.8}}
    out = m.resolve_maturity("X_Metric",
                             metric_config=_metric("foo"),
                             efficacy=efficacy)
    assert out["maturity"] == m.APPLY
    assert out["reflex_ready"] is True
    assert out["mechanism_status"] == m.MECH_RULE


def test_neutral_rate_preserves_seed():
    # rate 0.50 in [0.30, 0.70) ⇒ NEUTRAL ⇒ seed wins.
    efficacy = {"foo::mechanism": {"total_runs": 6, "success_count": 3, "success_rate": 0.50}}
    out = m.resolve_maturity("X_Metric",
                             metric_config=_metric("foo", seed=m.APPLY),
                             efficacy=efficacy)
    assert out["maturity"] == m.APPLY
    assert out["mechanism_status"] == m.MECH_NEUTRAL


# ---------------------------------------------------------------------------
# H2 — distinct mechanism_status="error" for total-failure mechanisms
# ---------------------------------------------------------------------------

def test_h2_error_state_when_zero_successes_with_minimum_runs():
    # 5 runs, 0 success — mechanism never improves the metric (e.g.
    # canary_fault_detect.py exiting 1). Must surface as ERROR, not GOTCHA,
    # so operators see "broken" rather than "underperforming".
    efficacy = {"foo::mechanism": {"total_runs": 5, "success_count": 0, "success_rate": 0.0}}
    out = m.resolve_maturity("X_Metric",
                             metric_config=_metric("foo"),
                             efficacy=efficacy)
    assert out["mechanism_status"] == m.MECH_ERROR
    assert out["maturity"] == m.OBSERVE
    assert out["reflex_ready"] is False


def test_low_run_count_stays_ungraded_not_error():
    # 2 runs, 0 success — too few to call it broken (< _MIN_RUNS).
    efficacy = {"foo::mechanism": {"total_runs": 2, "success_count": 0, "success_rate": 0.0}}
    out = m.resolve_maturity("X_Metric",
                             metric_config=_metric("foo"),
                             efficacy=efficacy)
    assert out["mechanism_status"] == m.MECH_UNGRADED
    assert out["maturity"] == m.APPLY  # seed preserved


# ---------------------------------------------------------------------------
# Promotion from a non-APPLY seed
# ---------------------------------------------------------------------------

def test_observe_seed_promoted_to_apply_on_rule():
    efficacy = {"foo::mechanism": {"total_runs": 10, "success_count": 9, "success_rate": 0.9}}
    out = m.resolve_maturity("X_Metric",
                             metric_config=_metric("foo", seed=m.OBSERVE),
                             efficacy=efficacy)
    assert out["maturity"] == m.APPLY  # promoted
    assert out["reflex_ready"] is True


def test_observe_seed_with_ungraded_mechanism_becomes_dry_run_graded():
    out = m.resolve_maturity("X_Metric",
                             metric_config=_metric("foo", seed=m.OBSERVE),
                             efficacy={})
    assert out["maturity"] == m.DRY_RUN_GRADED
    assert out["reflex_ready"] is False
    assert out["mechanism_status"] == m.MECH_UNGRADED
