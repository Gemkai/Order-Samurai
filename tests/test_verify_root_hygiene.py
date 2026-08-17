from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from execution.verify_root_hygiene import (  # type: ignore[attr-defined]
    find_unclassified_root_entries,
    index_declared_root_entries,
    pack_audit_profile,
    resolve_required_sections,
    summarize,
    validate_root_hygiene_policy,
)

#: These cases assert the validator's own logic against hand-built payloads, so
#: they pin the tier instead of inheriting it. Left to inherit, the same payloads
#: would be read at the baseline tier inside the exported (standalone) tree, hit
#: keys they never declare, and assert something different there than here.
_FULL_SECTIONS = ("requiredDirectories", "requiredFiles")


class VerifyRootHygieneTests(unittest.TestCase):
    def test_validate_root_hygiene_policy_reports_missing_required_directory(self) -> None:
        sandbox = REPO_ROOT / ".tmp" / "test_verify_root_hygiene" / self._testMethodName
        sandbox.mkdir(parents=True, exist_ok=True)
        payload = {
            "directories": {"live": ["execution"]},
            "files": {},
            "requiredDirectories": ["execution"],
            "requiredFiles": [],
            "boundaryRules": [],
        }

        failures = validate_root_hygiene_policy(
            payload=payload, repo_root=sandbox, sections=_FULL_SECTIONS
        )

        self.assertEqual(failures, ["root_hygiene_policy: execution"])

    def test_validate_root_hygiene_policy_handles_null_directories_key(self) -> None:
        # A "directories" key present with an explicit JSON null (not absent)
        # must not crash: dict.get(key, default) only substitutes default when
        # the key is absent, not when it's present with value None.
        sandbox = REPO_ROOT / ".tmp" / "test_verify_root_hygiene" / self._testMethodName
        sandbox.mkdir(parents=True, exist_ok=True)
        payload = {
            "directories": None,
            "files": {},
            "requiredDirectories": [],
            "requiredFiles": [],
            "boundaryRules": [],
        }

        failures = validate_root_hygiene_policy(
            payload=payload, repo_root=sandbox, sections=_FULL_SECTIONS
        )

        self.assertEqual(failures, [])

    def test_validate_root_hygiene_policy_handles_null_entries_list(self) -> None:
        # A classification bucket present with an explicit JSON null entry
        # list must not crash iterating over it.
        sandbox = REPO_ROOT / ".tmp" / "test_verify_root_hygiene" / self._testMethodName
        sandbox.mkdir(parents=True, exist_ok=True)
        payload = {
            "directories": {"live": None},
            "files": {},
            "requiredDirectories": [],
            "requiredFiles": [],
            "boundaryRules": [],
        }

        failures = validate_root_hygiene_policy(
            payload=payload, repo_root=sandbox, sections=_FULL_SECTIONS
        )

        self.assertEqual(failures, [])

    def test_index_declared_root_entries_handles_null_section(self) -> None:
        payload = {"directories": None, "files": None}

        declared = index_declared_root_entries(payload=payload)

        self.assertEqual(declared, set())

    def test_find_unclassified_root_entries_reports_unknown_directory(self) -> None:
        sandbox = REPO_ROOT / ".tmp" / "test_verify_root_hygiene" / self._testMethodName
        (sandbox / "config").mkdir(parents=True, exist_ok=True)
        (sandbox / "mystery").mkdir(parents=True, exist_ok=True)

        warnings = find_unclassified_root_entries(
            repo_root=sandbox,
            declared_entries={"config"},
        )

        self.assertEqual(warnings, ["mystery"])

    def test_validate_root_hygiene_policy_reports_invalid_classification(self) -> None:
        sandbox = REPO_ROOT / ".tmp" / "test_verify_root_hygiene" / self._testMethodName
        (sandbox / "config").mkdir(parents=True, exist_ok=True)
        payload = {
            "directories": {"mystery": ["config"]},
            "files": {},
            "requiredDirectories": [],
            "requiredFiles": [],
            "boundaryRules": [],
        }

        failures = validate_root_hygiene_policy(
            payload=payload, repo_root=sandbox, sections=_FULL_SECTIONS
        )

        self.assertEqual(failures, ["root_hygiene_policy: invalid classification mystery"])

    def test_summarize_sets_nonzero_exit_for_failures(self) -> None:
        counts, exit_code = summarize(
            [
                {"status": "OK", "label": "a", "detail": "x"},
                {"status": "FAIL", "label": "b", "detail": "y"},
            ]
        )

        self.assertEqual(counts["OK"], 1)
        self.assertEqual(counts["FAIL"], 1)
        self.assertEqual(exit_code, 1)


