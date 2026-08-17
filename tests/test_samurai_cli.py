"""Tests for bin/samurai (the standalone install/uninstall/doctor CLI).

Claude Code's real `settings.json` hooks shape is `hooks.<Event> = [matcher, ...]` where each
matcher is `{"matcher": ..., "hooks": [{"type": "command", "command": str}]}` -- confirmed by
`execution/verify_claude_hook_contract.py::collect_hook_commands()` and the fixtures in
`tests/test_verify_claude_runtime_portability.py`. `_register_hooks_in_file()` used to write a
flat `{"name": ..., "command": ..., "async": ...}` object directly into the matcher list instead,
which a real hook-contract parser silently ignores (`matcher.get("hooks")` is `None`) -- the
installed security hooks would never actually run.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

_SAMURAI_PATH = Path(__file__).resolve().parents[1] / "bin" / "samurai"
_loader = SourceFileLoader("samurai_cli", str(_SAMURAI_PATH))
_spec = importlib.util.spec_from_loader("samurai_cli", _loader)
assert _spec
samurai_cli = importlib.util.module_from_spec(_spec)
sys.modules["samurai_cli"] = samurai_cli
_loader.exec_module(samurai_cli)


def _collect_hook_commands(settings_payload: dict) -> list[str]:
    """Minimal re-implementation of the real hook-contract parser
    (execution/verify_claude_hook_contract.py::collect_hook_commands): only commands
    reachable via matcher["hooks"][i]["type"] == "command" count as actually wired."""
    commands = []
    for matchers in settings_payload.get("hooks", {}).values():
        if not isinstance(matchers, list):
            continue
        for matcher in matchers:
            if not isinstance(matcher, dict):
                continue
            for entry in matcher.get("hooks") or []:
                if isinstance(entry, dict) and entry.get("type") == "command":
                    commands.append(entry.get("command"))
    return commands


def test_register_hooks_writes_commands_the_real_hook_contract_can_find(tmp_path):
    settings_path = tmp_path / "settings.json"
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()

    samurai_cli._register_claude_hooks(
        settings_path, "/path/to/guard.py", "/path/to/scrubber.py", backups_dir, "test_settings"
    )

    written = json.loads(settings_path.read_text(encoding="utf-8"))
    commands = _collect_hook_commands(written)
    assert "python3 /path/to/guard.py" in commands, (
        f"guard hook not reachable by the real hook-contract parser; wrote: {written['hooks']}"
    )
    assert "python3 /path/to/scrubber.py" in commands


def test_register_hooks_is_idempotent_on_reinstall(tmp_path):
    settings_path = tmp_path / "settings.json"
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()

    samurai_cli._register_claude_hooks(
        settings_path, "/path/to/guard.py", "/path/to/scrubber.py", backups_dir, "test_settings"
    )
    samurai_cli._register_claude_hooks(
        settings_path, "/path/to/guard.py", "/path/to/scrubber.py", backups_dir, "test_settings"
    )

    written = json.loads(settings_path.read_text(encoding="utf-8"))
    commands = _collect_hook_commands(written)
    assert commands.count("python3 /path/to/guard.py") == 1
    assert commands.count("python3 /path/to/scrubber.py") == 1
