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


from execution import verify_claude_mcp_contract as vmc  # type: ignore[attr-defined]


# A launcher-backed server config in the portable form the live mcp.json uses.
def _launcher_server(name: str, *, disabled: bool = False, env: dict | None = None) -> dict:
    cfg: dict = {
        "command": "python",
        "args": [
            "-u",
            "-c",
            "import pathlib,runpy,sys;"
            "script=pathlib.Path.home()/'.claude'/'scripts/launch_mcp_server.py';"
            "sys.argv=[str(script),*sys.argv[1:]];"
            "runpy.run_path(str(script),run_name='__main__')",
            name,
        ],
        "env": env or {},
        "disabled": disabled,
    }
    return cfg


class VerifyClaudeMcpContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = (
            REPO_ROOT / ".tmp" / "test_verify_claude_mcp_contract" / self._testMethodName
        )
        if self.sandbox.exists():
            shutil.rmtree(self.sandbox)
        (self.sandbox / "scripts").mkdir(parents=True, exist_ok=True)
        self._saved_env = dict(os.environ)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._saved_env)

    def _write_scripts(self) -> None:
        for rel in vmc.REQUIRED_LAUNCHER_SCRIPTS:
            path = self.sandbox / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# generator\n", encoding="utf-8")

    def _write_mcp(self, servers: dict, extra: dict | None = None) -> None:
        doc: dict = {"mcpServers": servers}
        if extra:
            doc.update(extra)
        (self.sandbox / "mcp.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")

    def _by_label(self, results: list[dict[str, str]]) -> dict[str, dict[str, str]]:
        return {result["label"]: result for result in results}

    def test_happy_path_launcher_backed_config_is_all_ok(self) -> None:
        self._write_scripts()
        self._write_mcp(
            {
                "mcp-deep-think": _launcher_server("mcp-deep-think"),
                "notebooklm": _launcher_server("notebooklm"),
            }
        )

        results = vmc.run_checks(runtime_root_dir=self.sandbox)

        statuses = {result["status"] for result in results}
        self.assertEqual(statuses, {"OK"})

    def test_literal_absolute_home_path_in_server_entry_fails(self) -> None:
        self._write_scripts()
        drifted = {
            "command": "python",
            "args": ["~/.claude/scripts/launch_mcp_server.py", "drifted"],
            "env": {},
            "disabled": False,
        }
        self._write_mcp({"drifted": drifted})

        results = vmc.run_checks(runtime_root_dir=self.sandbox)

        row = self._by_label(results)["claude-mcp-contract.launcher-backed"]
        self.assertEqual(row["status"], "FAIL")
        self.assertIn("drifted", row["detail"])

    def test_windows_doubled_backslash_home_path_in_server_entry_fails(self) -> None:
        self._write_scripts()
        # Embed the Windows form literally (json.dumps will re-escape it).
        drifted = {
            "command": r"~/.claude\scripts\launch_mcp_server.py",
            "args": ["drifted"],
            "env": {},
            "disabled": False,
        }
        self._write_mcp({"drifted": drifted})

        results = vmc.run_checks(runtime_root_dir=self.sandbox)

        row = self._by_label(results)["claude-mcp-contract.launcher-backed"]
        self.assertEqual(row["status"], "FAIL")
        self.assertIn(r"~/.claude", row["detail"])

    def test_disabled_server_with_unset_activation_env_is_reported_ok(self) -> None:
        self._write_scripts()
        self._write_mcp(
            {
                "mcp-deep-think": _launcher_server("mcp-deep-think"),
                "postgres": _launcher_server(
                    "postgres",
                    disabled=True,
                    env={"POSTGRES_URL": "${POSTGRES_URL}"},
                ),
            }
        )
        # Ensure the activation env is genuinely unset.
        os.environ.pop("POSTGRES_URL", None)

        results = vmc.run_checks(runtime_root_dir=self.sandbox)

        statuses = {result["status"] for result in results}
        self.assertNotIn("FAIL", statuses)
        self.assertNotIn("WARN", statuses)
        gating = self._by_label(results)["claude-mcp-contract.activation-gating"]
        self.assertEqual(gating["status"], "OK")
        self.assertIn("postgres", gating["detail"])

    def test_enabled_server_not_launcher_backed_warns(self) -> None:
        self._write_scripts()
        broken = {
            "command": "some-ad-hoc-binary",
            "args": ["--serve"],
            "env": {},
            "disabled": False,
        }
        self._write_mcp(
            {
                "mcp-deep-think": _launcher_server("mcp-deep-think"),
                "broken": broken,
            }
        )

        results = vmc.run_checks(runtime_root_dir=self.sandbox)

        row = self._by_label(results)["claude-mcp-contract.enabled-servers"]
        self.assertEqual(row["status"], "WARN")
        self.assertIn("broken", row["detail"])

    def test_missing_runtime_root_warns_and_does_not_crash(self) -> None:
        absent = self.sandbox / "does-not-exist"

        results = vmc.run_checks(runtime_root_dir=absent)

        self.assertEqual(results[-1]["status"], "WARN")
        self.assertEqual(results[-1]["label"], "claude-mcp-contract.runtime-root")

    def test_missing_launcher_scripts_warn_not_fail(self) -> None:
        # scripts/ dir exists (from setUp) but no generator files.
        self._write_mcp({"mcp-deep-think": _launcher_server("mcp-deep-think")})

        results = vmc.run_checks(runtime_root_dir=self.sandbox)

        row = self._by_label(results)["claude-mcp-contract.launcher-scripts"]
        self.assertEqual(row["status"], "WARN")

    def test_malformed_mcp_json_fails(self) -> None:
        self._write_scripts()
        (self.sandbox / "mcp.json").write_text("{ not json", encoding="utf-8")

        results = vmc.run_checks(runtime_root_dir=self.sandbox)

        row = self._by_label(results)["claude-mcp-contract.mcp-json"]
        self.assertEqual(row["status"], "FAIL")

    def test_no_activation_metadata_emits_honor_system_ok(self) -> None:
        self._write_scripts()
        # Servers carry neither a disabled flag nor env placeholders.
        bare = {
            "command": "python",
            "args": [
                "-c",
                "import runpy,pathlib;"
                "runpy.run_path(str(pathlib.Path.home()/'.claude'/'scripts/launch_mcp_server.py'))",
                "bare",
            ],
        }
        self._write_mcp({"bare": bare})

        results = vmc.run_checks(runtime_root_dir=self.sandbox)

        row = self._by_label(results)["claude-mcp-contract.activation-gating"]
        self.assertEqual(row["status"], "OK")
        self.assertIn("honor-system", row["detail"])

    def test_summarize_sets_nonzero_exit_for_failures(self) -> None:
        counts, exit_code = vmc.summarize(
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
