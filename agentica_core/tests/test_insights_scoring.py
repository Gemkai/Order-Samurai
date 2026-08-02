import json
import tempfile
from pathlib import Path

from agentica_core import insights


def test_health_is_continuous_and_bounded():
    lower = {"dir": "lower", "warn": 10, "fail": 20}
    assert insights._health(5, lower) == 100.0       # inside warn
    assert insights._health(10, lower) == 100.0      # at warn
    assert insights._health(15, lower) == 70.0       # midway warn..fail -> 100-30
    assert insights._health(20, lower) == 40.0       # at fail
    assert insights._health(40, lower) == 0.0        # ~2x fail floors
    higher = {"dir": "higher", "warn": 85, "fail": 70}
    assert insights._health(90, higher) == 100.0
    assert insights._health(70, higher) == 40.0
    assert 40 < insights._health(78, higher) < 100


def test_protective_activity_not_graded():
    # Guardrail_Blocks is informational — huge values must NOT flag or score.
    # Gate_Fires / Secret_Scrubs no longer exist (Secret_Scrubs RETIRED 2026-07-19,
    # dead emitter); the asserts stay as regression guards against graded re-adds.
    assert "Guardrail_Blocks" not in insights.METRIC_RULES
    assert "Gate_Fires" not in insights.METRIC_RULES
    assert "Secret_Scrubs" not in insights.METRIC_RULES


def test_no_weighted_mean_score_key():
    # De-aggregation 2026-07-19: annotate() must NOT emit a blended pillar score
    # or letter grade — status is the rollup, flags carry per-metric grades.
    pillars = {"bow": {}, "sword": {
        "g": {
            "Open_CVEs": {"val": "6", "is_simulated": False},
            "Boundary_Violations": {"val": "0", "is_simulated": False},
            "Secrets_Detected": {"val": "0", "is_simulated": False},
        }}, "brush": {}, "arts": {}}
    sc = insights.annotate(pillars)["sword"]
    assert "score" not in sc
    assert "grade" not in sc
    assert "score_delta" not in sc
    # Status rollup carries the signal: 1 breaching metric can't be averaged away.
    assert sc["rollup"]["worst"] in ("HIGH", "CRITICAL")
    assert sc["rollup"]["passing"] == 2
    assert sc["rollup"]["graded"] == 3
    assert any(f["name"] == "Open_CVEs" for f in sc["flags"])


def test_cumulative_metric_rate_normalized_by_sessions():
    # 74 frustration signals across 150 sessions ~0.49/session -> healthy, not flagged
    pillars = {
        "bow": {"a": {"Session_Count": {"val": "150", "is_simulated": False}}},
        "sword": {}, "brush": {},
        "arts": {"i": {"Frustration_Signals": {"val": "74", "is_simulated": False}}},
    }
    sc = insights.annotate(pillars)["arts"]
    assert not any(f["name"] == "Frustration_Signals" for f in sc["flags"])


def test_health_lower_is_better_warn_equals_fail_no_zero_division():
    """When warn==fail, _health() must not ZeroDivisionError for any input.

    When warn==fail=5 the paths are:
      v <= 5  → first guard  → 100.0 (no interpolation reached)
      v >  5  → v>=fail branch → score < 40.0 (no interpolation reached)
    The warn>=fail dead-code guard exists only to protect against float
    near-equality where fail-warn would approach zero in the interpolation.
    """
    rule = {"dir": "lower", "warn": 5, "fail": 5}
    # at-or-below threshold: first guard fires
    assert insights._health(5, rule) == 100.0
    assert insights._health(0, rule) == 100.0
    # above threshold: v>=fail branch fires, valid float, no exception
    result = insights._health(7, rule)
    assert isinstance(result, float), f"expected float, got {type(result)}"
    assert 0.0 <= result < 40.0, f"over-fail score should be in [0,40), got {result}"


def test_health_lower_is_better_above_fail_boundary():
    """Ensure existing paths still work after adding the warn==fail guard."""
    rule = {"dir": "lower", "warn": 0, "fail": 10}
    assert insights._health(0, rule) == 100.0   # at warn
    assert insights._health(10, rule) == 40.0   # at fail
    assert insights._health(5, rule) == 70.0    # midpoint


