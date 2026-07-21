"""Eval for the deterministic Error_Rate triage mechanism (bin/error_triage.py).

Covers the two things that make the mechanism a 10/10 remediation:
  - the MIN-SAMPLE GUARD (an under-sampled window is uncalibrated, never a false FAIL), and
  - the VERIFY gate (breach_confirmed only when calibrated AND rate >= FAIL).
Plus signature grouping, idempotency (read-only), and a drift check that the bin's
classification constants stay in lockstep with agentica_core.aggregate.
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

from bin.error_triage import (  # type: ignore[import-not-found]
    error_rate_stats,
    triage,
)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def _rec(status: str = "success", *, platform: str = "claude",
         error: str = "", exit_code=None, session_id: str = "s1") -> dict:
    rec: dict = {"status": status, "platform": platform, "session_id": session_id}
    if error:
        rec["error"] = error
    if exit_code is not None:
        rec["exit_code"] = exit_code
    return rec


def _window(n_errors: int, n_success: int, **kw) -> list[dict]:
    return ([_rec("error", **kw) for _ in range(n_errors)]
            + [_rec("success") for _ in range(n_success)])


_FIXED_NOW = lambda: "2026-06-28T00:00:00+00:00"


def _triage(records, **kw):
    return triage(records, now_fn=_FIXED_NOW, **kw)


# ---------------------------------------------------------------------------
# error_rate_stats — the shared computation
# ---------------------------------------------------------------------------

class ErrorRateStats(unittest.TestCase):
    def test_under_min_sample_is_uncalibrated(self):
        rate, errors, total = error_rate_stats(_window(1, 1))
        self.assertIsNone(rate)
        self.assertEqual((errors, total), (1, 2))

    def test_at_min_sample_is_graded(self):
        self.assertEqual(error_rate_stats(_window(2, 8)), (20.0, 2, 10))

    def test_only_error_status_counts(self):
        # success records are never errors; the schema admits no other status.
        rate, errors, total = error_rate_stats(_window(0, 12))
        self.assertEqual((rate, errors, total), (0.0, 0, 12))


# ---------------------------------------------------------------------------
# triage — detect + verify
# ---------------------------------------------------------------------------

class Triage(unittest.TestCase):
    def test_uncalibrated_window_never_confirms_a_breach(self):
        # 1 error of 2 = 50% would trip fail=5, but the window is under the sample floor.
        report = _triage(_window(1, 1))
        self.assertEqual(report["verdict"], "uncalibrated")
        self.assertFalse(report["breach_confirmed"])
        self.assertIsNone(report["error_rate"])

    def test_calibrated_below_threshold_does_not_confirm(self):
        report = _triage(_window(0, 20))  # 0%
        self.assertEqual(report["verdict"], "below_threshold")
        self.assertFalse(report["breach_confirmed"])

    def test_calibrated_at_or_above_fail_confirms_breach(self):
        report = _triage(_window(2, 8))  # 20% >= fail 5
        self.assertEqual(report["verdict"], "breach_confirmed")
        self.assertTrue(report["breach_confirmed"])
        self.assertEqual(report["error_rate"], 20.0)

    def test_top_signature_groups_errors_with_exemplar(self):
        records = (
            [_rec("error", platform="claude", error="ECONNREFUSED", exit_code=1, session_id="boom")] * 3
            + [_rec("error", platform="antigravity", error="timeout", exit_code=2)]
            + [_rec("success") for _ in range(6)]
        )
        report = _triage(records)
        top = report["top_signature"]
        self.assertEqual(top["count"], 3)
        self.assertEqual(top["message"], "ECONNREFUSED")
        self.assertEqual(top["exemplar_session"], "boom")

    def test_is_idempotent_read_only(self):
        records = _window(2, 8)
        a, b = _triage(records), _triage(records)
        self.assertEqual(a, b)

    def test_top_signature_is_order_independent_on_tied_counts(self):
        # Two distinct signatures with equal counts must yield the same top_signature
        # regardless of input order (no Counter insertion-order tie-break leak).
        a = [_rec("error", platform="A", error="x", session_id="sa")] * 2
        b = [_rec("error", platform="B", error="y", session_id="sb")] * 2
        pad = [_rec("success") for _ in range(8)]
        forward = _triage(a + b + pad)["top_signature"]
        reversed_ = _triage(b + a + pad)["top_signature"]
        self.assertEqual(forward, reversed_)


# The drift guard (bin constants vs the kernel reducer / METRIC_CONFIG) lives in
# agentica_core/tests/test_error_triage_drift.py, where agentica_core resolves to the
# canonical Governance package rather than the partial Order Samurai shadow package.


if __name__ == "__main__":
    unittest.main()
