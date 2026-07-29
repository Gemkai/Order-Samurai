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


from execution import verify_claude_runtime_coupling as coupling  # type: ignore[attr-defined]


ANTIGRAVITY_TILDE = "~/.gemini/antigravity"
ANTIGRAVITY_WINDOWS = "C:\\Users\\example\\.gemini\\antigravity"


def make_pattern_rule(**overrides) -> dict:
    rule = {
        "name": "external-runtime-isolation",
        "scanPaths": ["scripts"],
        "forbiddenPatterns": [ANTIGRAVITY_TILDE, ANTIGRAVITY_WINDOWS],
    }
    rule.update(overrides)
    return rule


def make_root_rule(**overrides) -> dict:
    rule = {
        "name": "historical-surface-isolation",
        "scanPaths": ["scripts"],
        "forbiddenRoots": ["backups", "file-history"],
    }
    rule.update(overrides)
    return rule


class VerifyClaudeRuntimeCouplingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = REPO_ROOT / ".tmp" / "test_verify_claude_runtime_coupling" / self._testMethodName
        if self.sandbox.exists():
            shutil.rmtree(self.sandbox)
        self.sandbox.mkdir(parents=True)

    def _build_runtime(self, files: dict[str, str]) -> Path:
        runtime = self.sandbox / "claude-home"
        runtime.mkdir(parents=True, exist_ok=True)
        for rel, content in files.items():
            path = runtime / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return runtime

    def _write_policy(self, boundary_rules: list[dict]) -> Path:
        path = self.sandbox / "root_hygiene_policy.json"
        path.write_text(json.dumps({"boundaryRules": boundary_rules}), encoding="utf-8")
        return path

    def _write_anti_drift(self) -> Path:
        payload = {
            "rules": [
                {
                    "id": "runtime-coupling-boundary",
                    "severity": "critical",
                    "statement": "Live Claude runtime files must not reference Antigravity-owned paths.",
                }
            ]
        }
        path = self.sandbox / "anti_drift_policy.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _run(self, boundary_rules: list[dict], runtime: Path) -> list[dict[str, str]]:
        return coupling.run_checks(
            policy_path=self._write_policy(boundary_rules),
            anti_drift_path=self._write_anti_drift(),
            root=runtime,
        )

    @staticmethod
    def _rows(results: list[dict[str, str]], status: str) -> list[dict[str, str]]:
        return [row for row in results if row["status"] == status]

    def test_flags_scanned_file_containing_forbidden_pattern(self) -> None:
        runtime = self._build_runtime(
            {"scripts/reaper.py": f'ROOT = "{ANTIGRAVITY_TILDE}/state"\n'}
        )

        results = self._run([make_pattern_rule()], runtime)

        failures = self._rows(results, "FAIL")
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["label"], "runtime_coupling.external-runtime-isolation")
        self.assertIn("scripts/reaper.py", failures[0]["detail"])
        self.assertIn(ANTIGRAVITY_TILDE, failures[0]["detail"])

    def test_allowlisted_file_is_excused_from_pattern_rule(self) -> None:
        # Governance pattern #3 (intentional_scope): a file whose reference to a
        # forbidden pattern is intentional/documented is declared in the rule's
        # allowlist and does not fail — the excusal is reported, not silent.
        runtime = self._build_runtime(
            {"scripts/retention_reaper.py": f'ROOT = "{ANTIGRAVITY_TILDE}/state"\n'}
        )
        rule = make_pattern_rule(
            allowlist=[{"path": "scripts/retention_reaper.py",
                        "reason": "cross-home GC is this script's purpose"}]
        )

        results = self._run([rule], runtime)

        self.assertEqual(self._rows(results, "FAIL"), [])
        ok = self._rows(results, "OK")
        row = next(r for r in ok if r["label"] == "runtime_coupling.external-runtime-isolation")
        self.assertIn("allowlisted", row["detail"])

    def test_allowlist_excuses_only_the_named_file(self) -> None:
        # A non-allowlisted file with the same forbidden pattern still fails.
        runtime = self._build_runtime({
            "scripts/retention_reaper.py": f'ROOT = "{ANTIGRAVITY_TILDE}/state"\n',
            "scripts/rogue.py": f'BAD = "{ANTIGRAVITY_TILDE}/leak"\n',
        })
        rule = make_pattern_rule(
            allowlist=[{"path": "scripts/retention_reaper.py", "reason": "intended"}]
        )

        results = self._run([rule], runtime)

        failures = self._rows(results, "FAIL")
        self.assertEqual(len(failures), 1)
        self.assertIn("scripts/rogue.py", failures[0]["detail"])
        self.assertNotIn("scripts/retention_reaper.py", failures[0]["detail"])

    def test_allowlisted_file_is_excused_from_root_rule(self) -> None:
        runtime = self._build_runtime(
            {"scripts/backup_scheduler.py": 'OUT = "~/.claude/backups/x.zip"\n'}
        )
        rule = make_root_rule(
            allowlist=[{"path": "scripts/backup_scheduler.py",
                        "reason": "produces the backups directory"}]
        )

        results = self._run([rule], runtime)

        self.assertEqual(self._rows(results, "FAIL"), [])

    def test_matches_windows_pattern_in_json_doubled_backslash_form(self) -> None:
        doubled = ANTIGRAVITY_WINDOWS.replace("\\", "\\\\")
        runtime = self._build_runtime(
            {"scripts/paths.json": f'{{"legacy": "{doubled}"}}\n'}
        )

        results = self._run([make_pattern_rule()], runtime)

        failures = self._rows(results, "FAIL")
        self.assertEqual(len(failures), 1)
        self.assertIn("scripts/paths.json", failures[0]["detail"])

    def test_flags_content_referencing_entry_under_forbidden_root(self) -> None:
        runtime = self._build_runtime(
            {"scripts/restore.py": 'SOURCE = "~/.claude/backups/claude-backup-2026-01-01.zip"\n'}
        )

        results = self._run([make_root_rule()], runtime)

        failures = self._rows(results, "FAIL")
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["label"], "runtime_coupling.historical-surface-isolation")
        self.assertIn("scripts/restore.py", failures[0]["detail"])
        self.assertIn("root: backups", failures[0]["detail"])

    def test_reports_all_ok_when_scan_paths_are_clean(self) -> None:
        runtime = self._build_runtime(
            {"scripts/clean.py": 'print("no forbidden references here")\n'}
        )

        results = self._run([make_pattern_rule(), make_root_rule()], runtime)

        self.assertEqual(self._rows(results, "FAIL"), [])
        self.assertEqual(self._rows(results, "WARN"), [])
        labels = {row["label"] for row in self._rows(results, "OK")}
        self.assertIn("runtime_coupling.external-runtime-isolation", labels)
        self.assertIn("runtime_coupling.historical-surface-isolation", labels)

    def test_missing_scan_path_warns_instead_of_failing(self) -> None:
        runtime = self._build_runtime(
            {"scripts/clean.py": 'print("clean")\n'}
        )
        rule = make_pattern_rule(scanPaths=["scripts", "orchestration"])

        results = self._run([rule], runtime)

        self.assertEqual(self._rows(results, "FAIL"), [])
        warnings = self._rows(results, "WARN")
        self.assertEqual(len(warnings), 1)
        self.assertEqual(
            warnings[0]["label"],
            "runtime_coupling.external-runtime-isolation.scan-paths",
        )
        self.assertIn("orchestration", warnings[0]["detail"])

    def test_pack_policy_copies_inside_scan_paths_are_exempt(self) -> None:
        runtime = self._build_runtime(
            {
                "scripts/claude_root_hygiene_policy.json": json.dumps(
                    {"forbiddenPatterns": [ANTIGRAVITY_TILDE]}
                )
            }
        )

        results = self._run([make_pattern_rule()], runtime)

        self.assertEqual(self._rows(results, "FAIL"), [])

    def test_directories_named_in_skip_list_are_not_scanned(self) -> None:
        runtime = self._build_runtime(
            {"scripts/node_modules/dep.js": f'const legacy = "{ANTIGRAVITY_TILDE}";\n'}
        )

        results = self._run([make_pattern_rule()], runtime)

        self.assertEqual(self._rows(results, "FAIL"), [])

    def test_files_over_size_cap_are_not_scanned(self) -> None:
        oversized = ANTIGRAVITY_TILDE + "x" * (coupling.MAX_FILE_BYTES + 1)
        runtime = self._build_runtime({"scripts/huge.py": oversized})

        results = self._run([make_pattern_rule()], runtime)

        self.assertEqual(self._rows(results, "FAIL"), [])

    def test_env_override_redirects_scan_to_sandbox_root(self) -> None:
        runtime = self._build_runtime(
            {"scripts/reaper.py": f'ROOT = "{ANTIGRAVITY_TILDE}"\n'}
        )

        with mock.patch.dict(os.environ, {"CLAUDE_RUNTIME_ROOT": str(runtime)}):
            results = coupling.run_checks(
                policy_path=self._write_policy([make_pattern_rule()]),
                anti_drift_path=self._write_anti_drift(),
            )

        failures = self._rows(results, "FAIL")
        self.assertEqual(len(failures), 1)
        self.assertIn("scripts/reaper.py", failures[0]["detail"])

    def test_unreadable_policy_reports_fail(self) -> None:
        runtime = self._build_runtime({})

        results = coupling.run_checks(
            policy_path=self.sandbox / "does_not_exist.json",
            anti_drift_path=self._write_anti_drift(),
            root=runtime,
        )

        failures = self._rows(results, "FAIL")
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["label"], "claude_root_hygiene_policy.json")
        self.assertEqual(failures[0]["detail"], "missing")

    def test_missing_runtime_root_warns_instead_of_crashing(self) -> None:
        results = self._run([make_pattern_rule()], self.sandbox / "no-such-home")

        self.assertEqual(self._rows(results, "FAIL"), [])
        warnings = self._rows(results, "WARN")
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["label"], "runtime_coupling.root")

    def test_rule_without_patterns_or_roots_warns(self) -> None:
        runtime = self._build_runtime({"scripts/clean.py": "print('x')\n"})
        rule = {"name": "empty-rule", "scanPaths": ["scripts"]}

        results = self._run([rule], runtime)

        self.assertEqual(self._rows(results, "FAIL"), [])
        warnings = self._rows(results, "WARN")
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["label"], "runtime_coupling.empty-rule")

    def test_summarize_sets_nonzero_exit_for_failures(self) -> None:
        counts, exit_code = coupling.summarize(
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
