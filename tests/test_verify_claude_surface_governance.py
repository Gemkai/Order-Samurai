from __future__ import annotations

import json
import os
import shutil
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from execution.verify_claude_surface_governance import (  # type: ignore[attr-defined]
    check_compat_ownership,
    check_surface_existence,
    run_checks,
    summarize,
)


def make_surface(
    path: str,
    *,
    role: str = "operator",
    owner: str = "test control plane",
    contract: str = "test contract",
) -> dict:
    return {
        "path": path,
        "role": role,
        "owner": owner,
        "discoverabilityContract": contract,
    }


def make_matrix(
    surfaces: list[dict],
    *,
    surface_roles: list[str] | None = None,
) -> dict:
    default_roles = ["runtime", "operator", "state", "compatibility"]
    return {
        "version": 1,
        "surfaceRoles": default_roles if surface_roles is None else surface_roles,
        "surfaces": surfaces,
    }


class VerifyClaudeSurfaceGovernanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_runtime_root = os.environ.get("CLAUDE_RUNTIME_ROOT")
        self.sandbox = (
            REPO_ROOT / ".tmp" / "test_verify_claude_surface_governance" / self._testMethodName
        )
        if self.sandbox.exists():
            shutil.rmtree(self.sandbox)
        self.sandbox.mkdir(parents=True)

    def tearDown(self) -> None:
        if self._saved_runtime_root is None:
            os.environ.pop("CLAUDE_RUNTIME_ROOT", None)
        else:
            os.environ["CLAUDE_RUNTIME_ROOT"] = self._saved_runtime_root

    def _write_matrix(self, matrix: dict) -> Path:
        matrix_path = self.sandbox / "claude_surface_matrix.json"
        matrix_path.write_text(json.dumps(matrix), encoding="utf-8")
        return matrix_path

    def _make_root(self, *relative_paths: str) -> Path:
        root = self.sandbox / "runtime_root"
        root.mkdir(exist_ok=True)
        for relative in relative_paths:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("present\n", encoding="utf-8")
        return root

    def _rows_by_label(self, results: list[dict]) -> dict[str, dict]:
        return {row["label"]: row for row in results}

    def test_check_surface_existence_splits_missing_by_runtime_role(self) -> None:
        root = self._make_root("scripts/present.py")
        payload = make_matrix(
            [
                make_surface("scripts/present.py", role="runtime"),
                make_surface("scripts/gone.py", role="runtime"),
                make_surface("projects", role="state"),
            ]
        )

        missing_runtime, missing_other = check_surface_existence(payload=payload, root=root)

        self.assertEqual(missing_runtime, ["scripts/gone.py (role runtime)"])
        self.assertEqual(missing_other, ["projects (role state)"])

    def test_check_compat_ownership_flags_compat_surface_owning_itself(self) -> None:
        payload = make_matrix(
            [make_surface("execution/doctor.py", role="compatibility", owner="execution/doctor.py")]
        )

        findings = check_compat_ownership(payload=payload)

        self.assertEqual(
            findings, ["execution/doctor.py (compat surface names itself as owner)"]
        )

    def test_check_compat_ownership_accepts_distinct_canonical_owner(self) -> None:
        payload = make_matrix(
            [make_surface("execution/doctor.py", role="compatibility", owner="Claude control plane")]
        )

        self.assertEqual(check_compat_ownership(payload=payload), [])

    def test_run_checks_fails_when_runtime_surface_is_missing(self) -> None:
        root = self._make_root()
        matrix_path = self._write_matrix(
            make_matrix([make_surface("scripts/gone.py", role="runtime")])
        )

        results = run_checks(matrix_path=matrix_path, root=root)

        row = self._rows_by_label(results)["claude-surface-governance.existence"]
        self.assertEqual(row["status"], "FAIL")
        self.assertIn("scripts/gone.py", row["detail"])
        self.assertEqual(summarize(results)[1], 1)

    def test_run_checks_warns_but_passes_when_non_runtime_surface_is_missing(self) -> None:
        root = self._make_root()
        matrix_path = self._write_matrix(make_matrix([make_surface("projects", role="state")]))

        results = run_checks(matrix_path=matrix_path, root=root)

        row = self._rows_by_label(results)["claude-surface-governance.existence"]
        self.assertEqual(row["status"], "WARN")
        self.assertIn("projects", row["detail"])
        self.assertEqual(summarize(results)[1], 0)

    def test_run_checks_reports_structural_gap_via_shared_validator(self) -> None:
        root = self._make_root("scripts/present.py")
        matrix_path = self._write_matrix(
            make_matrix([make_surface("scripts/present.py", role="runtime", owner="")])
        )

        results = run_checks(matrix_path=matrix_path, root=root)

        row = self._rows_by_label(results)["claude-surface-governance.structure"]
        self.assertEqual(row["status"], "FAIL")
        self.assertIn("scripts/present.py", row["detail"])

    def test_run_checks_fails_role_outside_declared_surface_roles(self) -> None:
        root = self._make_root("scripts/present.py")
        matrix_path = self._write_matrix(
            make_matrix([make_surface("scripts/present.py", role="mystery")])
        )

        results = run_checks(matrix_path=matrix_path, root=root)

        row = self._rows_by_label(results)["claude-surface-governance.roles"]
        self.assertEqual(row["status"], "FAIL")
        self.assertIn("mystery", row["detail"])

    def test_run_checks_fails_when_matrix_declares_no_surface_roles(self) -> None:
        root = self._make_root("scripts/present.py")
        matrix_path = self._write_matrix(
            make_matrix([make_surface("scripts/present.py")], surface_roles=[])
        )

        results = run_checks(matrix_path=matrix_path, root=root)

        row = self._rows_by_label(results)["claude-surface-governance.roles"]
        self.assertEqual(row["status"], "FAIL")

    def test_run_checks_passes_on_fully_governed_matrix(self) -> None:
        root = self._make_root("scripts/present.py", "shims/legacy.py")
        matrix_path = self._write_matrix(
            make_matrix(
                [
                    make_surface("scripts/present.py", role="runtime"),
                    make_surface(
                        "shims/legacy.py", role="compatibility", owner="Claude control plane"
                    ),
                ]
            )
        )

        results = run_checks(matrix_path=matrix_path, root=root)

        self.assertEqual({row["status"] for row in results}, {"OK"})
        self.assertEqual(summarize(results)[1], 0)

    def test_run_checks_warns_when_runtime_root_is_absent(self) -> None:
        matrix_path = self._write_matrix(
            make_matrix([make_surface("scripts/gone.py", role="runtime")])
        )

        results = run_checks(matrix_path=matrix_path, root=self.sandbox / "no_such_root")

        row = self._rows_by_label(results)["claude-surface-governance.existence"]
        self.assertEqual(row["status"], "WARN")
        self.assertIn("existence checks skipped", row["detail"])
        self.assertEqual(summarize(results)[1], 0)

    def test_run_checks_honors_claude_runtime_root_env_when_no_root_given(self) -> None:
        root = self._make_root()
        os.environ["CLAUDE_RUNTIME_ROOT"] = str(root)
        matrix_path = self._write_matrix(
            make_matrix([make_surface("scripts/gone.py", role="runtime")])
        )

        results = run_checks(matrix_path=matrix_path)

        row = self._rows_by_label(results)["claude-surface-governance.existence"]
        self.assertEqual(row["status"], "FAIL")
        self.assertIn(str(root), row["detail"])

    def test_run_checks_fails_on_missing_matrix_file(self) -> None:
        results = run_checks(
            matrix_path=self.sandbox / "absent_matrix.json", root=self._make_root()
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "FAIL")
        self.assertEqual(results[0]["detail"], "missing")

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
