from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from execution import verify_claude_runtime_portability as vp  # type: ignore[attr-defined]


class VerifyClaudeRuntimePortabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = REPO_ROOT / ".tmp" / "test_verify_claude_runtime_portability" / self._testMethodName
        if self.sandbox.exists():
            shutil.rmtree(self.sandbox)
        self.sandbox.mkdir(parents=True, exist_ok=True)

    def _write(self, rel: str, obj: object) -> None:
        path = self.sandbox / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj), encoding="utf-8")

    def _labels(self, results: list[dict]) -> dict:
        return {r["label"]: r for r in results}

    def test_portable_runtime_all_ok(self) -> None:
        self._write("settings.json", {"hooks": {"PreToolUse": [
            {"hooks": [{"command": "python3 ~/.claude/scripts/hook_dispatch.py"}]}]}})
        self._write("mcp.json", {"mcpServers": {
            "svc": {"command": "python3", "args": ["~/.claude/scripts/launch_mcp_server.py", "svc"]}}})
        results = vp.run_checks(runtime_root_dir=self.sandbox)
        statuses = {r["status"] for r in results}
        self.assertEqual(statuses, {"OK"})

    def test_pinned_home_command_fails(self) -> None:
        self._write("settings.json", {"hooks": {"PreToolUse": [
            {"hooks": [{"command": "python C:\\\\Users\\\\exampleuser\\\\.claude\\\\scripts\\\\x.py"}]}]}})
        self._write("mcp.json", {"mcpServers": {}})
        results = vp.run_checks(runtime_root_dir=self.sandbox)
        pinned = self._labels(results)["runtime_portability.pinned-home-commands"]
        self.assertEqual(pinned["status"], "FAIL")

    def test_bash_only_hook_warns(self) -> None:
        self._write("settings.json", {"hooks": {"Stop": [
            {"hooks": [{"command": "bash /some/script.sh"}]}]}})
        self._write("mcp.json", {"mcpServers": {}})
        results = vp.run_checks(runtime_root_dir=self.sandbox)
        row = self._labels(results)["runtime_portability.bash-only-hooks"]
        self.assertEqual(row["status"], "WARN")

    def test_launcher_bypass_warns(self) -> None:
        self._write("settings.json", {"hooks": {}})
        self._write("mcp.json", {"mcpServers": {
            "rogue": {"command": "/usr/local/bin/some-server", "args": ["--stdio"]}}})
        results = vp.run_checks(runtime_root_dir=self.sandbox)
        row = self._labels(results)["runtime_portability.launcher-bypass"]
        self.assertEqual(row["status"], "WARN")
        self.assertIn("rogue", row["detail"])

    def test_disabled_bypass_server_not_flagged(self) -> None:
        self._write("settings.json", {"hooks": {}})
        self._write("mcp.json", {"mcpServers": {
            "off": {"command": "/usr/local/bin/x", "disabled": True}}})
        results = vp.run_checks(runtime_root_dir=self.sandbox)
        row = self._labels(results)["runtime_portability.launcher-bypass"]
        self.assertEqual(row["status"], "OK")

    def test_missing_runtime_root_warns(self) -> None:
        results = vp.run_checks(runtime_root_dir=self.sandbox / "absent")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "WARN")

    def test_summarize_exit_contract(self) -> None:
        _counts, code = vp.summarize([vp._make_result("FAIL", "a", "d")])
        self.assertEqual(code, 1)
        _counts, code = vp.summarize([vp._make_result("WARN", "a", "d")])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
