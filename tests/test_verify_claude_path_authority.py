from __future__ import annotations

import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from execution import verify_claude_path_authority as vpa  # type: ignore[attr-defined]


LITERALS = vpa.forbidden_literals({})


class VerifyClaudePathAuthorityTests(unittest.TestCase):
    def _sandbox(self, *surfaces: str) -> Path:
        sandbox = REPO_ROOT / ".tmp" / "test_verify_claude_path_authority" / self._testMethodName
        if sandbox.exists():
            shutil.rmtree(sandbox)
        for surface in surfaces or vpa.SCAN_SURFACES:
            (sandbox / surface).mkdir(parents=True, exist_ok=True)
        return sandbox

    def test_scan_flags_literal_mac_claude_home_in_scripts(self) -> None:
        sandbox = self._sandbox()
        (sandbox / "scripts" / "drifted.py").write_text(
            'HUB_ROOT = "~/.claude/data"\n', encoding="utf-8"
        )

        offenders, missing = vpa.scan_path_literals(root=sandbox, literals=LITERALS)

        self.assertEqual(missing, [])
        self.assertEqual(offenders, ["scripts/drifted.py (~/.claude)"])

    def test_scan_flags_windows_single_backslash_form_in_hooks(self) -> None:
        sandbox = self._sandbox()
        (sandbox / "hooks" / "drifted_hook.py").write_text(
            'ROOT = r"~/.claude"\n', encoding="utf-8"
        )

        offenders, _ = vpa.scan_path_literals(root=sandbox, literals=LITERALS)

        self.assertEqual(offenders, ["hooks/drifted_hook.py (~/.claude)"])

    def test_scan_flags_windows_doubled_backslash_form_in_json(self) -> None:
        sandbox = self._sandbox()
        (sandbox / "safety" / "drifted.json").write_text(
            '{"root": "C:\\\\Users\\\\exampleuser\\\\.claude\\\\hooks"}\n', encoding="utf-8"
        )

        offenders, _ = vpa.scan_path_literals(root=sandbox, literals=LITERALS)

        self.assertEqual(offenders, ["safety/drifted.json (~/.claude)"])

    def test_scan_flags_windows_forward_slash_form(self) -> None:
        sandbox = self._sandbox()
        (sandbox / "orchestration" / "drifted.sh").write_text(
            'ROOT="C:/Users/example/.claude"\n', encoding="utf-8"
        )

        offenders, _ = vpa.scan_path_literals(root=sandbox, literals=LITERALS)

        self.assertEqual(offenders, ["orchestration/drifted.sh (C:/Users/example/.claude)"])

    def test_scan_flags_absolute_antigravity_reference(self) -> None:
        sandbox = self._sandbox()
        (sandbox / "llm" / "bridge.py").write_text(
            'AG = "~/.gemini/antigravity/config.json"\n', encoding="utf-8"
        )

        offenders, _ = vpa.scan_path_literals(root=sandbox, literals=LITERALS)

        self.assertEqual(offenders, ["llm/bridge.py (~/.gemini/antigravity)"])

    def test_portable_home_references_pass(self) -> None:
        sandbox = self._sandbox()
        (sandbox / "scripts" / "portable.py").write_text(
            "from pathlib import Path\n"
            "import os\n"
            'ROOT = Path.home() / ".claude"\n'
            'ALT = os.path.expanduser("~/.claude/data")\n',
            encoding="utf-8",
        )

        offenders, _ = vpa.scan_path_literals(root=sandbox, literals=LITERALS)

        self.assertEqual(offenders, [])

    def test_allowlisted_path_authority_may_anchor_the_home(self) -> None:
        sandbox = self._sandbox()
        (sandbox / "scripts" / "runtime_paths.py").write_text(
            'CLAUDE_HOME = "~/.claude"\n', encoding="utf-8"
        )

        offenders, _ = vpa.scan_path_literals(
            root=sandbox,
            literals=LITERALS,
            allowlist=frozenset({"scripts/runtime_paths.py"}),
        )

        self.assertEqual(offenders, [])

    def test_missing_scan_surface_is_reported_not_raised(self) -> None:
        sandbox = self._sandbox("scripts")

        offenders, missing = vpa.scan_path_literals(root=sandbox, literals=LITERALS)

        self.assertEqual(offenders, [])
        self.assertEqual(missing, ["hooks", "llm", "orchestration", "safety"])

    def test_skip_directories_are_not_descended(self) -> None:
        sandbox = self._sandbox()
        vendored = sandbox / "scripts" / "node_modules" / "pkg"
        vendored.mkdir(parents=True, exist_ok=True)
        (vendored / "vendored.js").write_text(
            'const root = "~/.claude";\n', encoding="utf-8"
        )

        offenders, _ = vpa.scan_path_literals(root=sandbox, literals=LITERALS)

        self.assertEqual(offenders, [])

    def test_oversized_file_is_skipped(self) -> None:
        sandbox = self._sandbox()
        big = (sandbox / "scripts" / "big.py")
        big.write_bytes(
            b'ROOT = "~/.claude"\n' + b"#" * (vpa.MAX_FILE_BYTES + 1)
        )

        offenders, _ = vpa.scan_path_literals(root=sandbox, literals=LITERALS)

        self.assertEqual(offenders, [])

    def test_non_text_extension_is_skipped(self) -> None:
        sandbox = self._sandbox()
        (sandbox / "scripts" / "blob.bin").write_text(
            "~/.claude\n", encoding="utf-8"
        )

        offenders, _ = vpa.scan_path_literals(root=sandbox, literals=LITERALS)

        self.assertEqual(offenders, [])

    def test_extensionless_small_file_is_scanned(self) -> None:
        sandbox = self._sandbox()
        (sandbox / "scripts" / "launcher").write_text(
            "#!/bin/sh\ncd ~/.claude\n", encoding="utf-8"
        )

        offenders, _ = vpa.scan_path_literals(root=sandbox, literals=LITERALS)

        self.assertEqual(offenders, ["scripts/launcher (~/.claude)"])

    def test_run_checks_passes_on_clean_sandbox_via_env_override(self) -> None:
        sandbox = self._sandbox()
        (sandbox / "scripts" / "clean.py").write_text(
            'ROOT = __import__("pathlib").Path.home() / ".claude"\n', encoding="utf-8"
        )

        with mock.patch.dict(os.environ, {"CLAUDE_RUNTIME_ROOT": str(sandbox)}):
            results = vpa.run_checks()

        statuses = {result["status"] for result in results}
        self.assertEqual(statuses, {"OK"})

    def test_run_checks_fails_on_offender_and_warns_on_missing_surfaces(self) -> None:
        sandbox = self._sandbox("scripts")
        (sandbox / "scripts" / "drifted.py").write_text(
            'HUB_ROOT = "~/.claude"\n', encoding="utf-8"
        )

        with mock.patch.dict(os.environ, {"CLAUDE_RUNTIME_ROOT": str(sandbox)}):
            results = vpa.run_checks()

        by_label = {result["label"]: result for result in results}
        self.assertEqual(by_label["claude-path-authority.scan-surfaces"]["status"], "WARN")
        self.assertEqual(by_label["claude-path-authority.literal-scan"]["status"], "FAIL")
        self.assertIn("scripts/drifted.py", by_label["claude-path-authority.literal-scan"]["detail"])

    def test_run_checks_warns_when_runtime_root_is_absent(self) -> None:
        sandbox = self._sandbox()
        absent = sandbox / "does-not-exist"

        results = vpa.run_checks(runtime_root_path=absent)

        self.assertEqual(results[-1]["status"], "WARN")
        self.assertEqual(results[-1]["label"], "claude-path-authority.runtime-root")

    def test_default_allowlist_comes_from_the_policy_rule(self) -> None:
        rule = {
            "expectedRuntimeArtifacts": ["scripts/runtime_paths.py"],
            "allowedBridges": ["scripts/legacy_bridge.py"],
        }

        allowlist = vpa.default_allowlist(rule)

        self.assertEqual(
            allowlist, frozenset({"scripts/runtime_paths.py", "scripts/legacy_bridge.py"})
        )

    def test_forbidden_literals_include_policy_target_runtime_root(self) -> None:
        literals = vpa.forbidden_literals({"targetRuntimeRoot": "/opt/other-host/.claude"})

        self.assertIn("/opt/other-host/.claude", literals)

    def test_summarize_sets_nonzero_exit_for_failures(self) -> None:
        counts, exit_code = vpa.summarize(
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
