from agentica_core import reflexes


def test_worst_project_skips_meta_bucket():
    # Regression: build_project_scores' `_meta` catch-all (unmatched telemetry tags,
    # e.g. Antigravity's fleet-wide "HUD"/"HUB" self-reporting -- 03f6f873, 2026-08-18)
    # carries has_data=True and real scores like any project, so an unfiltered scan
    # could name it "the worst project" -- but it is not a repo any remediation can
    # run against. `is_meta` must be excluded regardless of how low its score is.
    by_project = {
        "RepoA": {"has_data": True, "scores": {"sword": 80.0}},
        "RepoB": {"has_data": True, "scores": {"sword": 65.0}},
        "_meta": {"has_data": True, "is_meta": True, "scores": {"sword": 12.0}},
    }
    assert reflexes._worst_project(by_project, "sword") == "RepoB"


def test_worst_project_falls_through_to_none_when_only_meta_has_data():
    # If `_meta` is the only bucket with data, there is no real worst project --
    # callers fall back to "this repo" (reflexes.py's _metric_reflexes), not `_meta`.
    by_project = {
        "RepoA": {"has_data": False, "scores": {}},
        "_meta": {"has_data": True, "is_meta": True, "scores": {"sword": 12.0}},
    }
    assert reflexes._worst_project(by_project, "sword") is None


def test_worst_project_handles_empty_and_missing_by_project():
    assert reflexes._worst_project({}, "sword") is None
    assert reflexes._worst_project(None, "sword") is None


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
    """Field-assembly test (tier/target/message/trigger), not a routing test --
    doesn't matter which channel the entry lands on, so it checks both. Open_CVEs
    is now advisory-routed (F4, remediation-loops program, 2026-08-24, demoted to
    auto_remediable=False) rather than dispatched, but its fallback/target
    assembly is unaffected -- that logic runs before the dispatch/advisory split.
    """
    pillars = {
        "sword": {"Vulnerability": {"Open_CVEs": {"val": "6", "is_simulated": False,
                                                  "history": [], "mitigation_command": "/codebase-cleanup-deps-audit"}}},
        "bow": {}, "brush": {}, "arts": {},
    }
    category_scores = {"sword": {"flags": [{"name": "Open_CVEs", "val": "6", "grade": "F"}]},
                       "bow": {"flags": []}, "brush": {"flags": []}, "arts": {"flags": []}}
    by_project = {"RepoA": {"has_data": True, "scores": {"sword": 30, "bow": 100, "brush": 100, "arts": 100}}}
    out, advisory = reflexes.build_reflexes(pillars, category_scores, by_project,
                                            nudges_path=reflexes.Path("does-not-exist"),
                                            state_path=reflexes.Path("nope"))
    metric = [r for r in out + advisory if r["source"] == "metric"]
    assert len(metric) == 1
    r = metric[0]
    assert r["tier"] == "CRITICAL"
    assert r["target"] == "RepoA"  # worst-scoring project for sword
    assert "/codebase-cleanup-deps-audit" in r["message"]
    assert "limit" in r["trigger"]  # fixed-threshold fallback (no history)


def test_build_reflexes_uses_failure_platforms_as_target():
    # failure_platforms rides on Governance_Pass_Rate since the Verifier_Failures
    # consolidation (2026-07-08 audit) — the targeting mechanism is unchanged.
    pillars = {
        "bow": {"Governance": {"Governance_Pass_Rate": {
            "val": "50.0", "is_simulated": False, "history": [],
            "failure_platforms": ["antigravity"],
            "mitigation_command": "python -m agentica_core.doctor antigravity",
        }}},
        "sword": {}, "brush": {}, "arts": {},
    }
    category_scores = {"bow": {"flags": [{"name": "Governance_Pass_Rate", "val": "50.0", "grade": "F"}]},
                       "sword": {"flags": []}, "brush": {"flags": []}, "arts": {"flags": []}}
    out, _advisory = reflexes.build_reflexes(pillars, category_scores, {},
                                             nudges_path=reflexes.Path("does-not-exist"),
                                             state_path=reflexes.Path("nope"))
    metric = [r for r in out if r["source"] == "metric"][0]
    assert metric["target"] == "antigravity"
    assert "doctor antigravity" in metric["message"]


def test_non_remediable_metrics_generate_no_metric_reflex():
    # SENSEI-3/4: a breaching metric whose config says auto_remediable=False must not
    # produce a metric reflex on either the sigma or the threshold-fallback path — a
    # CRITICAL card routing to a skill that can't move the metric is a misrouted
    # channel. (Faithfulness_Score is advisory in METRIC_CONFIG.)
    pillars = {
        "arts": {"Output Quality": {"Faithfulness_Score": {
            "val": "40", "is_simulated": False, "history": [40, 40, 40, 40, 40, 90],
        }}},
        "bow": {}, "sword": {}, "brush": {},
    }
    category_scores = {"arts": {"flags": [{"name": "Faithfulness_Score", "val": "40", "grade": "F"}]},
                       "bow": {"flags": []}, "sword": {"flags": []}, "brush": {"flags": []}}
    out, advisory = reflexes.build_reflexes(pillars, category_scores, {},
                                            nudges_path=reflexes.Path("does-not-exist"),
                                            state_path=reflexes.Path("nope"))
    assert [r for r in out if r["source"] == "metric"] == []
    # ...but it is ROUTED, not dropped: it leaves on the advisory channel so
    # investigation-only consumers (sensei) can still see it. Dropping it entirely
    # starved the 6-hourly cycle for 8 days (2026-07-19 -> 2026-07-29).
    assert [r["id"] for r in advisory] == ["metric:arts:Faithfulness_Score"]
