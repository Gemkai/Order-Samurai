from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from execution.verify_runtime_contract import (  # type: ignore[attr-defined]
    summarize,
    validate_doctor_entrypoint,
    validate_required_paths,
    validate_runtime_paths_stay_under_root,
    validate_runtime_policy_contract,
    validate_runtime_scorecard_contract,
)


class VerifyRuntimeContractTests(unittest.TestCase):
    def test_validate_required_paths_reports_missing_file(self) -> None:
        sandbox = REPO_ROOT / ".tmp" / "test_verify_runtime_contract" / self._testMethodName
        (sandbox / "execution").mkdir(parents=True, exist_ok=True)

        failures = validate_required_paths(
            required_paths={
                "execution": sandbox / "execution",
                "execution/doctor.py": sandbox / "execution" / "doctor.py",
            }
        )

        self.assertEqual(failures, ["execution/doctor.py"])

    def test_validate_runtime_paths_stay_under_root_reports_external_path(self) -> None:
        sandbox = REPO_ROOT / ".tmp" / "test_verify_runtime_contract" / self._testMethodName
        module = SimpleNamespace(
            REPO_ROOT=sandbox,
            EXECUTION_DIR=sandbox / "execution",
            EXTERNAL_PATH=Path("C:/outside/order-samurai"),
        )

        failures = validate_runtime_paths_stay_under_root(path_module=module, repo_root=sandbox)

        self.assertEqual(failures, ["EXTERNAL_PATH"])

    def test_validate_runtime_scorecard_contract_reports_missing_runtime_verifier(self) -> None:
        failures = validate_runtime_scorecard_contract(
            payload={
                "categories": [
                    {
                        "id": "runtime_coherence",
                        "requiredArtifacts": [
                            "execution/doctor.py",
                            "config/anti_drift_policy.json",
                        ],
                        "requiredVerifiers": [],
                    }
                ]
            }
        )

        self.assertEqual(failures, ["runtime_coherence missing execution/verify_runtime_contract.py"])

    def test_validate_runtime_policy_contract_reports_missing_doctor_rule(self) -> None:
        failures = validate_runtime_policy_contract(payload={"rules": []})

        self.assertEqual(failures, ["missing doctor-is-the-operator-entrypoint rule"])

    def test_validate_doctor_entrypoint_reports_missing_runtime_contract_reference(self) -> None:
        sandbox = REPO_ROOT / ".tmp" / "test_verify_runtime_contract" / self._testMethodName
        sandbox.mkdir(parents=True, exist_ok=True)
        doctor_path = sandbox / "doctor.py"
        doctor_path.write_text(
            "from execution.verify_path_authority import run_checks\n"
            "print('Summary: OK=1 WARN=0 FAIL=0')\n",
            encoding="utf-8",
        )

        failures = validate_doctor_entrypoint(doctor_path=doctor_path)

        self.assertEqual(
            failures,
            [
                "verify_archive_boundaries",
                "verify_root_hygiene",
                "verify_runtime_contract",
            ],
        )

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