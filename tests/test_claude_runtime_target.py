from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from execution import claude_runtime_target as target  # type: ignore[attr-defined]


class ClaudeRuntimeTargetTests(unittest.TestCase):
    def test_all_policy_paths_exist(self) -> None:
        for path in target.ALL_POLICY_PATHS:
            with self.subTest(policy=path.name):
                self.assertTrue(path.exists(), f"missing pack policy: {path}")

    def test_report_and_backlog_exist(self) -> None:
        self.assertTrue(target.REPORT_PATH.exists())
        self.assertTrue(target.BACKLOG_PATH.exists())

    def test_runtime_root_defaults_to_home_claude(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_RUNTIME_ROOT", None)
            self.assertEqual(target.runtime_root(), Path.home() / ".claude")

    def test_runtime_root_env_override(self) -> None:
        with mock.patch.dict(os.environ, {"CLAUDE_RUNTIME_ROOT": "/tmp/sandbox-claude"}):
            self.assertEqual(target.runtime_root(), Path("/tmp/sandbox-claude"))


if __name__ == "__main__":
    unittest.main()
