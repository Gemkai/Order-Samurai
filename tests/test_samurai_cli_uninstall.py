"""Tests for bin/samurai's backup/restore behaviour on uninstall.

`_register_hooks_in_file()` names each settings backup `f"{settings_path.name}.bak.{ts}"`.
`~/.samurai/settings.json` and `~/.claude/settings.json` share the identical basename
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

import pytest
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
    claude_hooks = tmp_path / ".claude" / "hooks"
    claude_hooks.mkdir(parents=True)
    return {
        "root": tmp_path / "order-samurai",
        "home": samurai_home,
        "samurai_settings": samurai_home / "settings.json",
        "claude_hooks": claude_hooks,
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


def test_uninstall_keep_data_deregisters_hooks_from_samurai_settings_too(tmp_path, monkeypatch):
    """cmd_install() registers hooks into BOTH samurai_settings (the "agent-agnostic
    primary") and claude_settings. cmd_uninstall() only ever deregistered
    claude_settings -- with --keep-data (so ~/.samurai/settings.json survives instead
    of being rmtree'd), the hooks stayed fully wired in samurai_settings while the CLI
    printed "uninstalled cleanly."."""
    paths = _fake_paths(tmp_path)
    monkeypatch.setattr(samurai_cli, "get_paths", lambda: paths)

    guard_script = str(paths["root"] / "hooks" / "prompt_injection_guard.py")
    scrubber_script = str(paths["root"] / "hooks" / "secret_scrubber_realtime.py")
    samurai_cli._register_hooks_in_file(
        paths["samurai_settings"], guard_script, scrubber_script, paths["backups"], "samurai_settings"
    )

    samurai_cli.cmd_uninstall(argparse.Namespace(keep_data=True))

    cfg = json.loads(paths["samurai_settings"].read_text(encoding="utf-8"))
    guard_command = f"python3 {guard_script}"
    pre = cfg.get("hooks", {}).get("PreToolUse", [])
    still_registered = any(
        isinstance(m, dict) and isinstance(m.get("hooks"), list)
        and any(isinstance(h, dict) and h.get("command") == guard_command for h in m["hooks"])
        for m in pre
    )
    assert not still_registered, "samurai uninstall --keep-data left hooks registered in samurai_settings"


def test_uninstall_preserves_unrelated_legacy_hook_file_content(tmp_path, monkeypatch):
    """The v1.0.0 file may contain user hooks too; uninstall removes only Samurai rows."""
    paths = _fake_paths(tmp_path)
    monkeypatch.setattr(samurai_cli, "get_paths", lambda: paths)

    legacy = paths["claude_hooks"] / "settings.json"
    legacy.write_text(json.dumps({
        "user_setting": "keep-me",
        "hooks": {
            "PreToolUse": [
                {"name": "samurai_prompt_injection_guard", "command": "python3 /samurai/prompt_injection_guard.py"},
                {"name": "user_pre", "command": "echo user-pre"},
            ],
            "PostToolUse": [
                {"name": "samurai_secret_scrubber", "command": "python3 /samurai/secret_scrubber_realtime.py"},
                {"name": "user_post", "command": "echo user-post"},
            ],
        },
    }), encoding="utf-8")

    samurai_cli.cmd_uninstall(argparse.Namespace(keep_data=True))

    assert legacy.exists(), "uninstall deleted a legacy settings file containing user configuration"
    cfg = json.loads(legacy.read_text(encoding="utf-8"))
    assert cfg["user_setting"] == "keep-me"
    assert cfg["hooks"]["PreToolUse"] == [{"name": "user_pre", "command": "echo user-pre"}]
    assert cfg["hooks"]["PostToolUse"] == [{"name": "user_post", "command": "echo user-post"}]


@pytest.mark.parametrize("target", ["claude_settings", "samurai_settings", "legacy_settings"])
def test_uninstall_preserves_malformed_settings_bytes(tmp_path, monkeypatch, target):
    """Unreadable user configuration must never be replaced with an empty object or deleted."""
    paths = _fake_paths(tmp_path)
    monkeypatch.setattr(samurai_cli, "get_paths", lambda: paths)

    settings_path = (
        paths["claude_hooks"] / "settings.json"
        if target == "legacy_settings"
        else paths[target]
    )
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    original = b'{"hooks": {"PreToolUse": [invalid-user-json]\n'
    settings_path.write_bytes(original)

    samurai_cli.cmd_uninstall(argparse.Namespace(keep_data=True))

    assert settings_path.exists(), f"uninstall deleted malformed {target} configuration"
    assert settings_path.read_bytes() == original, (
        f"uninstall rewrote malformed {target} configuration instead of leaving it for recovery"
    )
