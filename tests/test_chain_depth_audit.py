"""Eval for the deterministic Chain_Depth_Avg triage mechanism (bin/chain_depth_audit.py).

Covers the two things that make the mechanism a faithful detect->verify clone:
  - the VERIFY gate (breach_confirmed only when calibrated AND median >= FAIL), and
  - an uncalibrated (no chain_depth field) window never confirms a breach.
Plus median == kernel reducer, top-session detection, idempotency (read-only), and an
order-independent tie-break. The kernel<->bin drift check lives in
agentica_core/tests/test_chain_depth_audit_drift.py.
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

from bin.chain_depth_audit import (  # type: ignore[import-not-found]
    audit,
    chain_depth_median,
    chain_depth_stats,
)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def _rec(chain_depth=None, *, platform: str = "claude", session_id: str = "s1") -> dict:
    rec: dict = {"platform": platform, "session_id": session_id}
    if chain_depth is not None:
        rec["chain_depth"] = chain_depth
    return rec


def _window(depths: list[int]) -> list[dict]:
    return [_rec(d, session_id=f"s{i}") for i, d in enumerate(depths)]


_FIXED_NOW = lambda: "2026-06-28T00:00:00+00:00"


def _audit(records, *, fail_threshold=5.0, **kw):
    return audit(records, fail_threshold=fail_threshold, now_fn=_FIXED_NOW, **kw)


# ---------------------------------------------------------------------------
# chain_depth_stats — the shared computation
# ---------------------------------------------------------------------------

class ChainDepthStats(unittest.TestCase):
    def test_no_field_is_uncalibrated(self):
        median, n_with_field, total = chain_depth_stats([_rec(), _rec()])
        self.assertIsNone(median)
        self.assertEqual((n_with_field, total), (0, 2))

    def test_median_of_field(self):
        # median of [2, 4, 6] = 4.0; two records lack the field and must not skew it.
        median, n_with_field, total = chain_depth_stats(_window([2, 4, 6]) + [_rec(), _rec()])
        self.assertEqual(median, 4.0)
        self.assertEqual((n_with_field, total), (3, 5))

    def test_bools_and_nonnumerics_dropped(self):
        # True is an int subclass but must be excluded; strings ignored.
        recs = [_rec(4), {"chain_depth": True}, {"chain_depth": "x"}, _rec(8)]
        median, n_with_field, _ = chain_depth_stats(recs)
        self.assertEqual((median, n_with_field), (6.0, 2))  # median of [4, 8]

    def test_median_equals_helper(self):
        recs = _window([1, 9, 5])
        self.assertEqual(chain_depth_median(recs), chain_depth_stats(recs)[0])


# ---------------------------------------------------------------------------
# audit — detect + verify
# ---------------------------------------------------------------------------

class Audit(unittest.TestCase):
    def test_uncalibrated_window_never_confirms_a_breach(self):
        # No record carries chain_depth -> uncalibrated, never a breach even though fail is low.
        report = _audit([_rec(), _rec()], fail_threshold=1.0)
        self.assertEqual(report["verdict"], "uncalibrated")
        self.assertFalse(report["breach_confirmed"])
        self.assertIsNone(report["chain_depth_median"])

    def test_calibrated_below_threshold_does_not_confirm(self):
        report = _audit(_window([1, 2, 3]))  # median 2 < fail 5
        self.assertEqual(report["verdict"], "below_threshold")
        self.assertFalse(report["breach_confirmed"])

    def test_calibrated_at_or_above_fail_confirms_breach(self):
        report = _audit(_window([4, 6, 600]))  # median 6 >= fail 5
        self.assertEqual(report["verdict"], "breach_confirmed")
        self.assertTrue(report["breach_confirmed"])
        self.assertEqual(report["chain_depth_median"], 6.0)

    def test_median_exactly_at_fail_confirms(self):
        report = _audit(_window([5, 5, 5]))  # median 5 == fail 5 -> >= confirms
        self.assertTrue(report["breach_confirmed"])

    def test_top_session_is_the_heaviest_fanout(self):
        report = _audit(_window([3, 999, 7]) , fail_threshold=5.0)
        top = report["top_session"]
        self.assertEqual(top["chain_depth"], 999)
        self.assertEqual(report["top_sessions"][0]["chain_depth"], 999)
        self.assertEqual(len(report["top_sessions"]), 3)

    def test_is_idempotent_read_only(self):
        records = _window([2, 6, 6, 10])
        self.assertEqual(_audit(records), _audit(records))

    def test_top_session_order_independent_on_tied_counts(self):
        # Two records with equal chain_depth must yield the same top_session regardless of order.
        a = _rec(8, platform="A", session_id="sa")
        b = _rec(8, platform="B", session_id="sb")
        pad = _window([1, 1])
        forward = _audit([a, b] + pad)["top_session"]
        reversed_ = _audit([b, a] + pad)["top_session"]
        self.assertEqual(forward, reversed_)


if __name__ == "__main__":
    unittest.main()
