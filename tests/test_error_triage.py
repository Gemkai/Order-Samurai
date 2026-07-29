"""Eval for the deterministic Error_Rate triage mechanism (bin/error_triage.py).

Covers the two things that make the mechanism a 10/10 remediation:
  - the MIN-SAMPLE GUARD (an under-sampled window is uncalibrated, never a false FAIL), and
  - the VERIFY gate (breach_confirmed only when calibrated AND rate >= FAIL).
Plus signature grouping, idempotency (read-only), and a drift check that the bin's
classification constants stay in lockstep with agentica_core.aggregate.
"""
from __future__ import annotations

import itertools
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

_SID_SEQ = itertools.count()


def _rec(status: str = "success", *, platform: str = "claude",
         error: str = "", exit_code=None, session_id: str | None = None) -> dict:
    # Per-session semantics: each record defaults to its OWN session so a window of
    # N records is N sessions unless a test deliberately shares a session_id.
    sid = session_id if session_id is not None else f"sid-{next(_SID_SEQ)}"
    rec: dict = {"status": status, "platform": platform, "session_id": sid}
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

    def test_all_success_without_exit_code_is_unfalsifiable(self):
        # An emitter that has never reported an error OR stamped an exit_code gives
        # no evidence the error channel is wired — uncalibrated, not a measured 0%.
        rate, errors, total = error_rate_stats(_window(0, 12))
        self.assertEqual((rate, errors, total), (None, 0, 12))

    def test_all_success_with_exit_code_is_a_real_zero(self):
        # exit_code presence proves the channel is wired -> 0.0 is a measurement.
        records = [_rec("success") for _ in range(11)]
        records[0]["exit_code"] = 0
        rate, errors, total = error_rate_stats(records)
        self.assertEqual((rate, errors, total), (0.0, 0, 11))

    def test_session_reemits_do_not_dilute_the_rate(self):
        # One success session re-emitted 28x must count ONCE in the denominator.
        # Record counting read 1/37 = 2.7%; session counting reads 1/10 = 10%.
        records = ([_rec("success", session_id="mega") for _ in range(28)]
                   + [_rec("error")]
                   + [_rec("success") for _ in range(8)])
        self.assertEqual(error_rate_stats(records), (10.0, 1, 10))

    def test_session_with_any_error_record_is_one_error_session(self):
        # A session that logged 5 successes and 1 error is an error session, once.
        records = ([_rec("success", session_id="flaky") for _ in range(5)]
                   + [_rec("error", session_id="flaky")]
                   + [_rec("success") for _ in range(9)])
        self.assertEqual(error_rate_stats(records), (10.0, 1, 10))

    def test_placeholder_session_id_records_count_individually(self):
        # "local-session" is a placeholder, not an identity — 12 such records are
        # 12 samples (one antigravity task-run each), never one collapsed session.
        records = ([_rec("error", session_id="local-session") for _ in range(2)]
                   + [_rec("success", session_id="local-session") for _ in range(10)])
        self.assertEqual(error_rate_stats(records), (16.7, 2, 12))


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
        # exit_code stamps make the all-success window a REAL measured 0% (falsifiability guard)
        records = [_rec("success") for _ in range(20)]
        for r in records:
            r["exit_code"] = 0
        report = _triage(records)  # 0%
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

    def test_whitespace_only_error_message_does_not_crash(self):
        # A truthy-but-whitespace `error` field passes the `if rec.get("error")`
        # guard but strips to "" — building the signature must degrade to
        # "(no message)", not raise IndexError from "".splitlines()[0].
        records = _window(10, 2, error="   ")
        report = _triage(records)  # must not raise
        self.assertEqual(report["top_signature"]["message"], "(no message)")
        self.assertEqual(report["top_signature"]["count"], 10)


# The drift guard (bin constants vs the kernel reducer / METRIC_CONFIG) lives in
# agentica_core/tests/test_error_triage_drift.py, where agentica_core resolves to the
# canonical Governance package rather than the partial Order Samurai shadow package.


if __name__ == "__main__":
    unittest.main()
