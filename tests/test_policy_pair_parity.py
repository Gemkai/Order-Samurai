"""Structural parity guard for the repo/claude policy pairs in config/.

Each policy family ships as a pair — ``X.json`` governs this repo,
``claude_X.json`` governs the ``~/.claude`` runtime. The pairs intentionally
diverge in VOCABULARY (rule/category IDs, classification names, lifecycle
states) but share a structural SKELETON. Nothing previously asserted that
skeleton, so a malformed edit on the claude side (5 of 6 claude policies have
no code consumer yet) was invisible until a future verifier crashed on it.

These tests assert required structural keys on BOTH sides of each pair —
never key-set equality and never value/vocabulary equality — so legitimate
per-surface evolution stays green and only skeleton/schema drift goes red.

Notes:
- ``forbiddenPatterns`` entries are plain strings. Consumers MUST match them
  as substrings (see ``verify_no_stale_paths._literal_in``) or wrap them in
  ``re.escape`` (see ``verify_archive_boundaries``); raw ``re.compile`` on the
  retained Windows entries raises ``re.error`` (``\\U`` escape).
- The surface-matrix check asserts ``unknown_role == []``, which is stricter
  than the live gate (``verify_surface_governance`` only WARNs on unknown
  roles). Intentional tightening: a brand-new role should be declared in
  ``surfaceRoles`` in the same edit that introduces it.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from execution.claude_runtime_target import pinned_home_paths  # type: ignore[attr-defined]
from execution.verify_root_hygiene import (  # type: ignore[attr-defined]
    validate_root_hygiene_policy,
)
from execution.verify_surface_governance import (  # type: ignore[attr-defined]
    validate_surface_entries,
)


CONFIG = REPO_ROOT / "config"

PAIR_FAMILIES = {
    "architecture_scorecard": ("architecture_scorecard.json", "claude_architecture_scorecard.json"),
    "anti_drift": ("anti_drift_policy.json", "claude_anti_drift_policy.json"),
    "anti_sprawl": ("anti_sprawl_policy.json", "claude_anti_sprawl_policy.json"),
    "root_hygiene": ("root_hygiene_policy.json", "claude_root_hygiene_policy.json"),
    "promotion": ("promotion_policy.json", "claude_promotion_policy.json"),
}

SURFACE_MATRICES = (
    "agentica_surface_matrix.json",
    "claude_surface_matrix.json",
    "hub_surface_matrix.json",
)

#: The claude-side policies declaring their audit target at the top level.
#: claude_architecture_scorecard.json nests its own at target.runtimeRoot and is
#: therefore handled separately by every test that reads a declared root.
CLAUDE_POLICIES_WITH_TOP_LEVEL_ROOT = (
    "claude_anti_drift_policy.json",
    "claude_anti_sprawl_policy.json",
    "claude_root_hygiene_policy.json",
    "claude_promotion_policy.json",
    "claude_surface_matrix.json",
)

ARTIFACT_KEY_RE = re.compile(r"^(expected|required).*Artifacts$")


def load(name: str) -> dict:
    return json.loads((CONFIG / name).read_text(encoding="utf-8"))


class PolicySanityTests(unittest.TestCase):
    def test_every_policy_loads_with_name_and_version(self) -> None:
        names = [n for pair in PAIR_FAMILIES.values() for n in pair] + list(SURFACE_MATRICES)
        for name in names:
            with self.subTest(policy=name):
                payload = load(name)
                self.assertIsInstance(payload, dict)
                self.assertTrue(payload.get("name"))
                self.assertIsInstance(payload.get("version"), int)

    def test_claude_policies_pin_a_runtime_root(self) -> None:
        # claude_architecture_scorecard nests its root at target.runtimeRoot;
        # the other claude files declare top-level targetRuntimeRoot.
        for name in CLAUDE_POLICIES_WITH_TOP_LEVEL_ROOT:
            with self.subTest(policy=name):
                root = load(name).get("targetRuntimeRoot")
                self.assertIsInstance(root, str)
                self.assertTrue(str(root).startswith(("/", "~")), f"not an absolute/~ path: {root!r}")
        scorecard_root = load("claude_architecture_scorecard.json").get("target", {}).get("runtimeRoot")
        self.assertTrue(scorecard_root and isinstance(scorecard_root, str))

    def test_claude_policy_roots_are_portable_not_pinned_to_one_machine(self) -> None:
        """A declared root must never name a specific user's home.

        Until 2026-07-31 all six carried this build machine's own
        "/Users/<owner>/.claude", so the shipped product resolved its audit
        target to a path that exists on exactly one Mac, and the public export
        depended on the scrubber rewriting it. "~/.claude" is the portable form
        every consumer already expands (CLAUDE_RUNTIME_ROOT overrides it).
        """
        declared = {
            name: load(name).get("targetRuntimeRoot")
            for name in CLAUDE_POLICIES_WITH_TOP_LEVEL_ROOT
        }
        declared["claude_architecture_scorecard.json"] = (
            load("claude_architecture_scorecard.json").get("target", {}).get("runtimeRoot")
        )
        for name, root in declared.items():
            with self.subTest(policy=name):
                self.assertEqual(
                    pinned_home_paths(str(root), ".claude"),
                    [],
                    f"{name} pins a machine-specific home: {root!r}",
                )


class ScorecardFamilyTests(unittest.TestCase):
    def test_scorecard_skeleton(self) -> None:
        for name in PAIR_FAMILIES["architecture_scorecard"]:
            with self.subTest(policy=name):
                payload = load(name)
                self.assertIn("scoring", payload)
                self.assertIn("categories", payload)
                for key in ("enforcementMode", "mergeFloor", "releaseFloor", "targetScore"):
                    self.assertIn(key, payload["scoring"])
                ids = [c["id"] for c in payload["categories"]]
                self.assertEqual(len(ids), len(set(ids)), "duplicate category ids")
                for cat in payload["categories"]:
                    for key in ("id", "label", "weight", "target", "requiredVerifiers"):
                        self.assertIn(key, cat, f"category {cat.get('id')} missing {key}")
                self.assertEqual(
                    sum(c["weight"] for c in payload["categories"]),
                    payload["scoring"]["targetScore"],
                    "category weights must sum to targetScore",
                )


class RulePolicyFamilyTests(unittest.TestCase):
    def _assert_rules_skeleton(self, name: str) -> dict:
        payload = load(name)
        self.assertIn("scope", payload)
        self.assertIn("rules", payload)
        ids = [r.get("id") for r in payload["rules"]]
        self.assertNotIn(None, ids, f"rule without id in {name}")
        self.assertEqual(len(ids), len(set(ids)), f"duplicate rule ids in {name}")
        for rule in payload["rules"]:
            for key in ("id", "severity", "statement", "verifier"):
                self.assertTrue(rule.get(key), f"rule {rule.get('id')} missing {key} in {name}")
            artifact_keys = [
                k for k, v in rule.items()
                if ARTIFACT_KEY_RE.match(k) and isinstance(v, list) and v
            ]
            self.assertTrue(
                artifact_keys,
                f"rule {rule['id']} in {name} declares no non-empty *Artifacts list",
            )
        return payload

    def test_anti_drift_skeleton(self) -> None:
        for name in PAIR_FAMILIES["anti_drift"]:
            with self.subTest(policy=name):
                payload = self._assert_rules_skeleton(name)
                for key in ("principles", "mergeGates", "releaseGates"):
                    self.assertIsInstance(payload.get(key), list, f"{name} missing list {key}")

    def test_anti_sprawl_skeleton(self) -> None:
        for name in PAIR_FAMILIES["anti_sprawl"]:
            with self.subTest(policy=name):
                payload = self._assert_rules_skeleton(name)
                self.assertIn("surfaceRoles", payload)
                warning_model = payload.get("warningModel", {})
                self.assertIn("warnOn", warning_model)
                self.assertIn("failOn", warning_model)


class RootHygieneFamilyTests(unittest.TestCase):
    def test_root_hygiene_skeleton(self) -> None:
        for name in PAIR_FAMILIES["root_hygiene"]:
            with self.subTest(policy=name):
                payload = load(name)
                for key in ("directories", "files", "requiredDirectories",
                            "requiredFiles", "boundaryRules"):
                    self.assertIn(key, payload)
                for section in ("directories", "files"):
                    for classification, entries in payload[section].items():
                        self.assertIsInstance(entries, list, f"{name} {section}.{classification}")
                        for entry in entries:
                            self.assertTrue(
                                entry and isinstance(entry, str),
                                f"empty/non-str entry in {name} {section}.{classification}",
                            )
                for rule in payload["boundaryRules"]:
                    self.assertTrue(rule.get("name"), f"unnamed boundary rule in {name}")
                    self.assertTrue(rule.get("scanPaths"), f"{rule.get('name')} has no scanPaths")
                    targets = rule.get("forbiddenRoots") or rule.get("forbiddenPatterns")
                    self.assertTrue(
                        targets and all(t and isinstance(t, str) for t in targets),
                        f"{rule.get('name')} in {name} needs a non-empty "
                        "forbiddenRoots or forbiddenPatterns list",
                    )

    def test_repo_policy_passes_production_validator(self) -> None:
        # Repo side only: the shared validator's classification vocabulary and
        # requiredDirectories existence checks are repo-rooted by design; the
        # claude policy targets ~/.claude and must never be fed to it.
        payload = load("root_hygiene_policy.json")
        self.assertEqual(
            validate_root_hygiene_policy(payload=payload, repo_root=REPO_ROOT), [],
        )


class PromotionFamilyTests(unittest.TestCase):
    def test_promotion_skeleton(self) -> None:
        for name in PAIR_FAMILIES["promotion"]:
            with self.subTest(policy=name):
                payload = load(name)
                for key in ("goal", "lifecycleStates", "promotionChecklist",
                            "blockers", "retirementPolicy"):
                    self.assertIn(key, payload)
                for item in payload["promotionChecklist"]:
                    for key in ("id", "required", "statement"):
                        self.assertIn(key, item)
                self.assertTrue(
                    {"candidate", "runtime", "deprecated", "archive"}
                    <= set(payload["lifecycleStates"]),
                    f"{name} lifecycleStates missing shared core states",
                )
                self.assertIn("requirements", payload["retirementPolicy"])


class SurfaceMatrixTests(unittest.TestCase):
    def test_all_matrices_pass_production_validator(self) -> None:
        for name in SURFACE_MATRICES:
            with self.subTest(matrix=name):
                incomplete, unknown_role = validate_surface_entries(load(name))
                self.assertEqual(incomplete, [])
                self.assertEqual(unknown_role, [])


if __name__ == "__main__":
    unittest.main()
