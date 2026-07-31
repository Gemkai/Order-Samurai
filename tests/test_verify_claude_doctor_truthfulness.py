from __future__ import annotations

import json
import os
import shutil
import sys
import unittest
from unittest import mock
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from execution.verify_claude_doctor_truthfulness import (  # type: ignore[attr-defined]
    DOCTOR_COMMAND_ENTRYPOINT,
    collect_server_disable_state,
    find_doctor_surfaces,
    policy_references_doctor_artifact,
    run_checks,
    summarize,
)


def make_anti_drift_policy() -> dict:
    return {
        "version": 1,
        "principles": ["Operator diagnostics must report effective runtime state."],
        "rules": [
            {
                "id": "doctor-must-report-effective-state",
                "severity": "high",
                "statement": "Doctor must summarize effective gateway and MCP state.",
                "expectedRuntimeArtifacts": [
                    "scripts/doctor.py",
                    "llm/gateway.py",
                    "scripts/mcp_server_registry.py",
                ],
            }
        ],
    }


def make_matrix(*, with_compat: bool = True, compat_path: str = "execution/doctor.py",
                compat_owner: str = "Claude control plane") -> dict:
    surfaces = [
        {
            "path": "scripts/doctor.py",
            "role": "operator",
            "owner": "Claude control plane",
            "discoverabilityContract": "canonical doctor entrypoint",
        },
        {
            "path": "commands/doctor.md",
            "role": "operator",
            "owner": "Claude governance docs",
            "discoverabilityContract": "doctor usage contract",
        },
    ]
    if with_compat:
        surfaces.append(
            {
                "path": compat_path,
                "role": "compatibility",
                "owner": compat_owner,
                "discoverabilityContract": "legacy shim",
            }
        )
    return {"version": 1, "surfaces": surfaces}


def make_mcp(disabled_names: list[str] | None = None) -> dict:
    disabled_names = disabled_names or []
    servers = {}
    for name in ("alpha", "beta", "gamma"):
        servers[name] = {"command": "python", "disabled": name in disabled_names}
    return {"mcpServers": servers}


class VerifyClaudeDoctorTruthfulnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self._sandbox_root = (
            REPO_ROOT / ".tmp" / "test_verify_claude_doctor_truthfulness" / self._testMethodName
        )
        if self._sandbox_root.exists():
            shutil.rmtree(self._sandbox_root)
        self._sandbox_root.mkdir(parents=True, exist_ok=True)
        self._saved_env = os.environ.get("CLAUDE_RUNTIME_ROOT")

    def tearDown(self) -> None:
        if self._saved_env is None:
            os.environ.pop("CLAUDE_RUNTIME_ROOT", None)
        else:
            os.environ["CLAUDE_RUNTIME_ROOT"] = self._saved_env

    # -- fixture helpers ---------------------------------------------------
    def _write_configs(self, policy: dict, matrix: dict) -> tuple[Path, Path]:
        policy_path = self._sandbox_root / "claude_anti_drift_policy.json"
        matrix_path = self._sandbox_root / "claude_surface_matrix.json"
        policy_path.write_text(json.dumps(policy), encoding="utf-8")
        matrix_path.write_text(json.dumps(matrix), encoding="utf-8")
        return policy_path, matrix_path

    def _make_runtime(self, *, with_doctor_md: bool = True, with_compat: bool = True,
                      compat_path: str = "execution/doctor.py",
                      mcp: dict | None = None) -> Path:
        runtime = self._sandbox_root / "runtime_root"
        (runtime / "commands").mkdir(parents=True, exist_ok=True)
        if with_doctor_md:
            (runtime / DOCTOR_COMMAND_ENTRYPOINT).write_text("# doctor\n", encoding="utf-8")
        if with_compat:
            shim = runtime / compat_path
            shim.parent.mkdir(parents=True, exist_ok=True)
            shim.write_text("# shim\n", encoding="utf-8")
        (runtime / "mcp.json").write_text(
            json.dumps(mcp if mcp is not None else make_mcp(["gamma"])), encoding="utf-8"
        )
        return runtime

    # -- run_checks scenarios ---------------------------------------------
    def test_declared_and_present_doctor_entrypoint_has_no_failures(self) -> None:
        policy_path, matrix_path = self._write_configs(
            make_anti_drift_policy(), make_matrix()
        )
        runtime = self._make_runtime()

        results = run_checks(
            policy_path=policy_path, matrix_path=matrix_path, runtime_root_dir=runtime
        )

        failures = [row for row in results if row["status"] == "FAIL"]
        self.assertEqual(failures, [])
        entrypoint_rows = [row for row in results if row["label"] == "doctor.entrypoint"]
        self.assertEqual(len(entrypoint_rows), 1)
        self.assertEqual(entrypoint_rows[0]["status"], "OK")

    @mock.patch.dict(
        os.environ, {"ORDER_SAMURAI_AUDIT_PROFILE": "full"}
    )  # asserts the opinionated tier; baseline is the shipped default
    def test_declared_compat_shim_absent_on_disk_fails(self) -> None:
        policy_path, matrix_path = self._write_configs(
            make_anti_drift_policy(), make_matrix()
        )
        # Runtime has the doctor doc but the declared compat shim is absent.
        runtime = self._make_runtime(with_compat=False)

        results = run_checks(
            policy_path=policy_path, matrix_path=matrix_path, runtime_root_dir=runtime
        )

        shim_rows = [row for row in results if row["label"] == "doctor.compat_shim_present"]
        self.assertEqual(len(shim_rows), 1)
        self.assertEqual(shim_rows[0]["status"], "FAIL")
        _counts, exit_code = summarize(results)
        self.assertEqual(exit_code, 1)

    def test_missing_runtime_root_warns_without_failing(self) -> None:
        policy_path, matrix_path = self._write_configs(
            make_anti_drift_policy(), make_matrix()
        )
        absent_runtime = self._sandbox_root / "does_not_exist"

        results = run_checks(
            policy_path=policy_path, matrix_path=matrix_path, runtime_root_dir=absent_runtime
        )

        failures = [row for row in results if row["status"] == "FAIL"]
        self.assertEqual(failures, [])
        warns = {row["label"] for row in results if row["status"] == "WARN"}
        self.assertIn("doctor.entrypoint", warns)
        self.assertIn("doctor.mcp_disabled_subset", warns)

    def test_missing_doctor_entrypoint_warns(self) -> None:
        policy_path, matrix_path = self._write_configs(
            make_anti_drift_policy(), make_matrix(with_compat=True)
        )
        runtime = self._make_runtime(with_doctor_md=False)

        results = run_checks(
            policy_path=policy_path, matrix_path=matrix_path, runtime_root_dir=runtime
        )

        entrypoint_rows = [row for row in results if row["label"] == "doctor.entrypoint"]
        self.assertEqual(len(entrypoint_rows), 1)
        self.assertEqual(entrypoint_rows[0]["status"], "WARN")
        # A missing doc entrypoint is a WARN, not a FAIL.
        self.assertEqual([r for r in results if r["status"] == "FAIL"], [])

    def test_honor_system_rows_present(self) -> None:
        policy_path, matrix_path = self._write_configs(
            make_anti_drift_policy(), make_matrix()
        )
        # mcp.json with no disabled/enabled metadata -> honor-system OK row.
        bare_mcp = {"mcpServers": {"alpha": {"command": "python"}}}
        runtime = self._make_runtime(mcp=bare_mcp)

        results = run_checks(
            policy_path=policy_path, matrix_path=matrix_path, runtime_root_dir=runtime
        )

        labels = {row["label"]: row for row in results}
        self.assertIn("doctor.runtime_script_honor_system", labels)
        self.assertEqual(labels["doctor.runtime_script_honor_system"]["status"], "OK")
        # No disabled/enabled metadata is reported as an honor-system OK, not a FAIL.
        self.assertEqual(labels["doctor.mcp_disabled_subset"]["status"], "OK")
        self.assertIn("honor-system", labels["doctor.mcp_disabled_subset"]["detail"])

    def test_runtime_root_honored_via_env(self) -> None:
        policy_path, matrix_path = self._write_configs(
            make_anti_drift_policy(), make_matrix()
        )
        runtime = self._make_runtime()
        os.environ["CLAUDE_RUNTIME_ROOT"] = str(runtime)

        # runtime_root_dir omitted -> falls back to runtime_root() which reads the env.
        results = run_checks(policy_path=policy_path, matrix_path=matrix_path)

        self.assertEqual([r for r in results if r["status"] == "FAIL"], [])
        entrypoint_rows = [row for row in results if row["label"] == "doctor.entrypoint"]
        self.assertEqual(entrypoint_rows[0]["status"], "OK")

    def test_missing_policy_returns_single_failure(self) -> None:
        _policy_path, matrix_path = self._write_configs(
            make_anti_drift_policy(), make_matrix()
        )
        runtime = self._make_runtime()

        results = run_checks(
            policy_path=self._sandbox_root / "absent_policy.json",
            matrix_path=matrix_path,
            runtime_root_dir=runtime,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "FAIL")
        self.assertEqual(results[0]["detail"], "missing")

    def test_mcp_disables_undeclared_server_fails(self) -> None:
        policy_path, matrix_path = self._write_configs(
            make_anti_drift_policy(), make_matrix()
        )
        mcp = {
            "mcpServers": {"alpha": {"command": "python", "disabled": False}},
            "disabled": ["ghost-server"],
        }
        runtime = self._make_runtime(mcp=mcp)

        results = run_checks(
            policy_path=policy_path, matrix_path=matrix_path, runtime_root_dir=runtime
        )

        subset_rows = [row for row in results if row["label"] == "doctor.mcp_disabled_subset"]
        self.assertEqual(len(subset_rows), 1)
        self.assertEqual(subset_rows[0]["status"], "FAIL")
        self.assertIn("ghost-server", subset_rows[0]["detail"])

    def test_compat_shim_owner_mismatch_warns(self) -> None:
        policy_path, matrix_path = self._write_configs(
            make_anti_drift_policy(),
            make_matrix(compat_owner="some other owner"),
        )
        runtime = self._make_runtime()

        results = run_checks(
            policy_path=policy_path, matrix_path=matrix_path, runtime_root_dir=runtime
        )

        rows = [row for row in results if row["label"] == "doctor.canonical_vs_compat"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "WARN")

    # -- pure-function unit checks ----------------------------------------
    def test_collect_server_disable_state_counts_per_server_flags(self) -> None:
        declared, disabled, present, malformed, unknown = collect_server_disable_state(
            mcp_payload=make_mcp(["beta"])
        )

        self.assertEqual(declared, {"alpha", "beta", "gamma"})
        self.assertEqual(disabled, {"beta"})
        self.assertTrue(present)
        self.assertEqual(malformed, [])
        self.assertEqual(unknown, [])

    def test_collect_server_disable_state_reports_no_metadata(self) -> None:
        _declared, _disabled, present, _malformed, _unknown = collect_server_disable_state(
            mcp_payload={"mcpServers": {"alpha": {"command": "python"}}}
        )

        self.assertFalse(present)

    def test_find_doctor_surfaces_identifies_canonical_and_compat(self) -> None:
        surfaces = find_doctor_surfaces(matrix_payload=make_matrix())

        self.assertIsNotNone(surfaces["canonical"])
        self.assertIsNotNone(surfaces["compat"])
        self.assertEqual(surfaces["canonical"]["path"], "scripts/doctor.py")
        self.assertEqual(surfaces["compat"]["path"], "execution/doctor.py")

    def test_policy_references_doctor_artifact_true_for_doctor_rule(self) -> None:
        self.assertTrue(
            policy_references_doctor_artifact(payload=make_anti_drift_policy())
        )
        self.assertFalse(
            policy_references_doctor_artifact(
                payload={"rules": [{"expectedRuntimeArtifacts": ["llm/gateway.py"]}]}
            )
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
