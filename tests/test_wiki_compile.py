"""Eval for the deterministic Raw_Pending triage mechanism (bin/wiki_compile.py).

Covers the verify gate (breach only when calibrated AND count >= FAIL), the uncalibrated guard
(an unreachable vault never confirms a breach), detect ordering, and idempotency. The kernel<->bin
count parity lives in agentica_core/tests/test_wiki_compile_drift.py.
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

from bin.wiki_compile import audit  # type: ignore[import-not-found]


_FIXED_NOW = lambda: "2026-06-29T00:00:00+00:00"


def _audit(pending, *, fail_threshold=5.0, calibrated=True):
    return audit(pending, fail_threshold=fail_threshold, calibrated=calibrated, now_fn=_FIXED_NOW)


class Audit(unittest.TestCase):
    def test_below_threshold_does_not_confirm(self):
        report = _audit(["a.md", "b.md"])  # 2 < fail 5
        self.assertEqual(report["verdict"], "below_threshold")
        self.assertFalse(report["breach_confirmed"])
        self.assertEqual(report["raw_pending_count"], 2)

    def test_at_or_above_fail_confirms_breach(self):
        report = _audit([f"n{i}.md" for i in range(5)])  # 5 >= fail 5
        self.assertEqual(report["verdict"], "breach_confirmed")
        self.assertTrue(report["breach_confirmed"])

    def test_empty_is_below_threshold_when_calibrated(self):
        report = _audit([])
        self.assertEqual(report["verdict"], "below_threshold")
        self.assertFalse(report["breach_confirmed"])
        self.assertIsNone(report["top_pending"])

    def test_uncalibrated_vault_never_confirms_a_breach(self):
        # vault unreachable: even a high count (passed through) must read uncalibrated, not a breach.
        report = _audit([f"n{i}.md" for i in range(9)], calibrated=False)
        self.assertEqual(report["verdict"], "uncalibrated")
        self.assertFalse(report["breach_confirmed"])

    def test_top_pending_is_sorted_and_order_independent(self):
        forward = _audit(["c.md", "a.md", "b.md"])
        reverse = _audit(["b.md", "c.md", "a.md"])
        self.assertEqual(forward["top_pending"], "a.md")
        self.assertEqual(forward["pending"], reverse["pending"])

    def test_is_idempotent(self):
        pending = ["x.md", "y.md", "z.md", "w.md", "v.md"]
        self.assertEqual(_audit(pending), _audit(pending))


if __name__ == "__main__":
    unittest.main()