def test_critical_breach_sets_rollup_worst_and_weight_survives_as_hint():
    """De-aggregation: a hard-failing weight-3 metric marks the pillar CRITICAL via
    the rollup (worst tier wins — no mean to drown it in), and the weight survives
    only as a sort/priority hint on the per-metric rule, never a multiplier."""
    pillars = {"bow": {}, "sword": {
        "g": {
            # weight=3.0, hard FAIL: Boundary_Violations at 10 (warn=1, fail=3)
            "Boundary_Violations": {"val": "10", "is_simulated": False},
            # weight=1.0, PASS: Deprecated_Deps at 0 (warn=20, fail=120)
            "Deprecated_Deps":     {"val": "0",  "is_simulated": False},
        }}, "brush": {}, "arts": {}}
    annotated_pillars = pillars
    sc = insights.annotate(annotated_pillars)["sword"]
    assert sc["rollup"] == {"worst": "CRITICAL", "passing": 1, "graded": 2}
    # Weight rides on the per-metric rule for display/sort, not in any blend.
    bv_env = annotated_pillars["sword"]["g"]["Boundary_Violations"]
    assert bv_env["rule"]["weight"] == 3.0
    assert bv_env["status"] == "FAIL"
    # The breach surfaces as a flag with a per-metric letter grade (reflex tier source).
    assert any(f["name"] == "Boundary_Violations" and f["grade"] == "F" for f in sc["flags"])


