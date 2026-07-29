from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from execution.verify_claude_promotion_policy import (  # type: ignore[attr-defined]
    MECHANICAL_GATE_IDS,
    honor_system_gate_ids,
    run_checks,
    summarize,
    validate_lifecycle_states,
    validate_matrix_promotion_requirements,
    validate_promotion_checklist,
    validate_retirement_policy,
    validate_role_lifecycle_mapping,
)


def make_checklist_item(item_id: str, *, required: bool = True, statement: str | None = None) -> dict:
    return {
        "id": item_id,
        "required": required,
        "statement": statement if statement is not None else f"Statement for {item_id}.",
    }


def make_policy_payload() -> dict:
    return {
        "version": 1,
        "lifecycleStates": [
            "source",
            "candidate",
            "runtime",
            "compatibility",
            "deprecated",
            "archive",
        ],
        "promotionChecklist": [
            make_checklist_item("explicit-purpose-owner"),
            make_checklist_item("generated-config-integration"),
            make_checklist_item("doctor-and-invariant-coverage"),
            make_checklist_item("inventory-parity"),
            make_checklist_item("doc-parity"),
        ],
        "blockers": ["handwritten live config drift"],
        "retirementPolicy": {
            "requirements": ["mark deprecated state explicitly"],
        },
    }


def make_surface(path: str, *, role: str = "runtime", owner: str = "Claude control plane",
                 contract: str = "canonical entrypoint") -> dict:
    return {
        "path": path,
        "role": role,
        "owner": owner,
        "discoverabilityContract": contract,
    }


def make_matrix_payload() -> dict:
    return {
        "version": 1,
        "surfaceRoles": ["runtime", "source", "compatibility", "archive"],
        "surfaces": [
            make_surface("scripts/runtime_paths.py"),
            make_surface("skill-source", role="source", contract="cold-storage plane"),
            make_surface("backups", role="archive", contract="historical backup plane"),
        ],
    }