class RequirementTierTests(unittest.TestCase):
    """backlog/, reports/, PROJECT.md and RONIN_SPEC.md are this repo's charter and
    workflow, not universal invariants. Measured before this tier existed: an
    adopter who deleted PROJECT.md and RONIN_SPEC.md from a standalone pack got
    `FAIL=1, exit 1` -- a red gate about documents, not about their code.
    """

    def _sandbox(self) -> Path:
        sandbox = REPO_ROOT / ".tmp" / "test_root_hygiene_tiers" / self._testMethodName
        (sandbox / "config").mkdir(parents=True, exist_ok=True)
        (sandbox / "execution").mkdir(parents=True, exist_ok=True)
        return sandbox

    def _payload(self) -> dict:
        """A pack that has the essentials but none of this repo's charter docs."""
        return {
            "directories": {"live": ["execution"], "support": ["config", "backlog", "reports"]},
            "files": {"support": ["PROJECT.md", "RONIN_SPEC.md"]},
            "requiredDirectories": ["backlog", "config", "execution", "reports"],
            "requiredFiles": ["PROJECT.md", "RONIN_SPEC.md"],
            "baselineRequiredDirectories": ["config", "execution"],
            "baselineRequiredFiles": [],
            "boundaryRules": [],
        }

    def test_baseline_tier_accepts_a_pack_without_this_repos_charter_documents(self) -> None:
        failures = validate_root_hygiene_policy(
            payload=self._payload(),
            repo_root=self._sandbox(),
            sections=("baselineRequiredDirectories", "baselineRequiredFiles"),
        )

        self.assertEqual(failures, [])

    def test_full_tier_still_demands_them(self) -> None:
        failures = validate_root_hygiene_policy(
            payload=self._payload(), repo_root=self._sandbox(), sections=_FULL_SECTIONS
        )

        self.assertIn("root_hygiene_policy: PROJECT.md", failures)
        self.assertIn("root_hygiene_policy: RONIN_SPEC.md", failures)

    def test_baseline_tier_still_demands_what_the_tool_cannot_run_without(self) -> None:
        """Lenient is not vacuous: without config/ there is no policy to enforce."""
        sandbox = self._sandbox()
        (sandbox / "config").rmdir()

        failures = validate_root_hygiene_policy(
            payload=self._payload(),
            repo_root=sandbox,
            sections=("baselineRequiredDirectories", "baselineRequiredFiles"),
        )

        self.assertEqual(failures, ["root_hygiene_policy: config"])

    def test_a_policy_missing_the_selected_tier_fails_loudly(self) -> None:
        """The trap this tier could have introduced: reading a key the policy never
        declares yields an empty list and asserts nothing, so the check reports OK
        while enforcing no requirement at all."""
        payload = self._payload()
        del payload["baselineRequiredDirectories"]

        failures = validate_root_hygiene_policy(
            payload=payload,
            repo_root=self._sandbox(),
            sections=("baselineRequiredDirectories", "baselineRequiredFiles"),
        )

        self.assertIn(
            "root_hygiene_policy: policy declares no baselineRequiredDirectories", failures
        )

    def test_an_empty_tier_is_a_declaration_and_is_honoured(self) -> None:
        """Absent and empty are different: [] says 'nothing required', which is a
        real answer; a missing key says the policy does not support this tier."""
        failures = validate_root_hygiene_policy(
            payload=self._payload(),
            repo_root=self._sandbox(),
            sections=("baselineRequiredDirectories", "baselineRequiredFiles"),
        )

        self.assertNotIn("root_hygiene_policy: policy declares no baselineRequiredFiles", failures)

    def test_a_development_checkout_gets_the_strict_tier_with_no_variable_set(self) -> None:
        """The whole point of deriving the tier. ORDER_SAMURAI_AUDIT_PROFILE has
        existed since 2026-07-31 and nothing outside tests has ever set it, so a
        strict tier that depends on someone remembering is a tier that is off.
        """
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ORDER_SAMURAI_AUDIT_PROFILE", None)
            self.assertEqual(pack_audit_profile(standalone=False), "full")

    def test_a_standalone_pack_gets_the_lenient_tier_with_no_variable_set(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ORDER_SAMURAI_AUDIT_PROFILE", None)
            self.assertEqual(pack_audit_profile(standalone=True), "baseline")

    def test_an_explicit_profile_overrides_the_derived_one_in_both_layouts(self) -> None:
        for standalone, override in ((False, "baseline"), (True, "full")):
            with self.subTest(standalone=standalone):
                with mock.patch.dict(os.environ, {"ORDER_SAMURAI_AUDIT_PROFILE": override}):
                    self.assertEqual(pack_audit_profile(standalone=standalone), override)

    def test_the_resolved_sections_follow_the_ambient_layout(self) -> None:
        """Portable: asserts the wiring holds in whichever tree this suite runs in,
        rather than asserting one tree's answer in both."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ORDER_SAMURAI_AUDIT_PROFILE", None)
            expected = _FULL_SECTIONS if pack_audit_profile() == "full" else (
                "baselineRequiredDirectories", "baselineRequiredFiles"
            )
            self.assertEqual(resolve_required_sections(), expected)

    def test_the_shipped_policy_supports_both_tiers(self) -> None:
        """Guards the pairing: adding a tier to the verifier without adding its
        keys to the policy is what turns the lenient run vacuous."""
        policy = json.loads(
            (REPO_ROOT / "config" / "root_hygiene_policy.json").read_text(encoding="utf-8")
        )

        for key in ("requiredDirectories", "requiredFiles",
                    "baselineRequiredDirectories", "baselineRequiredFiles"):
            with self.subTest(key=key):
                self.assertIn(key, policy)


if __name__ == "__main__":
    unittest.main()
