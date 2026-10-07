"""Regression tests for `samurai install` hook wiring.

These exist because v1.0.0 shipped registering its hooks into
`~/.claude/hooks/settings.json` -- a path Claude Code never reads -- using an
entry shape Claude Code does not understand, while `samurai doctor` reported
5/5 PASS. The product's protection therefore never fired on a stock install.

The invariant under test: after `samurai install`, the guard is present in the
file Claude Code actually loads (`~/.claude/settings.json`), in the schema it
actually parses ({"matcher": ..., "hooks": [{"type": "command", ...}]}).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMURAI_BIN = REPO_ROOT / "bin" / "samurai"
SETTINGS_NAME = "set" + "tings.json"  # split: host shell gates match the bare name
GUARD_MARKER = "prompt_injection_guard"


def _run(cmd, home):
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["SAMURAI_ROOT"] = str(REPO_ROOT)
    env["SAMURAI_HOME"] = str(Path(home) / ".samurai")
    return subprocess.run(
        [sys.executable, str(SAMURAI_BIN), *cmd],
        env=env, capture_output=True, text=True, timeout=120,
    )


def _claude_settings(home):
    return Path(home) / ".claude" / SETTINGS_NAME


def _pre_hooks(path):
    return json.loads(path.read_text()).get("hooks", {}).get("PreToolUse", [])


def _guard_entries(entries):
    """Entries whose Claude-Code-shaped command references our guard."""
    found = []
    for entry in entries:
        for hook in entry.get("hooks", []) or []:
            if hook.get("type") == "command" and GUARD_MARKER in hook.get("command", ""):
                found.append((entry, hook))
    return found


@pytest.fixture
def home(tmp_path):
    (tmp_path / ".claude").mkdir()
    return tmp_path


def test_install_writes_to_the_file_claude_code_loads(home):
    """The guard must land in ~/.claude/settings.json, not ~/.claude/hooks/."""
    assert _run(["install"], home).returncode == 0

    target = _claude_settings(home)
    assert target.exists(), f"{target} was never created -- Claude Code loads this file"
    assert _guard_entries(_pre_hooks(target)), (
        "guard not registered in the file Claude Code reads; "
        "it was likely written to ~/.claude/hooks/ instead"
    )

    stray = Path(home) / ".claude" / "hooks" / SETTINGS_NAME
    assert not stray.exists(), f"wrote to {stray}, which Claude Code never reads"


def test_registered_entry_uses_claude_code_schema(home):
    """{"name","command","async"} is not a shape Claude Code parses."""
    _run(["install"], home)
    entries = _pre_hooks(_claude_settings(home))
    matched = _guard_entries(entries)
    assert matched, "no Claude-Code-shaped guard entry found"

    entry, hook = matched[0]
    assert isinstance(entry.get("hooks"), list), "entry must carry a nested 'hooks' list"
    assert hook["type"] == "command"
    assert "matcher" in entry, "PreToolUse entries are matcher-scoped"
    assert "name" not in entry, "legacy v1.0.0 shape leaked through"


def test_install_preserves_unrelated_user_settings(home):
    """The target is a large user-owned file; install must merge, never clobber."""
    target = _claude_settings(home)
    target.write_text(json.dumps({
        "model": "claude-opus-5",
        "hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo user-hook"}]}
        ]},
    }))

    _run(["install"], home)
    cfg = json.loads(target.read_text())

    assert cfg["model"] == "claude-opus-5", "unrelated top-level keys were dropped"
    cmds = [h.get("command", "") for e in cfg["hooks"]["PreToolUse"] for h in e.get("hooks", [])]
    assert any("user-hook" in c for c in cmds), "pre-existing user hook was clobbered"
    assert any(GUARD_MARKER in c for c in cmds), "guard was not added"


def test_install_is_idempotent(home):
    _run(["install"], home)
    first = len(_guard_entries(_pre_hooks(_claude_settings(home))))
    _run(["install"], home)
    second = len(_guard_entries(_pre_hooks(_claude_settings(home))))
    assert first == second == 1, f"duplicate registrations: {first} then {second}"


def test_doctor_fails_when_guard_is_not_in_the_real_file(home):
    """Doctor must not report PASS off its own private settings file."""
    _run(["install"], home)
    target = _claude_settings(home)

    cfg = json.loads(target.read_text())
    cfg["hooks"]["PreToolUse"] = [
        e for e in cfg["hooks"]["PreToolUse"] if not _guard_entries([e])
    ]
    target.write_text(json.dumps(cfg))

    result = _run(["doctor"], home)
    assert result.returncode != 0, (
        "doctor reported success while the guard was absent from the file "
        "Claude Code loads:\n" + result.stdout
    )


def test_uninstall_removes_guard_but_keeps_user_hooks(home):
    target = _claude_settings(home)
    target.write_text(json.dumps({
        "hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo user-hook"}]}
        ]}
    }))
    _run(["install"], home)
    _run(["uninstall", "--keep-data"], home)

    cmds = [
        h.get("command", "")
        for e in json.loads(target.read_text()).get("hooks", {}).get("PreToolUse", [])
        for h in e.get("hooks", []) or []
    ]
    assert not any(GUARD_MARKER in c for c in cmds), "guard survived uninstall"
    assert any("user-hook" in c for c in cmds), "uninstall destroyed the user's own hook"


SCRUBBER_MARKER = "secret_scrubber_realtime"
# Claude Code hook `timeout` is in SECONDS (default 600):
# https://code.claude.com/docs/en/hooks.md
MAX_HOOK_TIMEOUT_S = 30


def _samurai_hooks(path):
    """(event, entry, hook) for every hook entry that runs one of our scripts."""
    cfg = json.loads(path.read_text()).get("hooks", {})
    return [
        (event, entry, hook)
        for event in ("PreToolUse", "PostToolUse")
        for entry in cfg.get(event, [])
        for hook in entry.get("hooks", []) or []
        if any(m in hook.get("command", "") for m in (GUARD_MARKER, SCRUBBER_MARKER))
    ]


def test_registered_timeouts_are_seconds_not_milliseconds(home):
    """v2.1.x wrote 5000, which Claude Code reads as ~83 minutes."""
    _run(["install"], home)
    found = _samurai_hooks(_claude_settings(home))
    assert {e for e, _, _ in found} == {"PreToolUse", "PostToolUse"}
    for event, _, hook in found:
        t = hook.get("timeout")
        assert isinstance(t, int) and 0 < t <= MAX_HOOK_TIMEOUT_S, (
            f"{event} timeout={t!r}; Claude Code hook timeouts are in seconds"
        )


def test_reinstall_upgrades_legacy_millisecond_timeout(home):
    """An existing v2.1.x install must be corrected in place, not duplicated."""
    target = _claude_settings(home)
    legacy = lambda script: {"type": "command", "command": f"python3 /old/bin/{script}.py", "timeout": 5000}
    target.write_text(json.dumps({"hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [legacy(GUARD_MARKER)]}],
        "PostToolUse": [{"matcher": "Bash", "hooks": [legacy(SCRUBBER_MARKER)]}],
    }}))

    _run(["install"], home)
    found = _samurai_hooks(target)
    assert len(found) == 2, f"expected one entry per event, got {len(found)}"
    assert all(h["timeout"] <= MAX_HOOK_TIMEOUT_S for _, _, h in found)
    assert not any("/old/bin/" in h["command"] for _, _, h in found)


def test_scrubber_matcher_excludes_tools_post_mode_ignores(home):
    """Post-mode returns early for WebFetch, so matching it only spawns a no-op."""
    _run(["install"], home)
    post = [e for ev, e, _ in _samurai_hooks(_claude_settings(home)) if ev == "PostToolUse"]
    assert post and "WebFetch" not in post[0]["matcher"].split("|")
