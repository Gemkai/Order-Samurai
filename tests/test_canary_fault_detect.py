"""Eval for the deterministic canary fault-detection mechanism (bin/canary_fault_detect.py).

This IS the eval the LLM /canary-fault-diagnosis skill never had: fixtures map a
canary state + a fixed clock to the expected fault class and regeneration safety,
pin the precedence (a broken gate outranks staleness), and assert idempotency.
The clock is injected so the eval never depends on wall-time.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin.canary_fault_detect import classify  # type: ignore[import-not-found]


# Fixed reference clock for every fixture — deterministic, no wall-time.
NOW = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def _canary(days_ago: float = 1.0, gate_working: bool = True,
            max_age_days: int = 7) -> dict:
    """Build a canary whose last_run is `days_ago` before the reference clock."""
    last_run = (NOW - timedelta(days=days_ago)).isoformat()
    return {"last_run": last_run, "gate_working": gate_working, "max_age_days": max_age_days}


# ---------------------------------------------------------------------------
# Fault classes
# ---------------------------------------------------------------------------

class FaultClassTests(unittest.TestCase):

    def test_classifies_missing_file_as_missing(self) -> None:
        verdict = classify(None, NOW)
        self.assertEqual(verdict["fault_class"], "missing")

    def test_classifies_gate_working_false_as_gate_not_working(self) -> None:
        verdict = classify(_canary(gate_working=False), NOW)
        self.assertEqual(verdict["fault_class"], "gate-not-working")

    def test_classifies_absent_timestamp_as_corrupt(self) -> None:
        verdict = classify({"gate_working": True}, NOW)
        self.assertEqual(verdict["fault_class"], "corrupt")

    def test_classifies_unparseable_timestamp_as_corrupt(self) -> None:
        verdict = classify({"gate_working": True, "last_run": "not-a-date"}, NOW)
        self.assertEqual(verdict["fault_class"], "corrupt")

    def test_classifies_old_run_as_stale(self) -> None:
        verdict = classify(_canary(days_ago=10, max_age_days=7), NOW)
        self.assertEqual(verdict["fault_class"], "stale")

    def test_classifies_recent_run_as_healthy(self) -> None:
        verdict = classify(_canary(days_ago=1, max_age_days=7), NOW)
        self.assertEqual(verdict["fault_class"], "healthy")


# ---------------------------------------------------------------------------
# Precedence — a broken gate outranks staleness
# ---------------------------------------------------------------------------

class PrecedenceTests(unittest.TestCase):

    def test_broken_gate_outranks_stale_timestamp(self) -> None:
        # Both broken AND stale: must report gate-not-working (forbids regeneration).
        verdict = classify(_canary(days_ago=99, gate_working=False, max_age_days=7), NOW)
        self.assertEqual(verdict["fault_class"], "gate-not-working")


# ---------------------------------------------------------------------------
# Staleness boundary + custom window
# ---------------------------------------------------------------------------

class StalenessBoundaryTests(unittest.TestCase):

    def test_run_exactly_at_max_age_is_healthy(self) -> None:
        # age == max_age is not yet stale (reducer uses strictly-greater).
        verdict = classify(_canary(days_ago=7, max_age_days=7), NOW)
        self.assertEqual(verdict["fault_class"], "healthy")

    def test_run_one_day_past_max_age_is_stale(self) -> None:
        verdict = classify(_canary(days_ago=8, max_age_days=7), NOW)
        self.assertEqual(verdict["fault_class"], "stale")

    def test_respects_custom_max_age_window(self) -> None:
        # 20 days old but a 35-day window -> still healthy.
        verdict = classify(_canary(days_ago=20, max_age_days=35), NOW)
        self.assertEqual(verdict["fault_class"], "healthy")


# ---------------------------------------------------------------------------
# Regeneration safety
# ---------------------------------------------------------------------------

class RegenerationSafetyTests(unittest.TestCase):

    def test_stale_canary_is_safe_to_regenerate(self) -> None:
        self.assertTrue(classify(_canary(days_ago=10), NOW)["safe_to_regenerate"])

    def test_missing_canary_is_safe_to_regenerate(self) -> None:
        self.assertTrue(classify(None, NOW)["safe_to_regenerate"])

    def test_broken_gate_is_not_safe_to_regenerate(self) -> None:
        self.assertFalse(classify(_canary(gate_working=False), NOW)["safe_to_regenerate"])

    def test_healthy_canary_is_not_flagged_for_regeneration(self) -> None:
        self.assertFalse(classify(_canary(days_ago=1), NOW)["safe_to_regenerate"])


# ---------------------------------------------------------------------------
# Metric parity + naive-timestamp handling
# ---------------------------------------------------------------------------

class VerdictShapeTests(unittest.TestCase):

    def test_fault_value_is_one_for_fault(self) -> None:
        self.assertEqual(classify(None, NOW)["fault_value"], 1.0)

    def test_fault_value_is_zero_for_healthy(self) -> None:
        self.assertEqual(classify(_canary(days_ago=1), NOW)["fault_value"], 0.0)

    def test_reports_age_in_days(self) -> None:
        self.assertEqual(classify(_canary(days_ago=10), NOW)["age_days"], 10)

    def test_naive_timestamp_treated_as_utc(self) -> None:
        naive = (NOW - timedelta(days=2)).replace(tzinfo=None).isoformat()
        verdict = classify({"gate_working": True, "last_run": naive, "max_age_days": 7}, NOW)
        self.assertEqual(verdict["age_days"], 2)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class IdempotencyTests(unittest.TestCase):

    def test_same_state_yields_identical_verdict(self) -> None:
        state = _canary(days_ago=10, max_age_days=7)
        self.assertEqual(classify(state, NOW), classify(state, NOW))


if __name__ == "__main__":
    unittest.main()
