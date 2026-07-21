from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from execution.verify_root_hygiene import (  # type: ignore[attr-defined]
    find_unclassified_root_entries,
    summarize,
    validate_root_hygiene_policy,
)


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

        failures = validate_root_hygiene_policy(payload=payload, repo_root=sandbox)

        self.assertEqual(failures, ["root_hygiene_policy: execution"])

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

        failures = validate_root_hygiene_policy(payload=payload, repo_root=sandbox)

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


if __name__ == "__main__":
    unittest.main()
