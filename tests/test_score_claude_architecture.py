from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from execution.score_claude_architecture import (  # type: ignore[attr-defined]
    compute_score,
    render_markdown,
    results_from_report,
    run_checks,
    summarize,
    write_artifacts,
)

OK_VERIFIER = (
    "def run_checks():\n"
    '    return [{"status": "OK", "label": "fake.check", "detail": "all good"}]\n'
)
WARN_VERIFIER = (
    "def run_checks():\n"
    '    return [{"status": "WARN", "label": "fake.check", "detail": "advisory drift"}]\n'
)
FAIL_VERIFIER = (
    "def run_checks():\n"
    '    return [{"status": "FAIL", "label": "fake.check", "detail": "hard violation"}]\n'
)
BROKEN_VERIFIER = 'raise RuntimeError("boom on import")\n'


def make_scorecard(categories: list[dict]) -> dict:
    return {
        "scoring": {
            "targetScore": sum(int(c.get("weight", 0)) for c in categories),
            "mergeFloor": 0,
            "releaseFloor": 0,
            "enforcementMode": "advisory-until-claude-verifiers-exist",
        },
        "categories": categories,
    }


def make_category(cat_id: str, weight: int, verifiers: list[str]) -> dict:
    return {
        "id": cat_id,
        "label": cat_id.replace("_", " ").title(),
        "weight": weight,
        "requiredVerifiers": verifiers,
    }


