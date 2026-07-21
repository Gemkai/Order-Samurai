"""Tests for cross-metric correlation engine."""
from agentica_core import correlation


def _pillars(overrides: dict) -> dict:
    """Build a minimal pillar dict with all groups present as empty dicts, then apply overrides.

    overrides: {(pillar, group, metric): {"val": ..., "is_simulated": False}}
    """
    base: dict = {
        "bow":   {"Activity": {}, "Governance": {}},
        "sword": {"Vulnerability": {}, "Code Security": {}},
        "brush": {"Token Efficiency": {}},
        "arts":  {"Interaction": {}, "Output Quality": {}, "Docs": {}, "Knowledge": {}},
    }
    for (pk, gk, mk), env in overrides.items():
        base[pk][gk][mk] = env
    return base


# ---------------------------------------------------------------------------
# _get_val
# ---------------------------------------------------------------------------

def test_get_val_returns_float():
    p = _pillars({("bow", "Activity", "Error_Rate"): {"val": "0.15", "is_simulated": False}})
    assert correlation._get_val(p, "bow", "Activity", "Error_Rate") == 0.15


def test_get_val_strips_percent():
    p = _pillars({("bow", "Governance", "Governance_Pass_Rate"): {"val": "75%", "is_simulated": False}})
    assert correlation._get_val(p, "bow", "Governance", "Governance_Pass_Rate") == 75.0


def test_get_val_returns_none_for_dash():
    p = _pillars({("bow", "Activity", "Error_Rate"): {"val": "—", "is_simulated": False}})
    assert correlation._get_val(p, "bow", "Activity", "Error_Rate") is None


def test_get_val_returns_none_for_simulated():
    p = _pillars({("bow", "Activity", "Error_Rate"): {"val": "0.9", "is_simulated": True}})
    assert correlation._get_val(p, "bow", "Activity", "Error_Rate") is None


def test_get_val_returns_none_for_missing_metric():
    p = _pillars({})
    assert correlation._get_val(p, "bow", "Activity", "Error_Rate") is None


# ---------------------------------------------------------------------------
# evaluate — ops_and_security_degraded
# ---------------------------------------------------------------------------

def test_ops_security_fires_when_both_elevated():
    p = _pillars({
        ("bow",   "Activity",     "Error_Rate"):          {"val": "0.2",  "is_simulated": False},
        ("sword", "Vulnerability","Vulnerability_MTTR"):   {"val": "3.0",  "is_simulated": False},
    })
    fired = correlation.evaluate(p)
    ids = {r["id"] for r in fired}
    assert "correlation:ops_and_security_degraded" in ids


def test_ops_security_does_not_fire_when_only_one_elevated():
    p = _pillars({
        ("bow",   "Activity",     "Error_Rate"):          {"val": "0.2",  "is_simulated": False},
        ("sword", "Vulnerability","Vulnerability_MTTR"):   {"val": "1.0",  "is_simulated": False},
    })
    fired = correlation.evaluate(p)
    ids = {r["id"] for r in fired}
    assert "correlation:ops_and_security_degraded" not in ids


def test_ops_security_does_not_fire_when_simulated():
    p = _pillars({
        ("bow",   "Activity",     "Error_Rate"):          {"val": "0.9",  "is_simulated": True},
        ("sword", "Vulnerability","Vulnerability_MTTR"):   {"val": "99.0", "is_simulated": True},
    })
    fired = correlation.evaluate(p)
    assert fired == []


# ---------------------------------------------------------------------------
# evaluate — cost_and_quality_tradeoff
# ---------------------------------------------------------------------------

def test_cost_quality_fires_when_both_elevated():
    # Frustration_Signals is per-session (SENSEI-6): the fixture carries a
    # Session_Count so 5 signals / 1 session = 5.0/session > 2 genuinely fires.
    p = _pillars({
        ("brush", "Token Efficiency", "Total_Cost"):          {"val": "60",  "is_simulated": False},
        ("arts",  "Interaction",      "Frustration_Signals"): {"val": "5",   "is_simulated": False},
        ("bow",   "Activity",         "Session_Count"):       {"val": "1",   "is_simulated": False},
    })
    fired = correlation.evaluate(p)
    ids = {r["id"] for r in fired}
    assert "correlation:cost_and_quality_tradeoff" in ids


