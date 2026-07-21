"""Eval for the deterministic Slop_Density triage mechanism (bin/slop_strip.py).

Covers the verify gate (breach only when calibrated AND density >= FAIL), the uncalibrated guard
(no output words -> None, never a false breach), the density formula (markers per 1k words),
worst-session detect ordering, and idempotency. Kernel<->bin parity lives in
agentica_core/tests/test_slop_strip_drift.py.
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

from bin.slop_strip import slop_density_stats, triage  # type: ignore[import-not-found]


def _rec(slop, words, *, session_id: str = "s1", platform: str = "claude") -> dict:
    rec: dict = {"session_id": session_id, "platform": platform}
    if slop is not None:
        rec["slop_markers"] = slop
    if words is not None:
        rec["output_words"] = words
    return rec


_FIXED_NOW = lambda: "2026-06-29T00:00:00+00:00"


def _triage(records, *, fail_threshold=30.0, **kw):
    return triage(records, fail_threshold=fail_threshold, now_fn=_FIXED_NOW, **kw)


class SlopDensityStats(unittest.TestCase):
    def test_markers_per_1k_words(self):
        # 30 markers across 2000 words -> 15.0 per 1k.
        density, sw, ow = slop_density_stats([_rec(10, 1000), _rec(20, 1000)])
        self.assertEqual((density, sw, ow), (15.0, 30, 2000))

    def test_no_words_is_uncalibrated(self):
        density, sw, ow = slop_density_stats([_rec(5, 0), _rec(3, None)])
        self.assertIsNone(density)
        self.assertEqual((sw, ow), (8, 0))

    def test_rounds_to_two_decimals(self):
        density, _, _ = slop_density_stats([_rec(1, 700)])  # 1/700*1000 = 1.4285...
        self.assertEqual(density, 1.43)


class Triage(unittest.TestCase):
    def test_uncalibrated_never_confirms_a_breach(self):
        # high markers but zero words: density is None -> never a breach.
        report = _triage([_rec(99, 0)], fail_threshold=1.0)
        self.assertEqual(report["verdict"], "uncalibrated")
        self.assertFalse(report["breach_confirmed"])
        self.assertIsNone(report["slop_density"])

    def test_below_threshold_does_not_confirm(self):
        report = _triage([_rec(10, 1000)])  # 10 < fail 30
        self.assertEqual(report["verdict"], "below_threshold")
        self.assertFalse(report["breach_confirmed"])

    def test_at_or_above_fail_confirms_breach(self):
        report = _triage([_rec(40, 1000)])  # 40 >= fail 30
        self.assertEqual(report["verdict"], "breach_confirmed")
        self.assertTrue(report["breach_confirmed"])
        self.assertEqual(report["slop_density"], 40.0)

    def test_top_session_is_the_sloppiest(self):
        report = _triage([
            _rec(10, 1000, session_id="mild"),    # density 10
            _rec(80, 1000, session_id="awful"),   # density 80
            _rec(5, 0, session_id="nowords"),     # not rankable
        ])
        self.assertEqual(report["top_session"]["session_id"], "awful")
        self.assertEqual(report["top_session"]["slop_density"], 80.0)
        self.assertEqual(len(report["top_sessions"]), 2)  # nowords excluded

    def test_low_word_outliers_excluded_from_detect(self):
        # a 1-marker, 8-word reply has density 125 but must not be named the sloppiest session;
        # it is still counted in the aggregate density.
        report = _triage([_rec(1, 8, session_id="tiny"), _rec(40, 1000, session_id="real")])
        self.assertEqual(report["top_session"]["session_id"], "real")
        self.assertEqual([s["session_id"] for s in report["top_sessions"]], ["real"])

    def test_is_idempotent(self):
        records = [_rec(40, 1000, session_id="a"), _rec(20, 1000, session_id="b")]
        self.assertEqual(_triage(records), _triage(records))

    def test_top_session_order_independent_on_ties(self):
        a = _rec(50, 1000, session_id="sa", platform="A")
        b = _rec(50, 1000, session_id="sb", platform="B")
        self.assertEqual(_triage([a, b])["top_session"], _triage([b, a])["top_session"])


if __name__ == "__main__":
    unittest.main()