def _make_store(rows: list[dict]) -> Path:
    """Write a JSONL history file into a temp dir and return its path."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    for row in rows:
        tmp.write(json.dumps(row) + "\n")
    tmp.close()
    return Path(tmp.name)


def test_trajectory_fires_for_rising_metric():
    """Continuously rising metric should predict a breach date."""
    from datetime import datetime, timezone, timedelta
    from agentica_core.insights import METRIC_RULES

    pillar = "arts"
    metric = "Slop_Density"
    group = "Output Quality"
    key = f"{pillar}/{group}/{metric}"
    # Scale the fixture to the LIVE calibrated fail threshold (thresholds.json drifts as
    # telemetry accumulates), and timestamp snapshots relative to now. Values rise toward
    # fail but the current reading stays below it, so the projected breach stays in the
    # future regardless of when the test runs or how calibration has moved the threshold.
    fail = METRIC_RULES[metric]["fail"]
    now = datetime.now(timezone.utc)
    rising = [fail * f for f in (0.30, 0.45, 0.60, 0.75)]
    current = fail * 0.90
    store = _make_store([
        {"ts": (now - timedelta(days=d)).isoformat(), "values": {key: v}}
        for d, v in zip((28, 21, 14, 7), rising)
    ])
    try:
        pillars = {
            pillar: {group: {metric: {"val": str(current), "is_simulated": False}}},
            "bow": {}, "sword": {}, "brush": {},
        }
        insights.populate_history(pillars, store=store)
        breach = pillars[pillar][group][metric].get("trajectory_breach_days")
        assert breach is not None, "Rising metric should predict a breach"
        assert breach > 0, "Breach days must be positive"
    finally:
        store.unlink(missing_ok=True)


def test_trajectory_suppressed_for_plateaued_metric():
    """Metric that rose historically but is now flat should NOT predict a breach."""
    from datetime import datetime, timezone, timedelta
    from agentica_core.insights import METRIC_RULES

    pillar = "arts"
    metric = "Slop_Density"
    group = "Output Quality"
    key = f"{pillar}/{group}/{metric}"
    # Scale to the live fail threshold and stay BELOW it, so the only thing that can
    # suppress the breach is the plateau guard (recent points flat) — not the
    # "already past fail" early-out. Spike in the past, then plateau for the last 3 points.
    fail = METRIC_RULES[metric]["fail"]
    now = datetime.now(timezone.utc)
    plateau = fail * 0.90
    series = [fail * 0.10, fail * 0.80, plateau, plateau]
    store = _make_store([
        {"ts": (now - timedelta(days=d)).isoformat(), "values": {key: v}}
        for d, v in zip((28, 21, 14, 7), series)
    ])
    try:
        pillars = {
            pillar: {group: {metric: {"val": str(plateau), "is_simulated": False}}},
            "bow": {}, "sword": {}, "brush": {},
        }
        insights.populate_history(pillars, store=store)
        breach = pillars[pillar][group][metric].get("trajectory_breach_days")
        assert breach is None, (
            f"Plateaued metric should not predict breach; got breach_days={breach}"
        )
    finally:
        store.unlink(missing_ok=True)


def test_graded_metric_pillar_map_matches_registry():
    """Drift guard for the S4 coverage fix: every graded metric (METRIC_RULES key)
    must have a pillar placement, and the map must not carry keys for metrics that
    are no longer graded — otherwise Instrumentation_Coverage's registry-based
    denominator silently drifts from the real registry."""
    assert set(insights._GRADED_METRIC_PILLARS) == set(insights.METRIC_RULES)


def test_observe_metrics_are_not_graded_before_calibration():
    """OBSERVE means visible evidence, not a green/red SLO contribution."""
    for metric in ("Remediation_Delta", "Verifier_Falsifiability"):
        assert insights.METRIC_CONFIG[metric]["maturity"] == "OBSERVE"
        assert metric not in insights.METRIC_RULES
        assert metric not in insights._GRADED_METRIC_PILLARS


def test_instrumentation_coverage_counts_absent_metrics_as_dark():
    """Audit S4 regression: a pillar with one live graded envelope out of a
    registry of many graded metrics must NOT report 100% coverage — absent-source
    metrics (no envelope at all) count against the denominator."""
    pillars = {
        "bow": {"Activity": {"Error_Rate": {"val": "1.0", "is_simulated": False}}},
        "sword": {}, "brush": {}, "arts": {},
    }
    scores = insights.annotate(pillars)
    bow_registry = sum(1 for pks in insights._GRADED_METRIC_PILLARS.values() if "bow" in pks)
    assert bow_registry > 1
    assert scores["bow"]["graded_count"] == 1
    assert scores["bow"]["total_gradeable"] == bow_registry
    assert scores["bow"]["coverage_pct"] == round(100 / bow_registry, 1)


def test_append_snapshot_dedups_and_carries_week_key(tmp_path):
    # Regression for the 2026-07-26 audit: the old bare append let concurrent
    # refreshers write byte-identical duplicate rows (biasing calibrate
    # percentiles), and live rows lacked the `week` key backfill rows carry,
    # leaving two schemas in one file.
    import json
    from agentica_core import insights

    store = tmp_path / "metrics_history.jsonl"
    ts = "2026-07-26T18:00:00+00:00"
    vals = {"brush/Token Efficiency/Cost_Per_Task": 11.28}

    insights.append_snapshot(store, ts, vals)
    insights.append_snapshot(store, ts, vals)  # identical concurrent write — dropped
    insights.append_snapshot(store, "2026-07-26T18:05:00+00:00", vals)  # new ts — kept

    rows = [json.loads(l) for l in store.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2  # duplicate was dropped
    assert all(r["week"] == "2026-W30" for r in rows)  # same schema as backfill rows
    assert rows[0]["values"] == vals


def test_24h_clause_surfaces_graded_metric_among_newly_tracked(tmp_path):
    # A newly tracked GRADED metric (Error_Rate has a dir rule) must win the
    # "now tracking" slot over ungraded ones. The graded-first filter used to
    # look up "error_rate" against the CamelCase METRIC_CONFIG keys, so it
    # matched nothing and the graded metric was dropped by insertion order.
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    store = tmp_path / "metrics_history.jsonl"
    rows = [
        {"ts": (now - timedelta(hours=30)).isoformat(),
         "values": {"bow/Ops/Tool_Calls": 5}},
        {"ts": (now - timedelta(hours=1)).isoformat(),
         "values": {"bow/Ops/Tool_Calls": 5,
                    "bow/Ops/Session_Count": 4,        # ungraded (no dir)
                    "bow/Ops/Wiki_Article_Count": 10,  # ungraded (not in config)
                    "bow/Ops/Error_Rate": 2.5}},       # graded (dir: lower)
    ]
    store.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    clause = insights._24h_clause("bow", store)
    assert "now tracking" in clause
    assert "error rate" in clause, clause


def test_one_detected_secret_must_not_grade_pass():
    """Secrets_Detected shipped with warn==fail==1: under the documented _health
    contract (at/inside warn -> 100) one leaked secret graded a perfect 100/PASS
    and never flagged — while two secrets floored to 0. Any detected secret must
    grade below the flag threshold (h < 60) so it surfaces in needs_attention;
    zero secrets stays a perfect score."""
    rule = insights.METRIC_RULES["Secrets_Detected"]
    assert insights._health(0, rule) == 100.0
    assert insights._health(1, rule) < 60.0