class ScoreClaudeArchitectureTests(unittest.TestCase):
    def sandbox(self) -> Path:
        root = REPO_ROOT / ".tmp" / "test_score_claude_architecture" / self._testMethodName
        (root / "execution").mkdir(parents=True, exist_ok=True)
        return root

    def write_verifier(self, root: Path, rel_path: str, body: str) -> None:
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")

    def test_passing_verifier_earns_full_category_weight(self) -> None:
        root = self.sandbox()
        self.write_verifier(root, "execution/verify_fake_pass.py", OK_VERIFIER)
        scorecard = make_scorecard(
            [make_category("path_authority", 10, ["execution/verify_fake_pass.py"])]
        )

        report = compute_score(scorecard, repo_root=root)

        self.assertEqual(report["earned"], 10)
        self.assertEqual(report["possible_measured"], 10)
        self.assertEqual(report["unmeasured_weight"], 0)
        self.assertEqual(report["categories"][0]["status"], "pass")

    def test_fail_row_zeroes_the_measured_category(self) -> None:
        root = self.sandbox()
        self.write_verifier(root, "execution/verify_fake_fail.py", FAIL_VERIFIER)
        scorecard = make_scorecard(
            [make_category("hook_control_plane", 12, ["execution/verify_fake_fail.py"])]
        )

        report = compute_score(scorecard, repo_root=root)
        category = report["categories"][0]

        self.assertEqual(report["earned"], 0)
        self.assertEqual(report["possible_measured"], 12)
        self.assertEqual(category["status"], "blocking")
        self.assertIn("execution/verify_fake_fail.py", category["reason"])
        self.assertIn("hard violation", category["reason"])

    def test_warn_rows_keep_the_weight_and_are_listed_as_advisory(self) -> None:
        root = self.sandbox()
        self.write_verifier(root, "execution/verify_fake_warn.py", WARN_VERIFIER)
        scorecard = make_scorecard(
            [make_category("doctor_truthfulness", 10, ["execution/verify_fake_warn.py"])]
        )

        report = compute_score(scorecard, repo_root=root)
        category = report["categories"][0]

        self.assertEqual(report["earned"], 10)
        self.assertEqual(category["status"], "advisory_warn")
        self.assertIn("advisory drift", category["reason"])
        self.assertEqual(report["advisory_categories"], ["doctor_truthfulness"])

    def test_category_without_any_existing_verifier_is_unmeasured(self) -> None:
        root = self.sandbox()
        scorecard = make_scorecard(
            [make_category("anti_sprawl", 5, ["execution/verify_not_built_yet.py"])]
        )

        report = compute_score(scorecard, repo_root=root)
        category = report["categories"][0]

        self.assertEqual(report["earned"], 0)
        self.assertEqual(report["possible_measured"], 0)
        self.assertEqual(report["unmeasured_weight"], 5)
        self.assertEqual(category["status"], "unmeasured")
        self.assertIn("not yet measurable (backlog)", category["reason"])
        self.assertIn("execution/verify_not_built_yet.py", category["reason"])

    def test_partial_category_with_unbuilt_verifier_is_unmeasured(self) -> None:
        # Strict model: one built passing verifier does NOT measure a category
        # whose sibling required verifier is still unbuilt.
        root = self.sandbox()
        self.write_verifier(root, "execution/verify_fake_pass.py", OK_VERIFIER)
        scorecard = make_scorecard(
            [
                make_category(
                    "anti_sprawl",
                    5,
                    ["execution/verify_fake_pass.py", "execution/verify_not_built_yet.py"],
                )
            ]
        )

        report = compute_score(scorecard, repo_root=root)
        category = report["categories"][0]

        self.assertFalse(category["measured"])
        self.assertEqual(report["earned"], 0)
        self.assertEqual(report["possible_measured"], 0)
        self.assertEqual(report["unmeasured_weight"], 5)
        self.assertEqual(category["status"], "unmeasured")
        self.assertEqual(category["missing_verifiers"], ["execution/verify_not_built_yet.py"])

    def test_partial_category_with_failing_built_verifier_still_blocks(self) -> None:
        # Preserve FAIL-blocking: a failing built verifier must block even when a
        # sibling required verifier is unbuilt — never excused as unmeasured.
        root = self.sandbox()
        self.write_verifier(root, "execution/verify_fake_fail.py", FAIL_VERIFIER)
        scorecard = make_scorecard(
            [
                make_category(
                    "anti_sprawl",
                    5,
                    ["execution/verify_fake_fail.py", "execution/verify_not_built_yet.py"],
                )
            ]
        )

        report = compute_score(scorecard, repo_root=root)
        category = report["categories"][0]

        self.assertTrue(category["measured"])
        self.assertEqual(report["earned"], 0)
        self.assertEqual(report["possible_measured"], 5)
        self.assertEqual(report["unmeasured_weight"], 0)
        self.assertEqual(category["status"], "blocking")
        self.assertIn("hard violation", category["reason"])

        fail_rows = [r for r in results_from_report(report) if r["status"] == "FAIL"]
        self.assertEqual(len(fail_rows), 1)
        self.assertEqual(fail_rows[0]["label"], "claude_architecture.anti_sprawl")

    def test_broken_verifier_counts_as_blocking_evidence(self) -> None:
        root = self.sandbox()
        self.write_verifier(root, "execution/verify_fake_broken.py", BROKEN_VERIFIER)
        scorecard = make_scorecard(
            [make_category("runtime_portability", 12, ["execution/verify_fake_broken.py"])]
        )

        report = compute_score(scorecard, repo_root=root)
        category = report["categories"][0]

        self.assertEqual(category["status"], "blocking")
        self.assertIn("verifier raised", category["reason"])

    def test_run_checks_emits_fail_row_only_when_a_measured_category_is_zeroed(self) -> None:
        root = self.sandbox()
        self.write_verifier(root, "execution/verify_fake_pass.py", OK_VERIFIER)
        self.write_verifier(root, "execution/verify_fake_fail.py", FAIL_VERIFIER)
        failing = make_scorecard(
            [
                make_category("path_authority", 10, ["execution/verify_fake_pass.py"]),
                make_category("hook_control_plane", 12, ["execution/verify_fake_fail.py"]),
            ]
        )
        passing = make_scorecard(
            [make_category("path_authority", 10, ["execution/verify_fake_pass.py"])]
        )

        failing_rows = run_checks(repo_root=root, scorecard_payload=failing)
        passing_rows = run_checks(repo_root=root, scorecard_payload=passing)

        failing_fails = [r for r in failing_rows if r["status"] == "FAIL"]
        self.assertEqual(len(failing_fails), 1)
        self.assertEqual(failing_fails[0]["label"], "claude_architecture.hook_control_plane")
        self.assertEqual([r for r in passing_rows if r["status"] == "FAIL"], [])

    def test_run_checks_emits_warn_row_only_when_unmeasured_weight_is_positive(self) -> None:
        root = self.sandbox()
        self.write_verifier(root, "execution/verify_fake_pass.py", OK_VERIFIER)
        unmeasured = make_scorecard(
            [make_category("anti_sprawl", 5, ["execution/verify_not_built_yet.py"])]
        )
        measured = make_scorecard(
            [make_category("path_authority", 10, ["execution/verify_fake_pass.py"])]
        )

        unmeasured_rows = run_checks(repo_root=root, scorecard_payload=unmeasured)
        measured_rows = run_checks(repo_root=root, scorecard_payload=measured)

        unmeasured_warns = [r for r in unmeasured_rows if r["status"] == "WARN"]
        self.assertEqual(len(unmeasured_warns), 1)
        self.assertIn("execution/verify_not_built_yet.py", unmeasured_warns[0]["detail"])
        self.assertEqual([r for r in measured_rows if r["status"] == "WARN"], [])

    def test_write_artifacts_emits_json_and_markdown_into_the_given_dir(self) -> None:
        root = self.sandbox()
        self.write_verifier(root, "execution/verify_fake_fail.py", FAIL_VERIFIER)
        scorecard = make_scorecard(
            [
                make_category("hook_control_plane", 12, ["execution/verify_fake_fail.py"]),
                make_category("anti_sprawl", 5, ["execution/verify_not_built_yet.py"]),
            ]
        )
        report = compute_score(scorecard, repo_root=root)
        artifacts_dir = root / "artifacts"

        json_path, md_path = write_artifacts(report, artifacts_dir=artifacts_dir)

        self.assertEqual(json_path, artifacts_dir / "claude_architecture_score.json")
        self.assertEqual(md_path, artifacts_dir / "claude_architecture_score.md")
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["earned"], 0)
        self.assertEqual(payload["unmeasured_weight"], 5)
        markdown = md_path.read_text(encoding="utf-8")
        self.assertIn("## Lost points", markdown)
        self.assertIn("## Not yet measurable (backlog)", markdown)
        self.assertIn("hard violation", markdown)

    def test_markdown_explains_every_lost_and_unmeasured_point(self) -> None:
        root = self.sandbox()
        self.write_verifier(root, "execution/verify_fake_fail.py", FAIL_VERIFIER)
        scorecard = make_scorecard(
            [
                make_category("hook_control_plane", 12, ["execution/verify_fake_fail.py"]),
                make_category("anti_sprawl", 5, ["execution/verify_not_built_yet.py"]),
            ]
        )

        markdown = render_markdown(compute_score(scorecard, repo_root=root))

        self.assertIn("execution/verify_fake_fail.py", markdown)
        self.assertIn("execution/verify_not_built_yet.py", markdown)

    def test_results_from_report_always_includes_an_ok_score_row(self) -> None:
        root = self.sandbox()
        report = compute_score(make_scorecard([]), repo_root=root)

        rows = results_from_report(report)

        score_rows = [r for r in rows if r["label"] == "claude_architecture.score"]
        self.assertEqual(len(score_rows), 1)
        self.assertEqual(score_rows[0]["status"], "OK")

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