class VerifyClaudePromotionPolicyTests(unittest.TestCase):
    def _sandbox(self) -> Path:
        sandbox = REPO_ROOT / ".tmp" / "test_verify_claude_promotion_policy" / self._testMethodName
        sandbox.mkdir(parents=True, exist_ok=True)
        return sandbox

    def _write_sandbox_configs(self, policy: dict, matrix: dict) -> tuple[Path, Path]:
        sandbox = self._sandbox()
        policy_path = sandbox / "claude_promotion_policy.json"
        matrix_path = sandbox / "claude_surface_matrix.json"
        policy_path.write_text(json.dumps(policy), encoding="utf-8")
        matrix_path.write_text(json.dumps(matrix), encoding="utf-8")
        return policy_path, matrix_path

    def test_run_checks_reports_no_failures_for_valid_sandbox_configs(self) -> None:
        policy_path, matrix_path = self._write_sandbox_configs(
            make_policy_payload(), make_matrix_payload()
        )

        results = run_checks(policy_path=policy_path, matrix_path=matrix_path)

        failures = [row for row in results if row["status"] == "FAIL"]
        self.assertEqual(failures, [])

    def test_run_checks_emits_single_honor_system_row_counting_judgment_only_gates(self) -> None:
        policy_path, matrix_path = self._write_sandbox_configs(
            make_policy_payload(), make_matrix_payload()
        )

        results = run_checks(policy_path=policy_path, matrix_path=matrix_path)

        honor_rows = [
            row for row in results if row["label"] == "claude_promotion_policy.honor_system"
        ]
        self.assertEqual(len(honor_rows), 1)
        self.assertEqual(honor_rows[0]["status"], "OK")
        # 5 checklist gates, 2 mechanically enforced -> 3 honor-system.
        self.assertIn("3 of 5", honor_rows[0]["detail"])

    def test_run_checks_returns_single_failure_when_policy_file_is_missing(self) -> None:
        sandbox = self._sandbox()
        policy_path = sandbox / "does_not_exist.json"
        matrix_path = sandbox / "claude_surface_matrix.json"
        matrix_path.write_text(json.dumps(make_matrix_payload()), encoding="utf-8")

        results = run_checks(policy_path=policy_path, matrix_path=matrix_path)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "FAIL")
        self.assertEqual(results[0]["detail"], "missing")

    def test_run_checks_fails_matrix_row_but_keeps_policy_rows_when_matrix_missing(self) -> None:
        sandbox = self._sandbox()
        policy_path = sandbox / "claude_promotion_policy.json"
        policy_path.write_text(json.dumps(make_policy_payload()), encoding="utf-8")

        results = run_checks(
            policy_path=policy_path, matrix_path=sandbox / "does_not_exist.json"
        )

        matrix_rows = [row for row in results if row["label"] == "claude_surface_matrix.json"]
        self.assertEqual(len(matrix_rows), 1)
        self.assertEqual(matrix_rows[0]["status"], "FAIL")
        policy_row_labels = {row["label"] for row in results}
        self.assertIn("claude_promotion_policy.lifecycleStates", policy_row_labels)
        self.assertIn("claude_promotion_policy.promotionChecklist", policy_row_labels)

    def test_validate_lifecycle_states_reports_missing_retirement_states(self) -> None:
        payload = make_policy_payload()
        payload["lifecycleStates"] = ["source", "runtime"]

        failures = validate_lifecycle_states(payload=payload)

        self.assertEqual(
            failures,
            ["missing lifecycle state 'archive'", "missing lifecycle state 'deprecated'"],
        )

    def test_validate_promotion_checklist_reports_item_without_statement(self) -> None:
        payload = make_policy_payload()
        payload["promotionChecklist"].append(make_checklist_item("new-gate", statement=""))

        failures = validate_promotion_checklist(payload=payload)

        self.assertEqual(failures, ["checklist item 'new-gate' has no statement"])

    def test_validate_promotion_checklist_reports_backlog_gate_not_marked_required(self) -> None:
        payload = make_policy_payload()
        payload["promotionChecklist"][0] = make_checklist_item(
            "explicit-purpose-owner", required=False
        )

        failures = validate_promotion_checklist(payload=payload)

        self.assertEqual(
            failures,
            ["promotion gate 'explicit-purpose-owner' is not marked required"],
        )

    def test_validate_role_lifecycle_mapping_reports_unmappable_role(self) -> None:
        matrix = make_matrix_payload()
        matrix["surfaces"].append(make_surface("mystery-dir", role="mystery"))

        failures = validate_role_lifecycle_mapping(
            matrix_payload=matrix, policy_payload=make_policy_payload()
        )

        self.assertEqual(failures, ["mystery-dir: role 'mystery' has no lifecycle mapping"])

    def test_validate_role_lifecycle_mapping_reports_undeclared_lifecycle_state(self) -> None:
        policy = make_policy_payload()
        policy["lifecycleStates"] = ["runtime", "deprecated", "archive"]
        matrix = make_matrix_payload()
        matrix["surfaces"] = [make_surface("skill-source", role="source")]

        failures = validate_role_lifecycle_mapping(
            matrix_payload=matrix, policy_payload=policy
        )

        self.assertEqual(
            failures,
            [
                "skill-source: role 'source' maps to lifecycle state 'source' "
                "which the promotion policy does not declare"
            ],
        )

    def test_validate_matrix_promotion_requirements_reports_empty_owner(self) -> None:
        matrix = make_matrix_payload()
        matrix["surfaces"] = [make_surface("scripts/doctor.py", owner="  ")]

        failures = validate_matrix_promotion_requirements(matrix_payload=matrix)

        self.assertEqual(
            failures,
            ["scripts/doctor.py: owner is empty (explicit-purpose-owner gate)"],
        )

    def test_validate_matrix_promotion_requirements_reports_runtime_surface_without_contract(
        self,
    ) -> None:
        matrix = make_matrix_payload()
        matrix["surfaces"] = [make_surface("settings.json", role="generated_truth", contract="")]

        failures = validate_matrix_promotion_requirements(matrix_payload=matrix)

        self.assertEqual(
            failures,
            [
                "settings.json: runtime-plane surface has no discoverabilityContract "
                "(inventory-parity gate)"
            ],
        )

    def test_validate_matrix_promotion_requirements_allows_archive_surface_without_contract(
        self,
    ) -> None:
        matrix = make_matrix_payload()
        matrix["surfaces"] = [make_surface("backups", role="archive", contract="")]

        failures = validate_matrix_promotion_requirements(matrix_payload=matrix)

        self.assertEqual(failures, [])

    def test_validate_retirement_policy_reports_missing_requirements(self) -> None:
        payload = make_policy_payload()
        payload["retirementPolicy"] = {"requirements": []}

        failures = validate_retirement_policy(payload=payload)

        self.assertEqual(failures, ["retirementPolicy.requirements absent or empty"])

    def test_honor_system_gate_ids_excludes_mechanical_gates(self) -> None:
        ids = honor_system_gate_ids(payload=make_policy_payload())

        self.assertEqual(
            ids,
            ["generated-config-integration", "doctor-and-invariant-coverage", "doc-parity"],
        )
        self.assertTrue(set(MECHANICAL_GATE_IDS).isdisjoint(ids))

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
