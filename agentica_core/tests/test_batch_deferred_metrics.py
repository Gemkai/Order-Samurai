"""Guards for insights.batch_deferred_metrics — the reflex fire-path batch/verify-gate set.

This set is the contract behind state/batch_metrics.json, which the TS ReflexEngine reads to
(1) live-verify a breach before spawning an expensive code-modifying skill and (2) defer that
spawn to REFLEX_BATCH_WINDOW. The rules that keep it correct:
  - deterministic-mechanism metrics stay real-time (never batched),
  - readonly/diagnostic metrics are excluded (no code change to defer),
  - auto_remediable=False metrics never appear (they never fire autonomously),
  - an `urgent` flag keeps a metric real-time.
"""
from __future__ import annotations

import unittest

from agentica_core.insights import METRIC_CONFIG, batch_deferred_metrics


class BatchDeferredMetrics(unittest.TestCase):
    def test_excludes_mechanism_backed(self):
        # Metrics with a deterministic mechanism get a fast real-time path — never batched.
        b = set(batch_deferred_metrics())
        for m, cfg in METRIC_CONFIG.items():
            if "mechanism" in cfg:
                self.assertNotIn(m, b, f"{m} has a mechanism and must not be batch-deferred")

    def test_excludes_readonly(self):
        b = set(batch_deferred_metrics())
        for m, cfg in METRIC_CONFIG.items():
            if cfg.get("readonly"):
                self.assertNotIn(m, b, f"{m} is readonly and must not be batch-deferred")

    def test_excludes_non_auto_remediable(self):
        b = set(batch_deferred_metrics())
        for m, cfg in METRIC_CONFIG.items():
            if cfg.get("auto_remediable") is False:
                self.assertNotIn(m, b, f"{m} is non-auto-remediable and must not be batched")

    def test_members_are_code_modifying_agent_remediations(self):
        # Every member: auto-remediable, has a skill+command, not readonly, no mechanism.
        for m in batch_deferred_metrics():
            cfg = METRIC_CONFIG[m]
            self.assertIsNot(cfg.get("auto_remediable"), False)
            self.assertTrue(cfg.get("skill"))
            self.assertTrue(cfg.get("command"))
            self.assertFalse(cfg.get("readonly"))
            self.assertNotIn("mechanism", cfg)

    def test_urgent_flag_keeps_realtime(self):
        # An `urgent` metric is held out even if it otherwise qualifies (security hotfix path).
        mc = {
            "Fake_Urgent": {"skill": "x", "command": "/x", "dir": "lower", "warn": 1, "fail": 2,
                            "urgent": True},
            "Fake_Batch": {"skill": "y", "command": "/y", "dir": "lower", "warn": 1, "fail": 2},
        }
        out = batch_deferred_metrics(mc)
        self.assertIn("Fake_Batch", out)
        self.assertNotIn("Fake_Urgent", out)

    def test_known_expectations(self):
        # Representative members / non-members from THIS pack's METRIC_CONFIG.
        b = set(batch_deferred_metrics())
        self.assertIn("Doc_Parity_Issues", b)        # code-modifying, no mechanism
        self.assertIn("Governance_Pass_Rate", b)
        self.assertNotIn("Secrets_Detected", b)       # has secret_scrub mechanism
        self.assertNotIn("Chain_Depth_Avg", b)        # has chain_depth_audit mechanism
        self.assertNotIn("Avg_Session_Turns", b)      # readonly diagnostic


if __name__ == "__main__":
    unittest.main()
