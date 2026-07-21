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
    # Gate_Fires is informational — huge values must NOT flag or score.
    # Secret_Scrubs no longer exists (RETIRED 2026-07-19, dead emitter); the
    # assert stays as a regression guard against a graded re-add.
    assert "Gate_Fires" not in insights.METRIC_RULES
    assert "Secret_Scrubs" not in insights.METRIC_RULES


def test_no_weighted_mean_score_key():
    # De-aggregation 2026-07-19: annotate() must NOT emit a blended pillar score
    # or letter grade — status is the rollup, flags carry per-metric grades.
    pillars = {"bow": {}, "sword": {
        "g": {
            "Vulnerability_MTTR": {"val": "6", "is_simulated": False},
            "Boundary_Violations": {"val": "0", "is_simulated": False},
            "Secrets_Detected": {"val": "0", "is_simulated": False},
            "Security_Scorecard": {"val": "95", "is_simulated": False},
        }}, "brush": {}, "arts": {}}
    sc = insights.annotate(pillars)["sword"]
    assert "score" not in sc
    assert "grade" not in sc
    assert "score_delta" not in sc
    # Status rollup carries the signal: 1 breaching metric can't be averaged away.
    assert sc["rollup"]["worst"] in ("HIGH", "CRITICAL")
    assert sc["rollup"]["passing"] == 3
    assert sc["rollup"]["graded"] == 4
    assert any(f["name"] == "Vulnerability_MTTR" for f in sc["flags"])


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
