"""Acceptance regressions for uninstall ownership and interrupted removal."""

import argparse
import copy
import importlib.util
import os
import shlex
import shutil
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

from test_codex_hook_wiring import Machine, read_json, user_group, write_json


@pytest.fixture
def machine_factory(tmp_path):
    def make(name):
        base = tmp_path / name
        base.mkdir()
        machine = Machine(base)
        machine.state.mkdir()
        root = machine.state / "core"
        shutil.move(str(machine.root), root)
        machine.root = root
        machine.env["SAMURAI_ROOT"] = str(root)
        machine.env["PYTHONNOUSERSITE"] = "1"
        return machine

    return make


def install(machine, harness):
    config = (machine.hooks if harness == "codex"
              else machine.home / ".claude" / "settings.json")
    write_json(config, {"hooks": {"PreToolUse": [user_group()]}})
    result = machine.run("install", "--harness", harness)
    label = "Codex" if harness == "codex" else "Claude Code"
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{label}: installed" in result.stdout
    entry = read_json(machine.manifest)["harnesses"][harness]
    assert Path(entry["config_path"]) == config
    pre = next(r for r in entry["groups"] if r["event"] == "PreToolUse")
    groups = read_json(config)["hooks"]["PreToolUse"]
    assert pre["index"] == len(groups) - 1
    assert groups[-1] == pre["definition"]
    assert shlex.split(groups[-1]["hooks"][0]["command"])[-1] == str(
        machine.root / "bin/prompt_injection_guard.py"
    )
    assert machine.root == machine.state / "core"
    assert (machine.root / "bin/prompt_injection_guard.py").is_file()
    return config, entry


def assert_edited_duplicate_refused(machine, harness, force):
    config, _ = install(machine, harness)
    data = read_json(config)
    duplicate = copy.deepcopy(data["hooks"]["PreToolUse"][-1])
    duplicate["hooks"][0]["timeout"] = 11
    data["hooks"]["PreToolUse"].append(duplicate)
    write_json(config, data)
    before = config.read_bytes()

    result = machine.run("uninstall", *(["--force"] if force else []))

    label = "Codex" if harness == "codex" else "Claude Code"
    assert result.returncode != 0, result.stdout + result.stderr
    assert f"{label}: FAILED" in result.stdout
    assert "refus" in result.stdout.lower()
    assert config.read_bytes() == before
    assert (machine.root / "bin/prompt_injection_guard.py").is_file()
    assert machine.manifest.is_file()


def test_codex_uninstall_refuses_edited_duplicate_with_and_without_force(machine_factory):
    """Cases 4/7: both uninstall modes refuse an edited duplicate and preserve scripts."""
    for force in (False, True):
        machine = machine_factory("codex-force" if force else "codex-default")
        assert_edited_duplicate_refused(machine, "codex", force)


def test_claude_force_uninstall_refuses_edited_duplicate(machine_factory):
    """Cases 4/7: forced Claude uninstall preserves config and scripts with an edited copy."""
    assert_edited_duplicate_refused(machine_factory("claude-force"), "claude", True)


def test_interrupted_force_uninstall_does_not_forget_remaining_claude_guard(
    machine_factory, monkeypatch, capsys,
):
    """Cases 4/7/13: recovery must not report success while the Claude guard remains."""
    machine = machine_factory("claude-interrupted")
    config, entry = install(machine, "claude")
    data = read_json(config)
    post = next(r for r in entry["groups"] if r["event"] == "PostToolUse")
    pre = next(r for r in entry["groups"] if r["event"] == "PreToolUse")
    assert data["hooks"]["PostToolUse"].pop(post["index"]) == post["definition"]
    write_json(config, data)
    before = config.read_bytes()

    for key in list(os.environ):
        monkeypatch.delenv(key, raising=False)
    for key, value in machine.env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(machine.home)
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    loader = SourceFileLoader("samurai_review_regressions_3", str(machine.root / "bin/samurai"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    cli = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, loader.name, cli)
    loader.exec_module(cli)
    checkpoints = []

    def interrupt(step, harness=None, path=None):
        if step == "manifest_removing" and harness == "claude":
            assert path == config
            checkpoints.append(step)
            raise KeyboardInterrupt("interrupted after manifest_removing")

    monkeypatch.setattr(cli, "_checkpoint", interrupt)
    with pytest.raises(KeyboardInterrupt, match="interrupted after manifest_removing"):
        cli.cmd_uninstall(argparse.Namespace(keep_data=False, force=True, harness=None))
    assert checkpoints == ["manifest_removing"]
    assert read_json(machine.manifest)["harnesses"]["claude"]["state"] == "removing"
    assert config.read_bytes() == before
    assert (machine.root / "bin/prompt_injection_guard.py").is_file()
    capsys.readouterr()

    monkeypatch.setattr(cli, "_checkpoint", lambda *args, **kwargs: None)
    result = cli.cmd_uninstall(argparse.Namespace(keep_data=False, force=False, harness=None))
    output = capsys.readouterr().out
    hooks = read_json(config)["hooks"]
    remaining = [
        handler["command"]
        for groups in hooks.values()
        for group in groups
        for handler in group.get("hooks", [])
        if str(machine.root / "bin") in handler.get("command", "")
    ]
    assert result != 0 or not remaining, (
        "uninstall reported success while Samurai scripts remain",
        remaining, output, f"Samurai state exists: {machine.state.exists()}",
    )
    if result == 0:
        assert pre["definition"] not in hooks.get("PreToolUse", [])
        assert "Claude Code: removed" in output
    else:
        assert "Claude Code: FAILED" in output
        assert (machine.root / "bin/prompt_injection_guard.py").is_file()
        assert machine.manifest.is_file()
