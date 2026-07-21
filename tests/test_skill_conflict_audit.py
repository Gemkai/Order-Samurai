"""Eval for the deterministic Skill_Conflicts triage mechanism (bin/skill_conflict_audit.py).

Covers the count (number of conflict groups), the verify gate (breach only when calibrated AND
count >= FAIL), the uncalibrated guard (absent source -> never a false breach), detect ordering,
and idempotency. Kernel<->bin parity lives in agentica_core/tests/test_skill_conflict_audit_drift.py.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]      # Order Samurai
GOV_ROOT = Path(__file__).resolve().parents[2]       # Governance (for agentica_core)
for _p in (REPO_ROOT, GOV_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from bin.skill_conflict_audit import audit, conflict_count  # type: ignore[import-not-found]


_FIXED_NOW = lambda: "2026-06-29T00:00:00+00:00"


def _audit(groups, *, fail_threshold=5.0, calibrated=True):
    return audit(groups, fail_threshold=fail_threshold, calibrated=calibrated, now_fn=_FIXED_NOW)


class ConflictCount(unittest.TestCase):
    def test_counts_group_keys(self):
        self.assertEqual(conflict_count({"security": ["a", "b"], "research": ["c"]}), 2)

    def test_empty_and_none_are_zero(self):
        self.assertEqual(conflict_count({}), 0)
        self.assertEqual(conflict_count(None), 0)


class Audit(unittest.TestCase):
    def test_below_threshold_does_not_confirm(self):
        report = _audit({"a": ["x"], "b": ["y"]})  # 2 groups < fail 5
        self.assertEqual(report["verdict"], "below_threshold")
        self.assertFalse(report["breach_confirmed"])
        self.assertEqual(report["conflict_groups"], 2)

    def test_at_or_above_fail_confirms_breach(self):
        groups = {f"g{i}": ["s"] for i in range(5)}  # 5 groups >= fail 5
        report = _audit(groups)
        self.assertEqual(report["verdict"], "breach_confirmed")
        self.assertTrue(report["breach_confirmed"])

    def test_uncalibrated_source_never_confirms_a_breach(self):
        # source absent: even passing a big mapping, calibrated=False must not confirm.
        report = _audit({f"g{i}": ["s"] for i in range(9)}, calibrated=False)
        self.assertEqual(report["verdict"], "uncalibrated")
        self.assertFalse(report["breach_confirmed"])

    def test_empty_groups_calibrated_is_below_threshold(self):
        report = _audit({})
        self.assertEqual(report["verdict"], "below_threshold")
        self.assertEqual(report["conflict_groups"], 0)
        self.assertIsNone(report["top_group"])

    def test_top_group_is_the_largest_family_with_members(self):
        report = _audit({"small": ["a"], "big": ["a", "b", "c"], "mid": ["a", "b"]})
        top = report["top_group"]
        self.assertEqual(top["group"], "big")
        self.assertEqual(top["skill_count"], 3)
        self.assertEqual(top["skills"], ["a", "b", "c"])  # sorted

    def test_is_idempotent(self):
        groups = {"security": ["audit-skills", "shannon"], "research": ["a", "b"]}
        self.assertEqual(_audit(groups), _audit(groups))


if __name__ == "__main__":
    unittest.main()
