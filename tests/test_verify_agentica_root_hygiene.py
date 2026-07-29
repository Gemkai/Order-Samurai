from __future__ import annotations

import json
import shutil
import sys
import unittest

import pytest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from execution.verify_agentica_root_hygiene import (  # type: ignore[attr-defined]
    AGENTICA_REPO_ROOT,
    find_unclassified_entries,
    run_checks,
    summarize,
)


def make_policy(**overrides) -> dict:
    payload = {
        "version": 1,
        "directories": {"live": ["Governance"], "state": [".planning"]},
        "files": {"metadata": ["README.md"], "state": ["HANDOFF-*.md"]},
        "requiredDirectories": ["Governance"],
        "requiredFiles": ["README.md"],
        "boundaryRules": [],
    }
    payload.update(overrides)
    return payload


class VerifyAgenticaRootHygieneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = (
            REPO_ROOT / ".tmp" / "test_verify_agentica_root_hygiene" / self._testMethodName
        )
        if self.sandbox.exists():
            shutil.rmtree(self.sandbox)
        self.root = self.sandbox / "repo-root"
        self.root.mkdir(parents=True)

    def _write_policy(self, payload: dict) -> Path:
        policy_path = self.sandbox / "policy.json"
        policy_path.write_text(json.dumps(payload), encoding="utf-8")
        return policy_path

    def _build_classified_root(self) -> None:
        (self.root / "Governance").mkdir()
        (self.root / ".planning").mkdir()
        (self.root / "README.md").write_text("# x", encoding="utf-8")

    def test_fully_classified_root_is_all_ok(self) -> None:
        self._build_classified_root()
        results = run_checks(policy_path=self._write_policy(make_policy()), root=self.root)
        counts, exit_code = summarize(results)
        self.assertEqual(counts["FAIL"], 0)
        self.assertEqual(counts["WARN"], 0)
        self.assertEqual(exit_code, 0)

    def test_glob_pattern_classifies_active_handoff(self) -> None:
        self._build_classified_root()
        (self.root / "HANDOFF-some-workstream-2026-07-16.md").write_text("h", encoding="utf-8")
        results = run_checks(policy_path=self._write_policy(make_policy()), root=self.root)
        counts, _ = summarize(results)
        self.assertEqual(counts["WARN"], 0, results)

    def test_unclassified_entry_warns_but_does_not_fail(self) -> None:
        self._build_classified_root()
        (self.root / "stray-notes.md").write_text("junk", encoding="utf-8")
        results = run_checks(policy_path=self._write_policy(make_policy()), root=self.root)
        counts, exit_code = summarize(results)
        self.assertEqual(counts["WARN"], 1)
        self.assertEqual(exit_code, 0)
        warn = next(r for r in results if r["status"] == "WARN")
        self.assertIn("stray-notes.md", warn["detail"])

    def test_missing_required_entry_fails(self) -> None:
        (self.root / ".planning").mkdir()
        (self.root / "README.md").write_text("# x", encoding="utf-8")
        results = run_checks(policy_path=self._write_policy(make_policy()), root=self.root)
        counts, exit_code = summarize(results)
        self.assertGreaterEqual(counts["FAIL"], 1)
        self.assertEqual(exit_code, 1)

    def test_invalid_vocabulary_bucket_fails(self) -> None:
        self._build_classified_root()
        policy = make_policy(directories={"live": ["Governance"], "runtime": [".planning"]})
        results = run_checks(policy_path=self._write_policy(policy), root=self.root)
        counts, exit_code = summarize(results)
        self.assertGreaterEqual(counts["FAIL"], 1)
        self.assertEqual(exit_code, 1)

    def test_find_unclassified_entries_matches_globs(self) -> None:
        self._build_classified_root()
        (self.root / "HANDOFF-x.md").write_text("h", encoding="utf-8")
        (self.root / "junk.tmp").write_text("j", encoding="utf-8")
        unclassified = find_unclassified_entries(
            root=self.root,
            declared_entries={"Governance", ".planning", "README.md", "HANDOFF-*.md"},
        )
        self.assertEqual(unclassified, ["junk.tmp"])

    @pytest.mark.live_machine  # requires generated STATE.md at repo root (gitignored; absent on a bare CI checkout)
    def test_live_repo_root_is_structurally_valid_against_live_policy(self) -> None:
        """Integration: the shipped policy must hold *structurally* against the
        real repo root — no FAIL (required entries present, vocabulary valid),
        and any WARNs are only the soft ``unclassified`` drift-pressure kind.

        Deliberately does NOT assert WARN == 0. Per the verifier's contract an
        unclassified top-level entry is soft drift pressure surfaced daily by
        doctor.py for a human to classify — not a defect. Gating the pytest
        suite on zero live WARNs coupled the suite's green state to transient
        repo-root contents, so every new root file (PROJECT_FACTS.md, an
        untracked scratch dir, ...) turned the whole suite red for a non-defect.
        The suite asserts the invariant that must always hold; doctor owns the
        drift nudge.

        Skipped where there is no live repo to validate: the public export ships
        this pack standalone, and the root resolver returns None there rather
        than guessing at a fixed parent-hop. Asserting against "the live repo
        root" in a tree that has none is not a weaker check, it is a different
        question with no subject.
        """
        if AGENTICA_REPO_ROOT is None:
            self.skipTest("standalone distribution — no Agentica repo root to validate")
        results = run_checks()
        counts, exit_code = summarize(results)
        self.assertEqual(exit_code, 0, results)
        self.assertEqual(counts["FAIL"], 0, results)
        # Whatever WARNs exist must be the soft unclassified kind, nothing worse.
        unexpected = [
            r
            for r in results
            if r["status"] == "WARN"
            and r["name"] != "root_hygiene.agentica.unclassified"
        ]
        self.assertEqual(unexpected, [], unexpected)
        self.assertTrue(AGENTICA_REPO_ROOT.joinpath("Governance").is_dir())


if __name__ == "__main__":
    unittest.main()
