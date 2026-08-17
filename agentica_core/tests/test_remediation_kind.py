"""Guards for insights.remediation_kind — the single source of truth that routes a reflex's
manual remediation into auto_fix | advisory | session_hygiene | mis_route.

Buckets (in precedence order):
  - explicit_kind (METRIC_CONFIG "kind") always wins → that's how mis_route is declared,
  - a SESSION_HYGIENE_SKILLS command → session_hygiene (live-session-only, no-op headless),
  - readonly OR auto_remediable is False → advisory (diagnostic; runs, no code change),
  - otherwise → auto_fix (code-modifying, goes through the staging pipeline).
"""
from __future__ import annotations

import unittest

from agentica_core.insights import (
    METRIC_CONFIG,
    SESSION_HYGIENE_SKILLS,
    remediation_kind,
)


class RemediationKind(unittest.TestCase):
    def test_auto_fix_code_modifying_skill(self):
        # /humanizer: not readonly, not flagged non-remediable, no explicit kind → auto_fix.
        self.assertEqual(
            remediation_kind("/humanizer", readonly=False, auto_remediable=None),
            "auto_fix",
        )

    def test_advisory_readonly_skill(self):
        # /insights flagged readonly → advisory (diagnostic, runs but produces no diff).
        self.assertEqual(
            remediation_kind("/insights", readonly=True, auto_remediable=None),
            "advisory",
        )

    def test_advisory_non_auto_remediable(self):
        # /investigate with auto_remediable=False → advisory even when not readonly-flagged.
        self.assertEqual(
            remediation_kind("/investigate", readonly=False, auto_remediable=False),
            "advisory",
        )

    def test_session_hygiene_by_skill(self):
        # /context-optimization is a live-session skill → session_hygiene regardless of flags.
        self.assertEqual(
            remediation_kind("/context-optimization", readonly=False, auto_remediable=None),
            "session_hygiene",
        )
        self.assertEqual(
            remediation_kind("/compact", readonly=True, auto_remediable=None),
            "session_hygiene",
        )

    def test_explicit_kind_wins(self):
        # METRIC_CONFIG "kind" overrides everything — this is how mis_route is expressed.
        self.assertEqual(
            remediation_kind(
                "/self-heal", readonly=False, auto_remediable=False, explicit_kind="mis_route"
            ),
            "mis_route",
        )

    def test_explicit_kind_overrides_session_hygiene(self):
        # Even a session-hygiene command yields to an explicit kind (precedence guard).
        self.assertEqual(
            remediation_kind(
                "/context-optimization", readonly=False, auto_remediable=None,
                explicit_kind="mis_route",
            ),
            "mis_route",
        )

    def test_missing_command_is_auto_fix(self):
        # Absent command (nudge with no mapping) falls through to auto_fix, not a crash.
        self.assertEqual(
            remediation_kind(None, readonly=False, auto_remediable=None),
            "auto_fix",
        )
        self.assertEqual(
            remediation_kind("", readonly=False, auto_remediable=None),
            "auto_fix",
        )

    def test_command_with_args_uses_first_word(self):
        # Commands can carry args ("/context-optimization --aggressive"); classify by skill word.
        self.assertEqual(
            remediation_kind(
                "/context-optimization --aggressive", readonly=False, auto_remediable=None
            ),
            "session_hygiene",
        )

    def test_four_do_not_use_metrics_declare_mis_route(self):
        # The four DO-NOT-USE entries carry kind=mis_route in METRIC_CONFIG (DRY declaration).
        for metric in (
            "Agent_Process_Count",
            "Boundary_Violations",
            "Kill_Chains_Open",
            "Local_Routing_Share",
        ):
            with self.subTest(metric=metric):
                cfg = METRIC_CONFIG[metric]
                self.assertEqual(cfg.get("kind"), "mis_route")
                # And the classifier honors it end-to-end.
                self.assertEqual(
                    remediation_kind(
                        cfg["command"],
                        readonly=cfg.get("readonly", False),
                        auto_remediable=cfg.get("auto_remediable"),
                        explicit_kind=cfg.get("kind"),
                    ),
                    "mis_route",
                )

    def test_unproven_simplifiers_cannot_run_autonomously(self):
        # These metrics previously attracted broad source-rewrite scripts that had
        # no metric-specific proof and damaged unrelated files. They remain visible
        # and manually actionable, but may not enter the autonomous fire path or
        # advertise a deterministic mechanism until a discriminating eval exists.
        for metric in ("Simplify_Age", "Revision_Ratio"):
            with self.subTest(metric=metric):
                cfg = METRIC_CONFIG[metric]
                self.assertIs(cfg.get("auto_remediable"), False)
                self.assertNotIn("mechanism", cfg)
                self.assertEqual(
                    remediation_kind(
                        cfg["command"],
                        readonly=cfg.get("readonly", False),
                        auto_remediable=cfg.get("auto_remediable"),
                        explicit_kind=cfg.get("kind"),
                    ),
                    "advisory",
                )

    def test_session_hygiene_skills_membership(self):
        # Guard the set stays what the engine/UI expect (context-optimization + compact).
        self.assertEqual(SESSION_HYGIENE_SKILLS, {"context-optimization", "compact"})

    def test_slash_only_or_whitespace_command_does_not_crash(self):
        # A command that is just "/" (or all-whitespace) strips/splits down to an
        # empty word list — `.split()[0]` on that raises IndexError instead of
        # falling through to the same no-mapping default a missing command gets.
        self.assertEqual(
            remediation_kind("/", readonly=False, auto_remediable=None),
            "auto_fix",
        )
        self.assertEqual(
            remediation_kind("   ", readonly=False, auto_remediable=None),
            "auto_fix",
        )


if __name__ == "__main__":
    unittest.main()
