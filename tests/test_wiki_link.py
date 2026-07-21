"""Eval for the deterministic Wiki_Orphans triage mechanism (bin/wiki_link.py).

Covers the verify gate (breach only when calibrated AND count >= FAIL), the uncalibrated guard
(an unreachable vault never confirms a breach), detect ordering, and idempotency. The kernel<->bin
count parity lives in agentica_core/tests/test_wiki_link_drift.py.
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

from bin.wiki_link import audit  # type: ignore[import-not-found]


_FIXED_NOW = lambda: "2026-06-29T00:00:00+00:00"


def _audit(orphans, *, fail_threshold=10.0, calibrated=True):
    return audit(orphans, fail_threshold=fail_threshold, calibrated=calibrated, now_fn=_FIXED_NOW)


class Audit(unittest.TestCase):
    def test_below_threshold_does_not_confirm(self):
        report = _audit([f"a{i}.md" for i in range(9)])  # 9 < fail 10
        self.assertEqual(report["verdict"], "below_threshold")
        self.assertFalse(report["breach_confirmed"])
        self.assertEqual(report["wiki_orphans_count"], 9)

    def test_at_or_above_fail_confirms_breach(self):
        report = _audit([f"a{i}.md" for i in range(10)])  # 10 >= fail 10
        self.assertEqual(report["verdict"], "breach_confirmed")
        self.assertTrue(report["breach_confirmed"])

    def test_empty_is_below_threshold_when_calibrated(self):
        report = _audit([])
        self.assertEqual(report["verdict"], "below_threshold")
        self.assertFalse(report["breach_confirmed"])
        self.assertIsNone(report["top_orphan"])

    def test_uncalibrated_vault_never_confirms_a_breach(self):
        report = _audit([f"a{i}.md" for i in range(20)], calibrated=False)
        self.assertEqual(report["verdict"], "uncalibrated")
        self.assertFalse(report["breach_confirmed"])

    def test_top_orphan_is_sorted_and_order_independent(self):
        forward = _audit(["c.md", "a.md", "b.md"])
        reverse = _audit(["b.md", "c.md", "a.md"])
        self.assertEqual(forward["top_orphan"], "a.md")
        self.assertEqual(forward["orphans"], reverse["orphans"])

    def test_is_idempotent(self):
        orphans = [f"n{i}.md" for i in range(12)]
        self.assertEqual(_audit(orphans), _audit(orphans))


if __name__ == "__main__":
    unittest.main()
