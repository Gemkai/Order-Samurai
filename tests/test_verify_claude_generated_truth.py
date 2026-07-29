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


from execution import verify_claude_generated_truth as truth  # type: ignore[attr-defined]


# Generator scripts and their generated outputs, mirroring the live anti-drift
# generation rules. Generators are written OLD, outputs NEW, so the baseline is
# "fresh" unless a test deliberately ages an output.
GENERATORS = (
    "scripts/hook_registry.py",
    "scripts/sync_settings_config.py",
    "scripts/mcp_server_registry.py",
    "scripts/sync_mcp_config.py",
    "scripts/sync_runtime_inventory.py",
    "scripts/runtime_paths.py",
)
VALID_OUTPUTS = {
    "settings.json": json.dumps({"hooks": {}}),
    "mcp.json": json.dumps({"mcpServers": {}}),
    "data/runtime_inventory.json": json.dumps({"entries": []}),
    "data/runtime_summary.md": "# Runtime Summary\n\ngenerated truth\n",
}

OLD_MTIME = 1_700_000_000.0
NEW_MTIME = 1_700_000_500.0


def build_policy() -> dict:
    return {
        "version": 1,
        "rules": [
            {
                "id": "single-path-authority",
                "expectedRuntimeArtifacts": ["scripts/runtime_paths.py"],
            },
            {
                "id": "generated-settings-from-hook-registry",
                "verifier": "execution/verify_claude_hook_contract.py",
                "expectedRuntimeArtifacts": [
                    "scripts/hook_registry.py",
                    "scripts/sync_settings_config.py",
                    "settings.json",
                ],
            },
            {
                "id": "generated-mcp-from-launcher-registry",
                "verifier": "execution/verify_claude_mcp_contract.py",
                "expectedRuntimeArtifacts": [
                    "scripts/mcp_server_registry.py",
                    "scripts/sync_mcp_config.py",
                    "mcp.json",
                ],
            },
            {
                "id": "generated-runtime-inventory",
                "verifier": "execution/verify_claude_generated_truth.py",
                "expectedRuntimeArtifacts": [
                    "scripts/sync_runtime_inventory.py",
                    "data/runtime_inventory.json",
                    "data/runtime_summary.md",
                ],
            },
        ],
    }


class VerifyClaudeGeneratedTruthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = (
            REPO_ROOT / ".tmp" / "test_verify_claude_generated_truth" / self._testMethodName
        )
        if self.sandbox.exists():
            shutil.rmtree(self.sandbox)
        self.sandbox.mkdir(parents=True)
        self._saved_env = os.environ.get("CLAUDE_RUNTIME_ROOT")

    def tearDown(self) -> None:
        if self._saved_env is None:
            os.environ.pop("CLAUDE_RUNTIME_ROOT", None)
        else:
            os.environ["CLAUDE_RUNTIME_ROOT"] = self._saved_env

    # -- fixture helpers ---------------------------------------------------

    def _write(self, runtime: Path, rel: str, content: str, mtime: float) -> None:
        path = runtime / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        os.utime(path, (mtime, mtime))

    def _build_runtime(
        self,
        *,
        outputs: dict[str, str] | None = None,
        output_mtimes: dict[str, float] | None = None,
    ) -> Path:
        runtime = self.sandbox / "claude-home"
        runtime.mkdir(parents=True, exist_ok=True)
        for generator in GENERATORS:
            self._write(runtime, generator, "# generator\n", OLD_MTIME)
        outputs = VALID_OUTPUTS if outputs is None else outputs
        output_mtimes = output_mtimes or {}
        for rel, content in outputs.items():
            self._write(runtime, rel, content, output_mtimes.get(rel, NEW_MTIME))
        return runtime

    def _write_policy(self, payload: dict | None = None) -> Path:
        path = self.sandbox / "anti_drift_policy.json"
        path.write_text(json.dumps(payload if payload is not None else build_policy()), encoding="utf-8")
        return path

    def _run(self, runtime: Path, policy: dict | None = None) -> list[dict[str, str]]:
        return truth.run_checks(
            policy_path=self._write_policy(policy),
            runtime_root_dir=runtime,
        )

    @staticmethod
    def _rows(results: list[dict[str, str]], status: str) -> list[dict[str, str]]:
        return [row for row in results if row["status"] == status]

    @staticmethod
    def _labels(results: list[dict[str, str]], status: str) -> set[str]:
        return {row["label"] for row in results if row["status"] == status}

    # -- tests -------------------------------------------------------------

    def test_derive_generated_artifacts_pairs_outputs_with_sync_generators(self) -> None:
        pairs = truth.derive_generated_artifacts(build_policy())

        self.assertEqual(
            pairs,
            [
                ("settings.json", "scripts/sync_settings_config.py"),
                ("mcp.json", "scripts/sync_mcp_config.py"),
                ("data/runtime_inventory.json", "scripts/sync_runtime_inventory.py"),
                ("data/runtime_summary.md", "scripts/sync_runtime_inventory.py"),
            ],
        )

    def test_all_artifacts_present_and_valid_reports_ok(self) -> None:
        runtime = self._build_runtime()

        results = self._run(runtime)

        self.assertEqual(self._rows(results, "FAIL"), [])
        self.assertEqual(self._rows(results, "WARN"), [])
        ok_labels = self._labels(results, "OK")
        self.assertIn("generated_truth.settings.json", ok_labels)
        self.assertIn("generated_truth.data/runtime_inventory.json", ok_labels)
        self.assertIn("generated_truth.rule-wiring", ok_labels)
        self.assertIn("generated_truth.impersonation", ok_labels)

    def test_missing_artifact_warns_but_does_not_fail(self) -> None:
        outputs = {k: v for k, v in VALID_OUTPUTS.items() if k != "data/runtime_inventory.json"}
        runtime = self._build_runtime(outputs=outputs)

        results = self._run(runtime)

        self.assertEqual(self._rows(results, "FAIL"), [])
        warn_labels = self._labels(results, "WARN")
        self.assertIn("generated_truth.data/runtime_inventory.json", warn_labels)
        missing_row = next(
            row for row in results if row["label"] == "generated_truth.data/runtime_inventory.json"
        )
        self.assertEqual(missing_row["status"], "WARN")

    def test_malformed_settings_json_fails(self) -> None:
        outputs = dict(VALID_OUTPUTS)
        outputs["settings.json"] = "{ this is not valid json"
        runtime = self._build_runtime(outputs=outputs)

        results = self._run(runtime)

        failures = self._rows(results, "FAIL")
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["label"], "generated_truth.settings.json")

    def test_stale_generated_output_warns(self) -> None:
        # Age the summary below its generator's OLD_MTIME so it is stale.
        runtime = self._build_runtime(
            output_mtimes={"data/runtime_summary.md": OLD_MTIME - 100}
        )

        results = self._run(runtime)

        self.assertEqual(self._rows(results, "FAIL"), [])
        warn_labels = self._labels(results, "WARN")
        self.assertIn("generated_truth.data/runtime_summary.md.freshness", warn_labels)

    def test_missing_runtime_root_warns_instead_of_crashing(self) -> None:
        results = self._run(self.sandbox / "no-such-home")

        self.assertEqual(self._rows(results, "FAIL"), [])
        warnings = self._rows(results, "WARN")
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["label"], "generated_truth.root")

    def test_missing_policy_fails(self) -> None:
        runtime = self._build_runtime()

        results = truth.run_checks(
            policy_path=self.sandbox / "does_not_exist.json",
            runtime_root_dir=runtime,
        )

        failures = self._rows(results, "FAIL")
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["label"], "claude_anti_drift_policy.json")
        self.assertEqual(failures[0]["detail"], "missing")

    def test_handwritten_doc_impersonating_inventory_warns(self) -> None:
        outputs = {k: v for k, v in VALID_OUTPUTS.items() if k != "data/runtime_inventory.json"}
        runtime = self._build_runtime(outputs=outputs)
        self._write(
            runtime,
            "inventory.md",
            "# Runtime Inventory\n\nThis is the authoritative runtime inventory.\n",
            NEW_MTIME,
        )

        results = self._run(runtime)

        self.assertEqual(self._rows(results, "FAIL"), [])
        impersonation = next(
            row for row in results if row["label"] == "generated_truth.impersonation"
        )
        self.assertEqual(impersonation["status"], "WARN")
        self.assertIn("inventory.md", impersonation["detail"])

    def test_env_override_redirects_checks_to_sandbox_root(self) -> None:
        runtime = self._build_runtime()

        with mock.patch.dict(os.environ, {"CLAUDE_RUNTIME_ROOT": str(runtime)}):
            results = truth.run_checks(policy_path=self._write_policy())

        self.assertEqual(self._rows(results, "FAIL"), [])
        self.assertIn("generated_truth.settings.json", self._labels(results, "OK"))

    def test_summarize_sets_nonzero_exit_for_failures(self) -> None:
        counts, exit_code = truth.summarize(
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