def test_cost_quality_does_not_fire_when_cost_low():
    p = _pillars({
        ("brush", "Token Efficiency", "Total_Cost"):          {"val": "10",  "is_simulated": False},
        ("arts",  "Interaction",      "Frustration_Signals"): {"val": "10",  "is_simulated": False},
    })
    fired = correlation.evaluate(p)
    ids = {r["id"] for r in fired}
    assert "correlation:cost_and_quality_tradeoff" not in ids


# ---------------------------------------------------------------------------
# evaluate — governance_and_security_gap
# ---------------------------------------------------------------------------

def test_governance_security_fires_when_both_degraded():
    p = _pillars({
        ("bow",   "Governance",   "Governance_Pass_Rate"):  {"val": "60",  "is_simulated": False},
        ("sword", "Code Security","Boundary_Violations"):   {"val": "2",   "is_simulated": False},
    })
    fired = correlation.evaluate(p)
    ids = {r["id"] for r in fired}
    assert "correlation:governance_and_security_gap" in ids


def test_governance_security_does_not_fire_when_governance_ok():
    p = _pillars({
        ("bow",   "Governance",   "Governance_Pass_Rate"):  {"val": "95",  "is_simulated": False},
        ("sword", "Code Security","Boundary_Violations"):   {"val": "5",   "is_simulated": False},
    })
    fired = correlation.evaluate(p)
    ids = {r["id"] for r in fired}
    assert "correlation:governance_and_security_gap" not in ids


# ---------------------------------------------------------------------------
# evaluate — result shape
# ---------------------------------------------------------------------------

def test_fired_reflex_has_required_fields():
    p = _pillars({
        ("bow",   "Activity",     "Error_Rate"):          {"val": "0.5",  "is_simulated": False},
        ("sword", "Vulnerability","Vulnerability_MTTR"):   {"val": "5.0",  "is_simulated": False},
    })
    fired = correlation.evaluate(p)
    r = next(f for f in fired if f["id"] == "correlation:ops_and_security_degraded")
    assert r["tier"] == "CRITICAL"
    assert r["source"] == "correlation_engine"
    assert r["status"] == "active"
    assert r["command"]
    assert r["message"]
    assert r["category"] == "correlation"


def test_evaluate_returns_empty_when_no_conditions_met():
    assert correlation.evaluate(_pillars({})) == []


def test_multiple_rules_can_fire_simultaneously():
    p = _pillars({
        ("bow",   "Activity",     "Error_Rate"):          {"val": "0.5",  "is_simulated": False},
        ("sword", "Vulnerability","Vulnerability_MTTR"):   {"val": "5.0",  "is_simulated": False},
        ("bow",   "Governance",   "Governance_Pass_Rate"): {"val": "50",   "is_simulated": False},
        ("sword", "Code Security","Boundary_Violations"):  {"val": "3",    "is_simulated": False},
    })
    fired = correlation.evaluate(p)
    ids = {r["id"] for r in fired}
    assert "correlation:ops_and_security_degraded" in ids
    assert "correlation:governance_and_security_gap" in ids


# ---------------------------------------------------------------------------
# evaluate — slop_and_docs_co_degraded (new cross-component rule)
# ---------------------------------------------------------------------------

def test_slop_docs_fires_when_both_degraded():
    p = _pillars({
        ("arts", "Output Quality", "Slop_Density"):       {"val": "20",  "is_simulated": False},
        ("arts", "Docs",           "Doc_Parity_Issues"):  {"val": "8",   "is_simulated": False},
    })
    fired = correlation.evaluate(p)
    ids = {r["id"] for r in fired}
    assert "correlation:slop_and_docs_co_degraded" in ids


