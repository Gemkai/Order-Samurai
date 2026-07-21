"""Tests for the generated-truth producers and their verifier.

Covers execution/sync_inventory.py, execution/sync_capability_manifest.py and
execution/verify_generated_truth.py — the `generated-truth-over-manual-inventory`
mechanism: generated artifacts answer existence questions; hand-maintained files
answer intent. Fixtures use temp dirs and injected policies; no wall-clock, no
repo state dependence, mtimes set explicitly via os.utime.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution import sync_capability_manifest, sync_inventory, verify_generated_truth  # noqa: E402


POLICY = {
    "directories": {
        "live": ["execution", "bin"],
        "support": ["docs", "config"],
        "archive": ["Research"],
        "state": ["state"],
    },
    "files": {
        "metadata": ["PROJECT.md"],
    },
}


def _make_repo(root: Path, dirs: list[str], files: list[str] = ()) -> None:
    for d in dirs:
        (root / d).mkdir(parents=True, exist_ok=True)
    for f in files:
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_text("x", encoding="utf-8")


class ClassificationIndexTest(unittest.TestCase):
    def test_reverses_policy_into_name_maps(self):
        dir_class, file_class = sync_inventory._classification_index(POLICY)
        self.assertEqual(dir_class["execution"], "live")
        self.assertEqual(dir_class["docs"], "support")
        self.assertEqual(dir_class["Research"], "archive")
        self.assertEqual(file_class["PROJECT.md"], "metadata")

    def test_empty_policy(self):
        self.assertEqual(sync_inventory._classification_index({}), ({}, {}))


class BuildInventoryTest(unittest.TestCase):
    def _build(self, tmp: Path) -> dict:
        policy_path = tmp / "policy.json"
        policy_path.write_text(json.dumps(POLICY), encoding="utf-8")
        repo = tmp / "repo"
        _make_repo(repo, ["execution", "docs", "mystery_dir"], ["PROJECT.md", "stray.txt"])
        with patch.object(sync_inventory, "ROOT_HYGIENE_POLICY_PATH", policy_path):
            return sync_inventory.build_inventory(repo_root=repo)

    def test_classifies_known_and_unclassified_entries(self):
        with tempfile.TemporaryDirectory() as td:
            inv = self._build(Path(td))
        by_path = {e["path"]: e for e in inv["entries"]}
        self.assertEqual(by_path["execution"]["classification"], "live")
        self.assertEqual(by_path["execution"]["type"], "dir")
        self.assertEqual(by_path["PROJECT.md"]["classification"], "metadata")
        self.assertEqual(by_path["PROJECT.md"]["type"], "file")
        self.assertEqual(by_path["mystery_dir"]["classification"], "unclassified")
        self.assertEqual(by_path["stray.txt"]["classification"], "unclassified")

    def test_entries_sorted_and_count_matches(self):
        with tempfile.TemporaryDirectory() as td:
            inv = self._build(Path(td))
        names = [e["path"] for e in inv["entries"]]
        self.assertEqual(names, sorted(names))
        self.assertEqual(inv["entryCount"], len(inv["entries"]))

    def test_deterministic_across_runs(self):
        with tempfile.TemporaryDirectory() as td:
            first = self._build(Path(td))
            second = self._build(Path(td))
        self.assertEqual(first, second)


class BuildManifestTest(unittest.TestCase):
    def _build(self, tmp: Path, dirs: list[str]) -> dict:
        policy_path = tmp / "policy.json"
        policy_path.write_text(json.dumps(POLICY), encoding="utf-8")
        repo = tmp / "repo"
        _make_repo(repo, dirs)
        with patch.object(sync_capability_manifest, "ROOT_HYGIENE_POLICY_PATH", policy_path):
            return sync_capability_manifest.build_manifest(repo_root=repo)

    def test_excludes_non_discoverable_classifications(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = self._build(Path(td), ["execution", "docs", "Research", "state"])
        paths = {s["path"] for s in manifest["surfaces"]}
        self.assertIn("execution", paths)
        self.assertIn("docs", paths)
        self.assertNotIn("Research", paths)  # archive
        self.assertNotIn("state", paths)  # state

    def test_only_advertises_dirs_that_exist_on_disk(self):
        with tempfile.TemporaryDirectory() as td:
            # policy declares bin + config but we only create execution
            manifest = self._build(Path(td), ["execution"])
        paths = {s["path"] for s in manifest["surfaces"]}
        self.assertEqual(paths, {"execution"})

    def test_role_mapping_and_overrides(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = self._build(Path(td), ["execution", "docs", "bin", "config"])
        roles = {s["path"]: s["role"] for s in manifest["surfaces"]}
        self.assertEqual(roles["execution"], "runtime")  # live -> runtime
        self.assertEqual(roles["docs"], "support")  # support -> support
        self.assertEqual(roles["bin"], "operator")  # override beats live
        self.assertEqual(roles["config"], "registry")  # override beats support

    def test_sorted_and_counted(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = self._build(Path(td), ["execution", "docs", "bin", "config"])
        paths = [s["path"] for s in manifest["surfaces"]]
        self.assertEqual(paths, sorted(paths))
        self.assertEqual(manifest["surfaceCount"], len(paths))
        self.assertTrue(all(s["discoverable"] is True for s in manifest["surfaces"]))


class SummarizeTest(unittest.TestCase):
    def test_exit_code_zero_without_fail(self):
        results = [
            verify_generated_truth._make_result("OK", "a", "d"),
            verify_generated_truth._make_result("WARN", "b", "d"),
        ]
        counts, exit_code = verify_generated_truth.summarize(results)
        self.assertEqual((counts["OK"], counts["WARN"], counts["FAIL"]), (1, 1, 0))
        self.assertEqual(exit_code, 0)

    def test_exit_code_one_with_fail(self):
        results = [verify_generated_truth._make_result("FAIL", "a", "d")]
        _, exit_code = verify_generated_truth.summarize(results)
        self.assertEqual(exit_code, 1)


class RuleLookupTest(unittest.TestCase):
    def test_find_truth_rule_and_category(self):
        payload = {
            "rules": [{"id": verify_generated_truth.TRUTH_RULE_ID, "x": 1}],
            "categories": [{"id": verify_generated_truth.TRUTH_CATEGORY_ID, "y": 2}],
        }
        self.assertEqual(verify_generated_truth.find_truth_rule(payload=payload)["x"], 1)
        self.assertEqual(verify_generated_truth.find_truth_category(payload=payload)["y"], 2)

    def test_lookup_missing_returns_none(self):
        self.assertIsNone(verify_generated_truth.find_truth_rule(payload={}))
        self.assertIsNone(verify_generated_truth.find_truth_category(payload={"categories": []}))


class ArtifactChecksTest(unittest.TestCase):
    def test_missing_generated_artifacts_sorted(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_repo(root, [], ["artifacts/inventory.json"])
            rule = {"expectedArtifacts": ["zz/absent.json", "artifacts/inventory.json", "aa/absent.json"]}
            missing = verify_generated_truth.missing_generated_artifacts(rule=rule, repo_root=root)
        self.assertEqual(missing, ["aa/absent.json", "zz/absent.json"])

    def test_shadowing_only_flagged_when_generated_absent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_repo(root, [], ["INVENTORY.md"])
            present = verify_generated_truth.shadowing_inventories(
                generated_present=True, repo_root=root
            )
            absent = verify_generated_truth.shadowing_inventories(
                generated_present=False, repo_root=root
            )
        self.assertEqual(present, [])
        self.assertEqual(absent, ["INVENTORY.md"])

    def test_stale_generated_artifact_detected_via_mtime(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_repo(root, [], ["artifacts/inventory.json", "INVENTORY.md"])
            rule = {"expectedArtifacts": ["artifacts/inventory.json"]}
            # generated artifact older than the hand-maintained inventory -> stale
            os.utime(root / "artifacts/inventory.json", (1000, 1000))
            os.utime(root / "INVENTORY.md", (2000, 2000))
            stale = verify_generated_truth.stale_generated_artifacts(rule=rule, repo_root=root)
            self.assertEqual(stale, ["artifacts/inventory.json"])
            # generated artifact newer -> not stale
            os.utime(root / "artifacts/inventory.json", (3000, 3000))
            fresh = verify_generated_truth.stale_generated_artifacts(rule=rule, repo_root=root)
            self.assertEqual(fresh, [])

    def test_stale_check_empty_without_manual_inventories(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_repo(root, [], ["artifacts/inventory.json"])
            rule = {"expectedArtifacts": ["artifacts/inventory.json"]}
            self.assertEqual(
                verify_generated_truth.stale_generated_artifacts(rule=rule, repo_root=root), []
            )


class RunChecksIntegrationTest(unittest.TestCase):
    """End-to-end run_checks against a synthetic repo + policy pair."""

    def _write_policy(self, tmp: Path, verifier: str) -> Path:
        policy = {
            "rules": [
                {
                    "id": verify_generated_truth.TRUTH_RULE_ID,
                    "verifier": verifier,
                    "expectedArtifacts": ["artifacts/inventory.json"],
                }
            ]
        }
        path = tmp / "anti_drift_policy.json"
        path.write_text(json.dumps(policy), encoding="utf-8")
        return path

    def _write_scorecard(self, tmp: Path, verifiers: list[str]) -> Path:
        scorecard = {
            "categories": [
                {
                    "id": verify_generated_truth.TRUTH_CATEGORY_ID,
                    "requiredVerifiers": verifiers,
                }
            ]
        }
        path = tmp / "architecture_scorecard.json"
        path.write_text(json.dumps(scorecard), encoding="utf-8")
        return path

    def test_happy_path_all_ok(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            repo = tmp / "repo"
            _make_repo(repo, [], ["artifacts/inventory.json"])
            policy = self._write_policy(tmp, verify_generated_truth.SELF_VERIFIER)
            scorecard = self._write_scorecard(tmp, [verify_generated_truth.SELF_VERIFIER])
            with patch.object(verify_generated_truth, "ANTI_DRIFT_POLICY_PATH", policy), \
                 patch.object(verify_generated_truth, "ARCHITECTURE_SCORECARD_PATH", scorecard):
                results = verify_generated_truth.run_checks(repo_root=repo)
        statuses = [r["status"] for r in results]
        self.assertEqual(statuses, ["OK"] * 5)
        _, exit_code = verify_generated_truth.summarize(results)
        self.assertEqual(exit_code, 0)

    def test_missing_policy_is_single_fail(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            with patch.object(
                verify_generated_truth, "ANTI_DRIFT_POLICY_PATH", tmp / "nope.json"
            ):
                results = verify_generated_truth.run_checks(repo_root=tmp)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "FAIL")
        self.assertEqual(results[0]["detail"], "missing")

    def test_wrong_verifier_routing_fails(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            repo = tmp / "repo"
            _make_repo(repo, [], ["artifacts/inventory.json"])
            policy = self._write_policy(tmp, "execution/other_verifier.py")
            scorecard = self._write_scorecard(tmp, [verify_generated_truth.SELF_VERIFIER])
            with patch.object(verify_generated_truth, "ANTI_DRIFT_POLICY_PATH", policy), \
                 patch.object(verify_generated_truth, "ARCHITECTURE_SCORECARD_PATH", scorecard):
                results = verify_generated_truth.run_checks(repo_root=repo)
        self.assertEqual(results[0]["status"], "FAIL")
        self.assertIn("verifier", results[0]["detail"])

    def test_missing_artifact_fails_and_manual_shadow_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            repo = tmp / "repo"
            _make_repo(repo, [], ["INVENTORY.md"])  # manual inventory, no generated artifact
            policy = self._write_policy(tmp, verify_generated_truth.SELF_VERIFIER)
            scorecard = self._write_scorecard(tmp, [verify_generated_truth.SELF_VERIFIER])
            with patch.object(verify_generated_truth, "ANTI_DRIFT_POLICY_PATH", policy), \
                 patch.object(verify_generated_truth, "ARCHITECTURE_SCORECARD_PATH", scorecard):
                results = verify_generated_truth.run_checks(repo_root=repo)
        by_label = {r["label"]: r for r in results}
        self.assertEqual(by_label["generated-truth.artifacts"]["status"], "FAIL")
        self.assertEqual(by_label["generated-truth.shadow"]["status"], "FAIL")
        self.assertIn("INVENTORY.md", by_label["generated-truth.shadow"]["detail"])
        _, exit_code = verify_generated_truth.summarize(results)
        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
