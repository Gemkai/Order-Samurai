from __future__ import annotations

import os
import shutil
import sys
import unittest
from unittest import mock
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from execution.claude_runtime_target import (  # type: ignore[attr-defined]
    SCORECARD_PATH,
    runtime_root,
)
from execution.verify_claude_doc_parity import (  # type: ignore[attr-defined]
    find_doc_parity_category,
    required_runtime_docs,
    run_checks,
    summarize,
)


def _load_required_runtime_docs() -> list[str]:
    """The runtime docs the live scorecard actually declares — the module reads
    the real config/claude_architecture_scorecard.json, so fixtures must build
    exactly this set under the sandbox root."""
    import json

    payload = json.loads(SCORECARD_PATH.read_text(encoding="utf-8"))
    category = find_doc_parity_category(payload)
    assert category is not None, "scorecard is missing the documentation_parity category"
    return required_runtime_docs(category)


class VerifyClaudeDocParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = (
            REPO_ROOT / ".tmp" / "test_verify_claude_doc_parity" / self._testMethodName
        )
        if self.sandbox.exists():
            shutil.rmtree(self.sandbox)
        self.root = self.sandbox / "claude-home"
        self.root.mkdir(parents=True)
        self._saved_env = os.environ.get("CLAUDE_RUNTIME_ROOT")
        self.required_docs = _load_required_runtime_docs()

    def tearDown(self) -> None:
        if self._saved_env is None:
            os.environ.pop("CLAUDE_RUNTIME_ROOT", None)
        else:
            os.environ["CLAUDE_RUNTIME_ROOT"] = self._saved_env

    def _build_all_runtime_docs(self) -> None:
        for doc in self.required_docs:
            target = self.root / doc
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("doc", encoding="utf-8")

    def _runtime_rows(self, results: list[dict]) -> list[dict]:
        return [row for row in results if row["label"].startswith("doc_parity.runtime.")]

    def _repo_rows(self, results: list[dict]) -> list[dict]:
        return [row for row in results if row["label"].startswith("doc_parity.repo.")]

    def test_all_runtime_docs_present_are_ok(self) -> None:
        self._build_all_runtime_docs()

        results = run_checks(runtime_root_dir=self.root)

        runtime_rows = self._runtime_rows(results)
        self.assertEqual(len(runtime_rows), len(self.required_docs))
        self.assertEqual({row["status"] for row in runtime_rows}, {"OK"})

    @mock.patch.dict(
        os.environ, {"ORDER_SAMURAI_AUDIT_PROFILE": "full"}
    )  # asserts the opinionated tier; baseline is the shipped default
    def test_missing_runtime_doc_fails_only_that_row(self) -> None:
        self._build_all_runtime_docs()
        # Drop exactly one required doc so a single runtime row goes FAIL.
        dropped = self.required_docs[-1]
        (self.root / dropped).unlink()

        results = run_checks(runtime_root_dir=self.root)

        failures = [row for row in results if row["status"] == "FAIL"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["label"], f"doc_parity.runtime.{dropped}")
        self.assertIn("missing", failures[0]["detail"])

    def test_missing_runtime_root_warns_runtime_docs_without_crashing(self) -> None:
        absent_root = self.sandbox / "absent-home"

        results = run_checks(runtime_root_dir=absent_root)

        runtime_rows = self._runtime_rows(results)
        self.assertEqual(len(runtime_rows), len(self.required_docs))
        self.assertEqual({row["status"] for row in runtime_rows}, {"WARN"})
        # No runtime doc is a FAIL when the root itself is absent.
        self.assertFalse(
            [row for row in runtime_rows if row["status"] == "FAIL"],
        )

    def test_in_repo_enforcement_pack_docs_are_ok(self) -> None:
        # REPORT_PATH and BACKLOG_PATH are real files in this repo, so the
        # in-repo rows are OK regardless of the (sandbox) runtime root.
        self._build_all_runtime_docs()

        results = run_checks(runtime_root_dir=self.root)

        repo_rows = self._repo_rows(results)
        self.assertEqual(len(repo_rows), 2)
        self.assertEqual({row["status"] for row in repo_rows}, {"OK"})

    def test_co_movement_honor_system_row_is_ok_and_not_a_git_check(self) -> None:
        self._build_all_runtime_docs()

        results = run_checks(runtime_root_dir=self.root)

        comovement = [row for row in results if row["label"] == "doc_parity.co-movement"]
        self.assertEqual(len(comovement), 1)
        self.assertEqual(comovement[0]["status"], "OK")
        self.assertIn("git-history", comovement[0]["detail"])

    def test_env_override_routes_run_checks_to_sandbox_root(self) -> None:
        self._build_all_runtime_docs()
        os.environ["CLAUDE_RUNTIME_ROOT"] = str(self.root)

        # No runtime_root_dir arg: run_checks must resolve runtime_root() itself,
        # which honors CLAUDE_RUNTIME_ROOT.
        results = run_checks()

        runtime_rows = self._runtime_rows(results)
        self.assertEqual({row["status"] for row in runtime_rows}, {"OK"})
        self.assertTrue(runtime_rows)

    def test_all_result_rows_use_label_key(self) -> None:
        self._build_all_runtime_docs()

        results = run_checks(runtime_root_dir=self.root)

        for row in results:
            self.assertIn("label", row)
            self.assertNotIn("name", row)
            self.assertIn(row["status"], {"OK", "WARN", "FAIL"})

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

    def test_runtime_root_default_is_live_claude_home_when_env_unset(self) -> None:
        # Sanity: with no override the module's resolver points at ~/.claude,
        # never at the sandbox — proving fixtures never touch the live home.
        os.environ.pop("CLAUDE_RUNTIME_ROOT", None)
        self.assertEqual(runtime_root(), Path.home() / ".claude")


if __name__ == "__main__":
    unittest.main()