def test_slop_docs_does_not_fire_when_docs_ok():
    p = _pillars({
        ("arts", "Output Quality", "Slop_Density"):      {"val": "20",  "is_simulated": False},
        ("arts", "Docs",           "Doc_Parity_Issues"): {"val": "2",   "is_simulated": False},
    })
    fired = correlation.evaluate(p)
    ids = {r["id"] for r in fired}
    assert "correlation:slop_and_docs_co_degraded" not in ids


def test_slop_docs_does_not_fire_when_slop_ok():
    p = _pillars({
        ("arts", "Output Quality", "Slop_Density"):      {"val": "10",  "is_simulated": False},
        ("arts", "Docs",           "Doc_Parity_Issues"): {"val": "8",   "is_simulated": False},
    })
    fired = correlation.evaluate(p)
    ids = {r["id"] for r in fired}
    assert "correlation:slop_and_docs_co_degraded" not in ids


# ---------------------------------------------------------------------------
# evaluate — vault_health_crisis (Knowledge vault cross-component rule)
# ---------------------------------------------------------------------------

def test_vault_crisis_fires_when_health_low_and_backlog_high():
    p = _pillars({
        ("arts", "Knowledge", "Wiki_Health_Score"): {"val": "65", "is_simulated": False},
        ("arts", "Knowledge", "Raw_Pending"):        {"val": "8",  "is_simulated": False},
    })
    fired = correlation.evaluate(p)
    ids = {r["id"] for r in fired}
    assert "correlation:vault_health_crisis" in ids


def test_vault_crisis_does_not_fire_when_health_ok():
    p = _pillars({
        ("arts", "Knowledge", "Wiki_Health_Score"): {"val": "95", "is_simulated": False},
        ("arts", "Knowledge", "Raw_Pending"):        {"val": "8",  "is_simulated": False},
    })
    fired = correlation.evaluate(p)
    ids = {r["id"] for r in fired}
    assert "correlation:vault_health_crisis" not in ids


def test_vault_crisis_does_not_fire_when_backlog_low():
    p = _pillars({
        ("arts", "Knowledge", "Wiki_Health_Score"): {"val": "65", "is_simulated": False},
        ("arts", "Knowledge", "Raw_Pending"):        {"val": "2",  "is_simulated": False},
    })
    fired = correlation.evaluate(p)
    ids = {r["id"] for r in fired}
    assert "correlation:vault_health_crisis" not in ids


def test_per_session_condition_normalized_by_session_count():
    # SENSEI-6: Frustration_Signals is per-session — the gate must compare the
    # normalized reading, not the raw cumulative count. 208 raw over 1861 sessions
    # (~0.11/session) must NOT satisfy "gt 2".
    pillars = {
        "bow": {"Activity": {"Session_Count": {"val": "1861", "is_simulated": False}}},
        "brush": {"Token Efficiency": {"Total_Cost": {"val": "21473", "is_simulated": False}}},
        "arts": {"Interaction": {"Frustration_Signals": {"val": "208", "is_simulated": False}}},
        "sword": {},
    }
    fired = correlation.evaluate(pillars)
    assert not any(r["id"] == "correlation:cost_and_quality_tradeoff" for r in fired)


def test_per_session_condition_fires_on_genuinely_high_rate():
    # 4 frustration signals per session across 10 sessions -> 4.0/session > 2 -> fires,
    # and routes to the readonly cost-breakdown diagnostic (not /model-selector).
    pillars = {
        "bow": {"Activity": {"Session_Count": {"val": "10", "is_simulated": False}}},
        "brush": {"Token Efficiency": {"Total_Cost": {"val": "100", "is_simulated": False}}},
        "arts": {"Interaction": {"Frustration_Signals": {"val": "40", "is_simulated": False}}},
        "sword": {},
    }
    fired = correlation.evaluate(pillars)
    hit = next((r for r in fired if r["id"] == "correlation:cost_and_quality_tradeoff"), None)
    assert hit is not None
    assert hit["command"] == "/cost-breakdown-audit"
