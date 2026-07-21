from agentica_core import reflexes


def test_sigma_tier_fires_on_anomaly():
    # lower-is-better metric spikes far above a flat history -> CRITICAL
    env = {"val": "50", "history": [10, 12, 9, 11, 50], "is_simulated": False}
    rule = {"dir": "lower", "warn": 5, "fail": 20}
    tier, trig = reflexes._sigma_tier(env, rule)
    assert tier == "CRITICAL"
    assert "above" in trig and "mean" in trig


def test_sigma_tier_needs_enough_history():
    env = {"val": "50", "history": [10, 50], "is_simulated": False}
    tier, _ = reflexes._sigma_tier(env, {"dir": "lower", "warn": 5, "fail": 20})
    assert tier is None  # <4 points -> no sigma signal


def test_sigma_tier_flat_history_no_fire():
    env = {"val": "10", "history": [10, 10, 10, 10, 10], "is_simulated": False}
    tier, _ = reflexes._sigma_tier(env, {"dir": "lower", "warn": 5, "fail": 20})
    assert tier is None  # zero variance


def test_build_reflexes_metric_fallback_and_target():
    pillars = {
        "sword": {"Vulnerability": {"Vulnerability_MTTR": {"val": "6", "is_simulated": False,
                                                  "history": [], "mitigation_command": "/codebase-cleanup-deps-audit"}}},
        "bow": {}, "brush": {}, "arts": {},
    }
    category_scores = {"sword": {"flags": [{"name": "Vulnerability_MTTR", "val": "6", "grade": "F"}]},
                       "bow": {"flags": []}, "brush": {"flags": []}, "arts": {"flags": []}}
    by_project = {"RepoA": {"has_data": True, "scores": {"sword": 30, "bow": 100, "brush": 100, "arts": 100}}}
    out = reflexes.build_reflexes(pillars, category_scores, by_project,
                                  nudges_path=reflexes.Path("does-not-exist"), state_path=reflexes.Path("nope"))
    metric = [r for r in out if r["source"] == "metric"]
    assert len(metric) == 1
    r = metric[0]
    assert r["tier"] == "CRITICAL"
    assert r["target"] == "RepoA"  # worst-scoring project for sword
    assert "/codebase-cleanup-deps-audit" in r["message"]
    assert "limit" in r["trigger"]  # fixed-threshold fallback (no history)


def test_build_reflexes_uses_failure_platforms_as_target():
    pillars = {
        "bow": {"Governance": {"Verifier_Failures": {
            "val": "4", "is_simulated": False, "history": [],
            "failure_platforms": ["antigravity"],
            "mitigation_command": "python -m agentica_core.doctor antigravity",
        }}},
        "sword": {}, "brush": {}, "arts": {},
    }
    category_scores = {"bow": {"flags": [{"name": "Verifier_Failures", "val": "4", "grade": "F"}]},
                       "sword": {"flags": []}, "brush": {"flags": []}, "arts": {"flags": []}}
    out = reflexes.build_reflexes(pillars, category_scores, {},
                                  nudges_path=reflexes.Path("does-not-exist"), state_path=reflexes.Path("nope"))
    metric = [r for r in out if r["source"] == "metric"][0]
    assert metric["target"] == "antigravity"
    assert "doctor antigravity" in metric["message"]


def test_non_remediable_metrics_generate_no_metric_reflex():
    # SENSEI-3/4: a breaching metric whose config says auto_remediable=False must not
    # produce a metric reflex on either the sigma or the threshold-fallback path — a
    # CRITICAL card routing to a skill that can't move the metric is a misrouted
    # channel. (Fallback_Recovery_Rate is advisory in METRIC_CONFIG.)
    pillars = {
        "bow": {"Activity": {"Fallback_Recovery_Rate": {
            "val": "40", "is_simulated": False, "history": [40, 40, 40, 40, 40, 90],
        }}},
        "arts": {}, "sword": {}, "brush": {},
    }
    category_scores = {"bow": {"flags": [{"name": "Fallback_Recovery_Rate", "val": "40", "grade": "F"}]},
                       "arts": {"flags": []}, "sword": {"flags": []}, "brush": {"flags": []}}
    out = reflexes.build_reflexes(pillars, category_scores, {},
                                  nudges_path=reflexes.Path("does-not-exist"),
                                  state_path=reflexes.Path("nope"))
    assert [r for r in out if r["source"] == "metric"] == []
