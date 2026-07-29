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


from execution import verify_claude_hook_contract as vhc  # type: ignore[attr-defined]


SANDBOX_BASE = REPO_ROOT / ".tmp" / "test_verify_claude_hook_contract"

# A portable dispatch command mirroring the live settings.json shape.
PORTABLE_COMMAND = (
    "python -u -c \"import pathlib,runpy,sys;"
    "script=pathlib.Path.home()/'.claude'/'scripts/hook_dispatch.py';"
    "sys.argv=[str(script),*sys.argv[1:]];"
    "runpy.run_path(str(script),run_name='__main__')\" nudge"
)

# Same dispatch but pinned to a literal absolute Claude-home path (non-portable).
LITERAL_HOME_COMMAND = (
    'python -u "~/.claude/hooks/foo.py"'
)


def _settings_with_command(command: str) -> dict:
    return {
        "model": "sonnet",
        "hooks": {
            "Stop": [
                {"hooks": [{"type": "command", "command": command}]},
            ],
        },
    }


class VerifyClaudeHookContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = SANDBOX_BASE / self._testMethodName
        if self.sandbox.exists():
            shutil.rmtree(self.sandbox)
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self._saved_env = os.environ.get("CLAUDE_RUNTIME_ROOT")

    def tearDown(self) -> None:
        if self._saved_env is None:
            os.environ.pop("CLAUDE_RUNTIME_ROOT", None)
        else:
            os.environ["CLAUDE_RUNTIME_ROOT"] = self._saved_env

    def _write(self, rel: str, content: str) -> Path:
        target = self.sandbox / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def _write_settings(self, payload: dict) -> None:
        self._write("settings.json", json.dumps(payload, indent=2))

    def _write_generators(self) -> None:
        for artifact in vhc.GENERATOR_ARTIFACTS:
            self._write(artifact, "# generator\n")

    def _labels(self, results: list[dict]) -> dict[str, dict]:
        return {result["label"]: result for result in results}

    # --- pure-function coverage -------------------------------------------

    def test_extract_hook_script_ref_from_portable_dispatch_command(self) -> None:
        refs = vhc.extract_hook_script_refs(PORTABLE_COMMAND)

        self.assertEqual(refs, {"scripts/hook_dispatch.py"})

    def test_extract_hook_script_ref_strips_claude_home_prefix(self) -> None:
        refs = vhc.extract_hook_script_refs(LITERAL_HOME_COMMAND)

        self.assertEqual(refs, {"hooks/foo.py"})

    def test_missing_hook_scripts_flags_absent_reference(self) -> None:
        missing = vhc.missing_hook_scripts([PORTABLE_COMMAND], self.sandbox)

        self.assertEqual(missing, ["scripts/hook_dispatch.py"])

    def test_missing_hook_scripts_passes_when_file_present(self) -> None:
        self._write("scripts/hook_dispatch.py", "# dispatch\n")

        missing = vhc.missing_hook_scripts([PORTABLE_COMMAND], self.sandbox)

        self.assertEqual(missing, [])

    def test_literal_offenders_flag_pinned_home_path(self) -> None:
        offenders = vhc.hook_command_literal_offenders([LITERAL_HOME_COMMAND])

        self.assertEqual(len(offenders), 1)
        self.assertIn("~/.claude", offenders[0])

    def test_literal_offenders_ignore_portable_command(self) -> None:
        offenders = vhc.hook_command_literal_offenders([PORTABLE_COMMAND])

        self.assertEqual(offenders, [])

    def test_collect_hook_commands_reports_malformed_shape(self) -> None:
        commands, errors = vhc.collect_hook_commands({"hooks": {"Stop": "not-a-list"}})

        self.assertEqual(commands, [])
        self.assertTrue(errors)

    # --- run_checks integration -------------------------------------------

    def test_happy_path_aligned_settings_has_no_failures(self) -> None:
        self._write_generators()
        self._write("scripts/hook_dispatch.py", "# dispatch\n")
        self._write_settings(_settings_with_command(PORTABLE_COMMAND))

        with mock.patch.dict(os.environ, {"CLAUDE_RUNTIME_ROOT": str(self.sandbox)}):
            results = vhc.run_checks()

        by_label = self._labels(results)
        self.assertNotIn("FAIL", {r["status"] for r in results})
        self.assertEqual(by_label["claude-hook-contract.hooks-section"]["status"], "OK")
        self.assertEqual(by_label["claude-hook-contract.hook-scripts"]["status"], "OK")
        self.assertEqual(by_label["claude-hook-contract.hook-portability"]["status"], "OK")

    def test_settings_referencing_missing_hook_file_fails(self) -> None:
        self._write_generators()
        # deliberately do NOT create scripts/hook_dispatch.py
        self._write_settings(_settings_with_command(PORTABLE_COMMAND))

        with mock.patch.dict(os.environ, {"CLAUDE_RUNTIME_ROOT": str(self.sandbox)}):
            results = vhc.run_checks()

        by_label = self._labels(results)
        self.assertEqual(by_label["claude-hook-contract.hook-scripts"]["status"], "FAIL")
        self.assertIn("scripts/hook_dispatch.py", by_label["claude-hook-contract.hook-scripts"]["detail"])

    def test_hook_command_with_literal_home_path_fails(self) -> None:
        self._write_generators()
        # create the referenced file so ONLY the portability check fails
        self._write("hooks/foo.py", "# hook\n")
        self._write_settings(_settings_with_command(LITERAL_HOME_COMMAND))

        with mock.patch.dict(os.environ, {"CLAUDE_RUNTIME_ROOT": str(self.sandbox)}):
            results = vhc.run_checks()

        by_label = self._labels(results)
        self.assertEqual(by_label["claude-hook-contract.hook-portability"]["status"], "FAIL")
        self.assertEqual(by_label["claude-hook-contract.hook-scripts"]["status"], "OK")
        self.assertIn("~/.claude", by_label["claude-hook-contract.hook-portability"]["detail"])

    def test_missing_runtime_root_warns_without_crash(self) -> None:
        absent = self.sandbox / "does-not-exist"

        results = vhc.run_checks(runtime_root_dir=absent)

        self.assertEqual(results[-1]["status"], "WARN")
        self.assertEqual(results[-1]["label"], "claude-hook-contract.runtime-root")
        self.assertNotIn("FAIL", {r["status"] for r in results})

    def test_summarize_sets_nonzero_exit_for_failures(self) -> None:
        counts, exit_code = vhc.summarize(
            [
                {"status": "OK", "label": "a", "detail": "x"},
                {"status": "FAIL", "label": "b", "detail": "y"},
            ]
        )

        self.assertEqual(counts["FAIL"], 1)
        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
