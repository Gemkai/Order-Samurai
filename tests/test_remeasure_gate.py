"""Eval for the deterministic reflex fire-path verify-gate (bin/remeasure_gate.py).

Covers the two things that make the gate safe:
  - the SUPPRESS decision fires ONLY on a positive "within threshold now" signal
    (no active CRITICAL/HIGH reflex for the metric in a fresh re-measure), and
  - every other outcome — still-breaching, aggregate error, unknown metric —
    PROCEEDS to the skill (fail-open: a broken gate never silences autonomy).

The pure core (still_breaching) is tested with injected reflex lists; main()'s
exit-code contract is tested with aggregate()/METRIC_CONFIG stubbed so no live
telemetry read happens in CI.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Pack root holds both bin/ and agentica_core/ (flattened layout).
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin import remeasure_gate  # type: ignore[import-not-found]
from bin.remeasure_gate import (  # type: ignore[import-not-found]
    EXIT_ERROR,
    EXIT_PROCEED,
    EXIT_SUPPRESS,
    metric_value,
    still_breaching,
)


def _reflex(rid: str, tier: str = "CRITICAL", status: str = "active") -> dict:
    return {"id": rid, "tier": tier, "status": status}


class StillBreachingCore(unittest.TestCase):
    """Pure core: does a fresh reflex list still fire this metric?"""

    def test_active_critical_reflex_is_breaching(self):
        rf = [_reflex("metric:arts:Doc_Parity_Issues", "CRITICAL")]
        self.assertTrue(still_breaching(rf, "Doc_Parity_Issues"))

    def test_active_high_reflex_is_breaching(self):
        rf = [_reflex("metric:arts:Doc_Parity_Issues", "HIGH")]
        self.assertTrue(still_breaching(rf, "Doc_Parity_Issues"))

    def test_absent_reflex_is_phantom(self):
        # Metric recovered — no reflex built for it at all.
        rf = [_reflex("metric:bow:Avg_Session_Turns", "CRITICAL")]
        self.assertFalse(still_breaching(rf, "Doc_Parity_Issues"))

    def test_medium_downgrade_is_phantom(self):
        # Recovered to MEDIUM — the engine's tier gate would not fire it, so it is a
        # phantom from the fire path's perspective and must be suppressed.
        rf = [_reflex("metric:arts:Doc_Parity_Issues", "MEDIUM")]
        self.assertFalse(still_breaching(rf, "Doc_Parity_Issues"))

    def test_inactive_reflex_is_phantom(self):
        rf = [_reflex("metric:arts:Doc_Parity_Issues", "CRITICAL", status="armed")]
        self.assertFalse(still_breaching(rf, "Doc_Parity_Issues"))

    def test_non_metric_reflex_ignored(self):
        # A nudge/trajectory reflex naming the metric must not count as a metric breach.
        rf = [_reflex("nudge:arts:Doc_Parity_Issues", "CRITICAL")]
        self.assertFalse(still_breaching(rf, "Doc_Parity_Issues"))

    def test_suffix_match_is_exact(self):
        # A metric whose name is a suffix of another must not false-match.
        rf = [_reflex("metric:arts:Wiki_Health_Score", "CRITICAL")]
        self.assertFalse(still_breaching(rf, "Health_Score"))


class MetricValueCore(unittest.TestCase):
    """Pure core: pull a metric's live numeric value out of a payload tree."""

    def _payload(self, val) -> dict:
        return {"pillars": {"arts": {"Craft": {"Doc_Parity_Issues": {"val": val}}}}}

    def test_numeric_value_is_returned(self):
        self.assertEqual(metric_value(self._payload(17), "Doc_Parity_Issues"), 17.0)

    def test_numeric_string_with_commas_is_parsed(self):
        self.assertEqual(metric_value(self._payload("1,234"), "Doc_Parity_Issues"), 1234.0)

    def test_absent_metric_returns_none(self):
        self.assertIsNone(metric_value(self._payload(17), "Wiki_Health_Score"))

    def test_non_numeric_value_returns_none(self):
        self.assertIsNone(metric_value(self._payload("n/a"), "Doc_Parity_Issues"))

    def test_boolean_value_returns_none(self):
        # bool is an int subclass — must not be misread as a measurement.
        self.assertIsNone(metric_value(self._payload(True), "Doc_Parity_Issues"))

    def test_malformed_payload_returns_none(self):
        self.assertIsNone(metric_value({}, "Doc_Parity_Issues"))
        self.assertIsNone(metric_value({"pillars": "oops"}, "Doc_Parity_Issues"))


class MainExitContract(unittest.TestCase):
    """main() exit-code contract, with aggregate()/METRIC_CONFIG stubbed."""

    def setUp(self):
        self._known = remeasure_gate._known_metric
        self._live = remeasure_gate._live_payload
        remeasure_gate._known_metric = lambda m: True

    def tearDown(self):
        remeasure_gate._known_metric = self._known
        remeasure_gate._live_payload = self._live

    def test_phantom_suppresses(self):
        remeasure_gate._live_payload = lambda window_days: {"reflexes": []}  # nothing breaching
        self.assertEqual(remeasure_gate.main(["--metric", "Doc_Parity_Issues"]), EXIT_SUPPRESS)

    def test_real_breach_proceeds(self):
        remeasure_gate._live_payload = lambda window_days: {
            "reflexes": [_reflex("metric:arts:Doc_Parity_Issues", "CRITICAL")]
        }
        self.assertEqual(remeasure_gate.main(["--metric", "Doc_Parity_Issues"]), EXIT_PROCEED)

    def test_unknown_metric_fails_open(self):
        remeasure_gate._known_metric = lambda m: False
        self.assertEqual(remeasure_gate.main(["--metric", "Not_A_Metric"]), EXIT_ERROR)

    def test_aggregate_error_fails_open(self):
        def _boom(window_days):
            raise RuntimeError("telemetry unreadable")
        remeasure_gate._live_payload = _boom
        self.assertEqual(remeasure_gate.main(["--metric", "Doc_Parity_Issues"]), EXIT_ERROR)


if __name__ == "__main__":
    unittest.main()
