from __future__ import annotations

import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from execution.verify_claude_root_hygiene import (  # type: ignore[attr-defined]
    find_unclassified_entries,
    run_checks,
    summarize,
)


def make_policy(**overrides) -> dict:
    """Factory for a minimal, internally consistent claude root-hygiene policy."""
    payload = {
        "version": 1,
        "directories": {"runtime": ["commands"], "state": ["cache"]},
        "files": {"generated_truth": ["settings.json"]},
        "requiredDirectories": ["commands"],
        "requiredFiles": ["settings.json"],
        "boundaryRules": [],
    }
    payload.update(overrides)
    return payload


class VerifyClaudeRootHygieneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = (
            REPO_ROOT / ".tmp" / "test_verify_claude_root_hygiene" / self._testMethodName
        )
        if self.sandbox.exists():
            shutil.rmtree(self.sandbox)
        self.root = self.sandbox / "claude-home"
        self.root.mkdir(parents=True)

    def _write_policy(self, payload: dict) -> Path:
        policy_path = self.sandbox / "policy.json"
        policy_path.write_text(json.dumps(payload), encoding="utf-8")
        return policy_path

    def _build_classified_root(self) -> None:
        (self.root / "commands").mkdir()
        (self.root / "cache").mkdir()
        (self.root / "settings.json").write_text("{}", encoding="utf-8")

    def test_run_checks_reports_all_ok_for_fully_classified_root(self) -> None:
        self._build_classified_root()
        policy_path = self._write_policy(make_policy())

        results = run_checks(policy_path=policy_path, root=self.root)

        self.assertEqual({row["status"] for row in results}, {"OK"})
        self.assertEqual(
            [row["name"] for row in results],
            [
                "root_hygiene.claude.policy",
                "root_hygiene.claude.vocabulary",
                "root_hygiene.claude.required",
                "root_hygiene.claude.unclassified",
            ],
        )

    def test_invalid_classification_bucket_fails_with_bucket_in_row_name(self) -> None:
        self._build_classified_root()
        # 'live' is the repo-root vocabulary, deliberately NOT in the claude
        # vocabulary — a genuine drift a claude policy edit could introduce.
        policy_path = self._write_policy(
            make_policy(files={"live": ["settings.json"]})
        )

        results = run_checks(policy_path=policy_path, root=self.root)

        failures = [row for row in results if row["status"] == "FAIL"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["name"], "root_hygiene.claude.vocabulary.live")
        self.assertIn("files classification 'live'", failures[0]["detail"])

    @mock.patch.dict(
        os.environ, {"ORDER_SAMURAI_AUDIT_PROFILE": "full"}
    )  # asserts the opinionated tier; baseline is the shipped default
    def test_missing_required_directory_fails_under_its_declared_bucket(self) -> None:
        (self.root / "cache").mkdir()
        (self.root / "settings.json").write_text("{}", encoding="utf-8")
        policy_path = self._write_policy(make_policy())

        results = run_checks(policy_path=policy_path, root=self.root)

        failures = [row for row in results if row["status"] == "FAIL"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["name"], "root_hygiene.claude.required.runtime")
        self.assertIn("required directory missing", failures[0]["detail"])
        self.assertIn("commands", failures[0]["detail"])

    @mock.patch.dict(
        os.environ, {"ORDER_SAMURAI_AUDIT_PROFILE": "full"}
    )  # asserts the opinionated tier; baseline is the shipped default
    def test_missing_required_file_fails_under_its_declared_bucket(self) -> None:
        (self.root / "commands").mkdir()
        (self.root / "cache").mkdir()
        policy_path = self._write_policy(make_policy())

        results = run_checks(policy_path=policy_path, root=self.root)

        failures = [row for row in results if row["status"] == "FAIL"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["name"], "root_hygiene.claude.required.generated_truth")
        self.assertIn("settings.json", failures[0]["detail"])

    @mock.patch.dict(
        os.environ, {"ORDER_SAMURAI_AUDIT_PROFILE": "full"}
    )  # asserts the opinionated tier; baseline is the shipped default
    def test_required_directory_present_as_file_fails_as_wrong_type(self) -> None:
        (self.root / "commands").write_text("not a directory", encoding="utf-8")
        (self.root / "cache").mkdir()
        (self.root / "settings.json").write_text("{}", encoding="utf-8")
        policy_path = self._write_policy(make_policy())

        results = run_checks(policy_path=policy_path, root=self.root)

        failures = [row for row in results if row["status"] == "FAIL"]
        self.assertEqual(len(failures), 1)
        self.assertIn("not a directory", failures[0]["detail"])

    def test_unclassified_entries_warn_one_row_each(self) -> None:
        self._build_classified_root()
        (self.root / "mystery").mkdir()
        (self.root / "stray.md").write_text("stray", encoding="utf-8")
        policy_path = self._write_policy(make_policy())

        results = run_checks(policy_path=policy_path, root=self.root)

        warnings = [row for row in results if row["status"] == "WARN"]
        self.assertEqual(len(warnings), 2)
        self.assertEqual(
            {row["name"] for row in warnings},
            {"root_hygiene.claude.unclassified"},
        )
        details = " | ".join(row["detail"] for row in warnings)
        self.assertIn("mystery", details)
        self.assertIn("stray.md", details)

    def test_undeclared_dotfiles_ignored_when_policy_declares_no_dotfiles(self) -> None:
        self._build_classified_root()
        (self.root / ".DS_Store").write_text("", encoding="utf-8")
        policy_path = self._write_policy(make_policy())

        results = run_checks(policy_path=policy_path, root=self.root)

        self.assertEqual({row["status"] for row in results}, {"OK"})

    def test_undeclared_dotfiles_warn_when_policy_declares_any_dotfile(self) -> None:
        self._build_classified_root()
        (self.root / ".DS_Store").write_text("", encoding="utf-8")
        policy_path = self._write_policy(
            make_policy(directories={"runtime": ["commands"], "state": ["cache", ".tmp"]})
        )

        results = run_checks(policy_path=policy_path, root=self.root)

        warnings = [row for row in results if row["status"] == "WARN"]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["name"], "root_hygiene.claude.unclassified")
        self.assertIn(".DS_Store", warnings[0]["detail"])

    def test_env_override_routes_run_checks_to_sandbox_root(self) -> None:
        self._build_classified_root()
        policy_path = self._write_policy(make_policy())

        with mock.patch.dict(os.environ, {"CLAUDE_RUNTIME_ROOT": str(self.root)}):
            results = run_checks(policy_path=policy_path)

        self.assertEqual({row["status"] for row in results}, {"OK"})
        required_rows = [
            row for row in results if row["name"] == "root_hygiene.claude.required"
        ]
        self.assertEqual(len(required_rows), 1)
        self.assertIn(str(self.root), required_rows[0]["detail"])

    def test_missing_policy_file_fails_without_further_checks(self) -> None:
        results = run_checks(policy_path=self.sandbox / "nope.json", root=self.root)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "FAIL")
        self.assertEqual(results[0]["name"], "root_hygiene.claude.policy")
        self.assertIn("missing", results[0]["detail"])

    def test_missing_runtime_root_warns_instead_of_crashing(self) -> None:
        policy_path = self._write_policy(make_policy())

        results = run_checks(policy_path=policy_path, root=self.sandbox / "absent-home")

        self.assertEqual([row["status"] for row in results], ["OK", "OK", "WARN"])
        self.assertEqual(results[-1]["name"], "root_hygiene.claude.runtime_root")
        self.assertIn("layout checks skipped", results[-1]["detail"])

    def test_find_unclassified_entries_returns_sorted_names(self) -> None:
        (self.root / "Zulu").mkdir()
        (self.root / "alpha").mkdir()
        (self.root / "config").mkdir()

        unclassified = find_unclassified_entries(
            root=self.root,
            declared_entries={"config"},
        )

        self.assertEqual(unclassified, ["alpha", "Zulu"])

    def test_summarize_sets_nonzero_exit_for_failures(self) -> None:
        counts, exit_code = summarize(
            [
                {"status": "OK", "name": "a", "detail": "x"},
                {"status": "FAIL", "name": "b", "detail": "y"},
            ]
        )

        self.assertEqual(counts["OK"], 1)
        self.assertEqual(counts["FAIL"], 1)
        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
