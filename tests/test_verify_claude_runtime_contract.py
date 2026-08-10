from __future__ import annotations

import shutil
import sys
import os
import unittest
from unittest import mock
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from execution import verify_claude_runtime_contract as vc  # type: ignore[attr-defined]


class VerifyClaudeRuntimeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = REPO_ROOT / ".tmp" / "test_verify_claude_runtime_contract" / self._testMethodName
        if self.sandbox.exists():
            shutil.rmtree(self.sandbox)
        self.sandbox.mkdir(parents=True, exist_ok=True)

    def _seed_required_artifacts(self) -> None:
        (self.sandbox / "commands").mkdir(parents=True, exist_ok=True)
        for rel in vc.REQUIRED_RUNTIME_ARTIFACTS:
            path = self.sandbox / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}" if rel.endswith(".json") else "# doc\n", encoding="utf-8")

    def _labels(self, results: list[dict]) -> dict:
        return {r["label"]: r for r in results}

    @mock.patch.dict(
        os.environ, {"ORDER_SAMURAI_AUDIT_PROFILE": "full"}
    )  # asserts the opinionated tier; baseline is the shipped default
    def test_missing_required_artifacts_fails(self) -> None:
        # Empty sandbox: no required artifacts present.
        results = vc.run_checks(runtime_root_dir=self.sandbox)
        artifacts = self._labels(results)["claude_runtime_contract.artifacts"]
        self.assertEqual(artifacts["status"], "FAIL")
        self.assertIn("settings.json", artifacts["detail"])

    def test_present_required_artifacts_ok(self) -> None:
        self._seed_required_artifacts()
        results = vc.run_checks(runtime_root_dir=self.sandbox)
        artifacts = self._labels(results)["claude_runtime_contract.artifacts"]
        self.assertEqual(artifacts["status"], "OK")

    def test_missing_runtime_root_warns_not_crashes(self) -> None:
        results = vc.run_checks(runtime_root_dir=self.sandbox / "does-not-exist")
        artifacts = self._labels(results)["claude_runtime_contract.artifacts"]
        self.assertEqual(artifacts["status"], "WARN")

    def test_every_foundational_area_is_reported(self) -> None:
        self._seed_required_artifacts()
        results = vc.run_checks(runtime_root_dir=self.sandbox)
        labels = self._labels(results)
        for area, _module in vc.FOUNDATIONAL_VERIFIERS:
            self.assertIn(f"claude_runtime_contract.{area}", labels)

    def test_summarize_exit_code_contract(self) -> None:
        ok = [vc._make_result("OK", "a", "d"), vc._make_result("WARN", "b", "d")]
        counts, code = vc.summarize(ok)
        self.assertEqual(code, 0)
        self.assertEqual(counts["WARN"], 1)

        bad = ok + [vc._make_result("FAIL", "c", "d")]
        _counts, code = vc.summarize(bad)
        self.assertEqual(code, 1)

    def test_rollup_is_worst_status(self) -> None:
        rows = [
            vc._make_result("OK", "x", "d"),
            vc._make_result("WARN", "y", "d"),
            vc._make_result("FAIL", "z", "d"),
        ]
        self.assertEqual(vc._roll_up(rows), "FAIL")
        self.assertEqual(vc._roll_up(rows[:2]), "WARN")
        self.assertEqual(vc._roll_up(rows[:1]), "OK")

    def test_custom_root_reaches_siblings_with_a_differently_named_param(self) -> None:
        # verify_claude_path_authority names its root kwarg `runtime_root_path` and
        # verify_claude_runtime_coupling names it `root` -- not `runtime_root_dir`.
        # run_checks(runtime_root_dir=...) must still propagate the caller's root to
        # them, not silently fall back to their own default (the live ~/.claude) via
        # the blanket `except TypeError: rows = run()` path.
        import importlib

        path_authority = importlib.import_module("execution.verify_claude_path_authority")
        runtime_coupling = importlib.import_module("execution.verify_claude_runtime_coupling")

        received: dict = {}

        def path_authority_stub(runtime_root_path=None, allowlist=None):
            received["path_authority"] = runtime_root_path
            return []

        def runtime_coupling_stub(*, policy_path=None, anti_drift_path=None, root=None):
            received["runtime_coupling"] = root
            return []

        with mock.patch.object(path_authority, "run_checks", path_authority_stub), \
                mock.patch.object(runtime_coupling, "run_checks", runtime_coupling_stub):
            vc.run_checks(runtime_root_dir=self.sandbox)

        self.assertEqual(received.get("path_authority"), self.sandbox)
        self.assertEqual(received.get("runtime_coupling"), self.sandbox)


if __name__ == "__main__":
    unittest.main()
