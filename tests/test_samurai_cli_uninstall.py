"""Tests for bin/samurai's backup/restore behaviour on uninstall.

`_register_hooks_in_file()` names each settings backup `f"{settings_path.name}.bak.{ts}"`.
`~/.samurai/settings.json` and `~/.claude/hooks/settings.json` share the identical basename
`settings.json`, so their backups land in the same `backups/` directory under an
indistinguishable pattern. `cmd_uninstall()` then does `sorted(backups_dir.glob("settings.json.bak.*"))[-1]`
and restores that onto `claude_settings` regardless of which original file it actually came
from -- if only `samurai_settings` was ever backed up, uninstall copies that unrelated
content straight over the user's real `claude_settings`.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

_SAMURAI_PATH = Path(__file__).resolve().parents[1] / "bin" / "samurai"
_loader = SourceFileLoader("samurai_cli_uninstall", str(_SAMURAI_PATH))
_spec = importlib.util.spec_from_loader("samurai_cli_uninstall", _loader)
assert _spec
samurai_cli = importlib.util.module_from_spec(_spec)
sys.modules["samurai_cli_uninstall"] = samurai_cli
_loader.exec_module(samurai_cli)


def _fake_paths(tmp_path: Path) -> dict:
    samurai_home = tmp_path / ".samurai"
    backups_dir = samurai_home / "backups"
    backups_dir.mkdir(parents=True)
    claude_settings = tmp_path / ".claude" / "settings.json"
    claude_settings.parent.mkdir(parents=True)
    return {
        "root": tmp_path / "order-samurai",
        "home": samurai_home,
        "samurai_settings": samurai_home / "settings.json",
        "claude_hooks": tmp_path / ".claude" / "hooks",
        "claude_settings": claude_settings,
        "backups": backups_dir,
        "state": samurai_home / "state",
        "taxonomy": samurai_home / "state" / "kill_chain_taxonomy.json",
    }


def test_uninstall_does_not_restore_an_unrelated_settings_backup_onto_claude_settings(tmp_path, monkeypatch):
    paths = _fake_paths(tmp_path)
    monkeypatch.setattr(samurai_cli, "get_paths", lambda: paths)

    # samurai_settings existed with unrelated content and got backed up during an earlier
    # install -- this is the ONLY backup that exists.
    paths["samurai_settings"].write_text(json.dumps({"marker": "samurai-content"}), encoding="utf-8")
    samurai_cli._register_hooks_in_file(
        paths["samurai_settings"], "/g.py", "/s.py", paths["backups"], "samurai_settings"
    )

    # claude_settings was populated independently afterwards (e.g. a real Claude Code
    # install unrelated to samurai) and was never itself backed up.
    paths["claude_settings"].write_text(json.dumps({"marker": "claude-content"}), encoding="utf-8")

    samurai_cli.cmd_uninstall(argparse.Namespace(keep_data=True))

    restored = json.loads(paths["claude_settings"].read_text(encoding="utf-8"))
    assert restored.get("marker") == "claude-content", (
        f"claude_settings was overwritten by an unrelated samurai_settings backup: {restored}"
    )
