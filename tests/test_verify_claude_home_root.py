"""Tests for the homeRoot ($HOME clutter) check in verify_claude_root_hygiene.

Added 2026-08-02: the ~/.claude hygiene policies were blind to sprawl landing in
the home root itself, so the policy grew an opt-in homeRoot.allowlist section.
"""

from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from execution.verify_claude_root_hygiene import (  # type: ignore[attr-defined]
    find_unlisted_home_entries,
    run_checks,
)


def make_policy(**overrides) -> dict:
    payload = {
        "version": 1,
        "directories": {"runtime": ["commands"], "state": ["cache"]},
        "files": {"generated_truth": ["settings.json"]},
        "requiredDirectories": ["commands"],
        "requiredFiles": ["settings.json"],
        "boundaryRules": [],
    }
    payload.update(overrides)
    return payload


class HomeRootCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = (
            REPO_ROOT / ".tmp" / "test_verify_claude_home_root" / self._testMethodName
        )
        if self.sandbox.exists():
            shutil.rmtree(self.sandbox)
        self.root = self.sandbox / "claude-home"
        self.home = self.sandbox / "user-home"
        self.root.mkdir(parents=True)
        self.home.mkdir(parents=True)
        (self.root / "commands").mkdir()
        (self.root / "cache").mkdir()
        (self.root / "settings.json").write_text("{}", encoding="utf-8")

    def _write_policy(self, payload: dict) -> Path:
        policy_path = self.sandbox / "policy.json"
        policy_path.write_text(json.dumps(payload), encoding="utf-8")
        return policy_path

    def _home_results(self, payload: dict) -> list[dict[str, str]]:
        results = run_checks(
            policy_path=self._write_policy(payload), root=self.root, home=self.home
        )
        return [r for r in results if r["name"] == "root_hygiene.claude.home_root"]

    def test_no_home_root_section_skips_the_check(self) -> None:
        (self.home / "stray-dir").mkdir()
        self.assertEqual(self._home_results(make_policy()), [])

    def test_allowlisted_entries_pass(self) -> None:
        (self.home / "Projects").mkdir()
        (self.home / "Brewfile").write_text("", encoding="utf-8")
        results = self._home_results(
            make_policy(homeRoot={"allowlist": ["Projects", "Brewfile"]})
        )
        self.assertEqual([r["status"] for r in results], ["OK"])

    def test_unlisted_visible_entry_warns_with_kind(self) -> None:
        (self.home / "Projects").mkdir()
        (self.home / "stray-dir").mkdir()
        (self.home / "stray-file.txt").write_text("", encoding="utf-8")
        results = self._home_results(make_policy(homeRoot={"allowlist": ["Projects"]}))
        self.assertEqual([r["status"] for r in results], ["WARN", "WARN"])
        details = [r["detail"] for r in results]
        self.assertIn("visible top-level directory not in homeRoot.allowlist: stray-dir", details)
        self.assertIn("visible top-level file not in homeRoot.allowlist: stray-file.txt", details)

    def test_hidden_entries_are_always_exempt(self) -> None:
        (self.home / ".config").mkdir()
        (self.home / ".zshrc").write_text("", encoding="utf-8")
        results = self._home_results(make_policy(homeRoot={"allowlist": []}))
        self.assertEqual([r["status"] for r in results], ["OK"])

    def test_warns_never_fail_the_run(self) -> None:
        (self.home / "stray-dir").mkdir()
        results = run_checks(
            policy_path=self._write_policy(make_policy(homeRoot={"allowlist": []})),
            root=self.root,
            home=self.home,
        )
        self.assertTrue(all(r["status"] != "FAIL" for r in results))

    def test_find_unlisted_home_entries_sorted_case_insensitive(self) -> None:
        for name in ("beta", "Alpha", "gamma"):
            (self.home / name).mkdir()
        self.assertEqual(
            find_unlisted_home_entries(
                payload=make_policy(homeRoot={"allowlist": ["gamma"]}),
                home=self.home,
            ),
            ["Alpha", "beta"],
        )


if __name__ == "__main__":
    unittest.main()
