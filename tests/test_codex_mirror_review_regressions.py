"""Acceptance regressions for Codex mirror review findings (cases 4, 7 and 9)."""

import argparse
import importlib.util
import os
import sys
from importlib.machinery import SourceFileLoader

import pytest

from test_codex_hook_wiring import Machine, read_json, user_group, write_json
from test_guard_codex_payloads import _payload, run_guard  # noqa: F401


INJ = "jail" + "break"


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\x0b", "\x0c", "\x1c", "\x85"],
                         ids=["line-separator", "paragraph-separator", "vertical-tab",
                              "form-feed", "file-separator", "next-line"])
def test_patch_added_content_after_separator_is_blocked(run_guard, separator):
    """Case 9: scan all added content after a separator that is not a patch newline."""
    command = "\n".join([
        "*** Begin Patch", "*** Add File: notes.txt",
        "+safe" + separator + INJ, "*** End Patch", "",
    ])
    result, elapsed = run_guard(_payload("apply_patch", command))
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert result.stderr.strip(), "blocking must explain its reason on stderr"
    assert elapsed < 5


@pytest.mark.parametrize("prefix", ["-", " "], ids=["removal", "context"])
def test_crlf_patch_existing_content_is_allowed(run_guard, prefix):
    """Case 9: CRLF patches still exclude removed and unchanged context content."""
    command = "\r\n".join([
        "*** Begin Patch", "*** Update File: notes.txt", "@@",
        prefix + INJ, "+safe", "*** End Patch", "",
    ])
    result, elapsed = run_guard(_payload("apply_patch", command))
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert elapsed < 5


@pytest.fixture
def machine(tmp_path):
    return Machine(tmp_path)


def test_force_uninstall_removes_remaining_claude_guard(machine):
    """Cases 4/7: a missing scrubber must not strand the guard or permit false success."""
    installed = machine.run("install", "--harness", "claude")
    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert "Claude Code: installed" in installed.stdout
    entry = read_json(machine.manifest)["harnesses"]["claude"]
    settings = machine.home / ".claude" / "settings.json"
    data = read_json(settings)
    post = next(r for r in entry["groups"] if r["event"] == "PostToolUse")
    pre = next(r for r in entry["groups"] if r["event"] == "PreToolUse")
    assert data["hooks"]["PreToolUse"][pre["index"]] == pre["definition"]
    assert data["hooks"]["PostToolUse"].pop(post["index"]) == post["definition"]
    write_json(settings, data)

    result = machine.run("uninstall", "--force")
    hooks = read_json(settings)["hooks"]
    remaining = [handler["command"] for groups in hooks.values() for group in groups
                 for handler in group.get("hooks", [])
                 if str(machine.root / "bin") in handler.get("command", "")]
    assert result.returncode != 0 or not remaining, (
        "uninstall reported success while Samurai scripts remain", remaining, result.stdout,
    )
    assert pre["definition"] not in hooks.get("PreToolUse", []), (
        "--force left the exact recorded guard installed", result.stdout, result.stderr,
    )


def test_force_uninstall_refuses_edited_codex_guard(machine):
    """Cases 4/7: force refuses an edited guard and retains its config and install state."""
    installed = machine.run("install", "--harness", "codex")
    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert "Codex: installed" in installed.stdout
    entry = read_json(machine.manifest)["harnesses"]["codex"]
    record = entry["groups"][0]
    data = read_json(machine.hooks)
    group = data["hooks"]["PreToolUse"][record["index"]]
    assert group == record["definition"]
    group["hooks"][0]["timeout"] += 1
    write_json(machine.hooks, data)
    before = machine.hooks.read_bytes()
    sentinel = machine.state / "keep-data"
    sentinel.write_text("keep")

    result = machine.run("uninstall", "--force")
    assert result.returncode != 0, result.stdout + result.stderr
    assert "Codex: FAILED" in result.stdout
    assert "refus" in result.stdout.lower()
    assert machine.hooks.read_bytes() == before
    assert machine.state.is_dir() and machine.manifest.is_file()
    assert sentinel.read_text() == "keep"
    assert "codex" in read_json(machine.manifest)["harnesses"]


def test_uninstall_refuses_concurrently_appended_codex_hook(machine, monkeypatch):
    """Case 7: recheck later hooks at removal time and retain both groups and ownership."""
    before = user_group("echo before")
    ours = machine.seed([before])
    later = user_group("echo appended")
    for key in list(os.environ):
        monkeypatch.delenv(key, raising=False)
    for key, value in machine.env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(machine.home)
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    loader = SourceFileLoader("samurai_review_regressions", str(machine.root / "bin/samurai"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    cli = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, loader.name, cli)
    loader.exec_module(cli)
    checkpoints = []

    def append_later_hook(step, harness=None, path=None):
        if step == "manifest_removing" and harness == "codex":
            assert path == machine.hooks
            data = read_json(path)
            assert data["hooks"]["PreToolUse"] == [before, ours]
            data["hooks"]["PreToolUse"].append(later)
            write_json(path, data)
            checkpoints.append(step)

    monkeypatch.setattr(cli, "_checkpoint", append_later_hook)
    result = cli.cmd_uninstall(argparse.Namespace(keep_data=True, force=False, harness=None))
    assert checkpoints == ["manifest_removing"]
    assert read_json(machine.hooks)["hooks"]["PreToolUse"] == [before, ours, later], (
        "uninstall removed our guard and shifted the concurrently appended user's hook",
    )
    assert result != 0
    entry = read_json(machine.manifest)["harnesses"]["codex"]
    assert entry["state"] in ("removing", "installed")
    assert entry["groups"][0]["index"] == 1
    assert entry["groups"][0]["definition"] == ours
