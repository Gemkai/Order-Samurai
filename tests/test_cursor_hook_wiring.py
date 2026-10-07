"""samurai install / doctor / uninstall for Cursor, the third supported harness.

Cursor's hooks.json differs from Claude's and Codex's: a top-level "version": 1 and
flat hook entries ({"command", "failClosed", ...}) listed per event, not matcher
groups. Entries that fail with an exit code other than 0 or 2 let the action through
unless they set failClosed, so Samurai's entries must set it."""

import copy
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
EVENTS = ("beforeShellExecution", "beforeMCPExecution", "preToolUse")


def definitions(root):
    command = "python3 " + shlex.quote(str(root / "bin/prompt_injection_guard.py"))
    entry = {"command": command, "timeout": 10, "failClosed": True}
    return {
        "beforeShellExecution": dict(entry),
        "beforeMCPExecution": dict(entry),
        "preToolUse": dict(entry, matcher="Write"),
    }


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def read_json(path):
    return json.loads(path.read_text())


class Machine:
    def __init__(self, base, cursor_evidence="path"):
        self.home = base / "home"
        self.home.mkdir()
        (self.home / ".claude").mkdir()
        self.root = base / "core with spaces"
        shutil.copytree(ROOT / "bin", self.root / "bin", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "agentica_core", self.root / "agentica_core",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (self.root / "state").mkdir()
        shutil.copy2(ROOT / "state/kill_chain_taxonomy.json", self.root / "state/kill_chain_taxonomy.json")
        self.path = base / "path"
        self.path.mkdir()
        (self.path / "python3").symlink_to(sys.executable)
        self.apps = base / "Applications"
        self.apps.mkdir()
        self.cursor = self.home / ".cursor"
        self.hooks = self.cursor / "hooks.json"
        self.state = self.home / ".samurai"
        self.manifest = self.state / "install.json"
        self.env = {
            "HOME": str(self.home), "PATH": str(self.path),
            "CODEX_HOME": str(self.home / ".codex"),
            "SAMURAI_CODEX_APP_BIN": str(base / "absent-app/codex"),
            "SAMURAI_APPLICATIONS_DIR": str(self.apps),
            "SAMURAI_HOME": str(self.state), "SAMURAI_ROOT": str(self.root),
            "SAMURAI_HARNESS": "cursor", "SAMURAI_NO_PROMPT": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if cursor_evidence == "path":
            self.stub(self.path / "cursor")
        elif cursor_evidence == "app":
            (self.apps / "Cursor.app").mkdir()

    @staticmethod
    def stub(path):
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o755)

    def run(self, *args):
        return subprocess.run([sys.executable, str(self.root / "bin/samurai"), *args],
                              env=self.env, cwd=self.home, capture_output=True,
                              text=True, timeout=20)

    def install(self):
        result = self.run("install")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Cursor: installed" in result.stdout, result.stdout
        return result

    def seed(self, before=()):
        """Recorded state, so doctor/uninstall tests do not depend on install."""
        defs = definitions(self.root)
        data = {"version": 1, "hooks": {event: [*before, defs[event]] for event in EVENTS}}
        write_json(self.hooks, data)
        write_json(self.manifest, {
            "version": 1, "selected": ["cursor"], "selection_source": "flag",
            "roots": [str(self.root)], "harnesses": {"cursor": {
                "state": "installed", "config_path": str(self.hooks),
                "groups": [{"event": e, "index": len(before), "definition": defs[e], "previous": []}
                           for e in EVENTS],
            }},
        })
        return defs


@pytest.fixture
def machine(tmp_path):
    return Machine(tmp_path)


def user_hook(command="echo user-hook"):
    return {"command": command, "timeout": 5}


def line(result, label="Cursor"):
    found = [row for row in result.stdout.splitlines() if row.startswith(label + ":")]
    assert len(found) == 1, result.stdout + result.stderr
    return found[0]


# --- install ------------------------------------------------------------------

def test_install_creates_private_hooks_file_with_version_and_all_events(machine):
    assert not machine.cursor.exists()
    machine.install()
    assert read_json(machine.hooks) == {
        "version": 1, "hooks": {e: [d] for e, d in definitions(machine.root).items()}}
    assert stat.S_IMODE(machine.hooks.stat().st_mode) == 0o600


def test_install_preserves_user_hooks_and_content(machine):
    original = {"version": 1, "custom": {"keep": [1, False]}, "hooks": {
        "beforeShellExecution": [user_hook(), user_hook("echo prompt_injection_guard")],
        "afterFileEdit": [user_hook("echo format")],
    }}
    write_json(machine.hooks, original)
    machine.install()
    expected = copy.deepcopy(original)
    for event, definition in definitions(machine.root).items():
        expected["hooks"].setdefault(event, []).append(definition)
    assert read_json(machine.hooks) == expected
    backups = list((machine.state / "backups").glob("cursor-hooks.bak.*"))
    assert len(backups) == 1 and json.loads(backups[0].read_text()) == original


def test_install_adds_version_only_when_absent(machine):
    write_json(machine.hooks, {"version": 3, "hooks": {}})
    machine.install()
    assert read_json(machine.hooks)["version"] == 3
    shutil.rmtree(machine.state)
    write_json(machine.hooks, {"hooks": {}})
    machine.install()
    assert read_json(machine.hooks)["version"] == 1


def test_reinstall_adds_missing_version_to_otherwise_unchanged_file(machine):
    machine.install()
    data = read_json(machine.hooks)
    del data["version"]
    write_json(machine.hooks, data)
    result = machine.run("install")
    assert result.returncode == 0, result.stdout + result.stderr
    assert read_json(machine.hooks) == {"version": 1, "hooks": data["hooks"]}


def test_reinstall_leaves_an_existing_version_alone(machine):
    machine.install()
    data = read_json(machine.hooks)
    data["version"] = 3
    write_json(machine.hooks, data)
    assert "Cursor: unchanged" in machine.run("install").stdout
    assert read_json(machine.hooks)["version"] == 3


def test_manifest_records_every_event(machine):
    machine.install()
    entry = read_json(machine.manifest)["harnesses"]["cursor"]
    defs = definitions(machine.root)
    assert entry["state"] == "installed" and entry["config_path"] == str(machine.hooks)
    assert entry["groups"] == [
        {"event": e, "index": 0, "definition": defs[e], "previous": []} for e in EVENTS]


def test_reinstall_is_idempotent_and_does_not_write(machine):
    machine.install()
    before = machine.hooks.read_bytes()
    os.utime(machine.hooks, ns=(1_700_000_000_123456789,) * 2)
    result = machine.run("install")
    assert result.returncode == 0, result.stdout + result.stderr
    assert machine.hooks.read_bytes() == before
    assert machine.hooks.stat().st_mtime_ns == 1_700_000_000_123456789
    assert "Cursor: unchanged" in result.stdout


def test_reinstall_from_new_root_updates_only_our_entries(machine, tmp_path):
    machine.seed([user_hook()])
    second = tmp_path / "second core"
    shutil.copytree(machine.root, second)
    machine.env["SAMURAI_ROOT"] = str(second)
    result = machine.run("install")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Cursor: updated" in result.stdout
    new = definitions(second)
    assert read_json(machine.hooks)["hooks"] == {e: [user_hook(), new[e]] for e in EVENTS}


def test_install_refuses_malformed_cursor_config_untouched(machine):
    machine.cursor.mkdir()
    machine.hooks.write_bytes(b'{"hooks": {"beforeShellExecution": {}}}')
    result = machine.run("install")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Cursor: FAILED" in result.stdout
    assert machine.hooks.read_bytes() == b'{"hooks": {"beforeShellExecution": {}}}'


def test_flag_cursor_fails_when_not_detected(tmp_path):
    machine = Machine(tmp_path, cursor_evidence=None)
    result = machine.run("install")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Cursor: FAILED" in result.stdout and "not detected" in result.stdout
    assert not machine.cursor.exists()


# --- detection ----------------------------------------------------------------

@pytest.mark.parametrize("evidence,expect", [
    ("path", "installed"), ("app", "installed"), (None, "skipped (not detected)")])
def test_auto_detection_uses_runtime_evidence(tmp_path, evidence, expect):
    machine = Machine(tmp_path, cursor_evidence=evidence)
    machine.env["SAMURAI_HARNESS"] = ""
    result = machine.run("install")
    assert result.returncode == 0, result.stdout + result.stderr
    assert expect in line(result)
    assert machine.hooks.exists() == (evidence is not None)


def test_cursor_agent_on_path_counts(tmp_path):
    machine = Machine(tmp_path, cursor_evidence=None)
    machine.stub(machine.path / "cursor-agent")
    machine.env["SAMURAI_HARNESS"] = ""
    assert "Cursor: installed" in machine.run("install").stdout
    assert machine.hooks.exists()


def test_config_directory_alone_is_not_an_install(tmp_path):
    """~/.cursor outlives the app and is created by other tools; like Codex's, it is
    reported and skipped, never written to."""
    machine = Machine(tmp_path, cursor_evidence=None)
    machine.cursor.mkdir()
    machine.env["SAMURAI_HARNESS"] = ""
    result = machine.run("install")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Cursor config found but Cursor not installed — skipped" in line(result)
    assert list(machine.cursor.iterdir()) == []


# --- doctor ---------------------------------------------------------------------

def test_doctor_reports_cursor_without_claiming_enforcement(machine):
    machine.install()
    result = machine.run("doctor")
    assert result.returncode == 0, result.stdout + result.stderr
    assert ("Cursor: guard installed and working when run directly; "
            "Cursor enforcement not verified by doctor") in result.stdout


def test_doctor_unselected_but_present_cursor_is_skipped(tmp_path):
    machine = Machine(tmp_path)
    machine.env["SAMURAI_HARNESS"] = "claude"
    assert machine.run("install").returncode == 0
    assert "skipped (not selected)" in line(machine.run("doctor"))


@pytest.mark.parametrize("tamper", ["failclosed", "missing-event", "other-root"])
def test_doctor_fails_on_changed_registration(machine, tmp_path, tamper):
    machine.install()
    data = read_json(machine.hooks)
    if tamper == "failclosed":
        data["hooks"]["beforeShellExecution"][0]["failClosed"] = False
    elif tamper == "missing-event":
        del data["hooks"]["beforeMCPExecution"]
    else:
        data["hooks"]["preToolUse"][0] = definitions(tmp_path / "elsewhere")["preToolUse"]
    write_json(machine.hooks, data)
    result = machine.run("doctor")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAILED" in line(result)


def test_doctor_probes_guard_with_cursor_payloads(machine):
    """A guard that blocks correctly but prints invalid JSON for Cursor fails open there."""
    machine.install()
    guard = machine.root / "bin/prompt_injection_guard.py"
    source = guard.read_text()
    broken = source.replace("sys.stdout.write(json.dumps(verdict) + \"\\n\")", "sys.stdout.write('not json')")
    assert broken != source
    guard.write_text(broken)
    result = machine.run("doctor")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Cursor" in result.stdout and "probe" in result.stdout


# --- uninstall ------------------------------------------------------------------

def test_uninstall_removes_only_our_entries(machine):
    original = {"version": 1, "hooks": {"beforeShellExecution": [user_hook()],
                                        "afterFileEdit": [user_hook("echo format")]}}
    write_json(machine.hooks, original)
    machine.install()
    result = machine.run("uninstall")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Cursor: removed" in result.stdout
    data = read_json(machine.hooks)
    assert data["hooks"]["beforeShellExecution"] == [user_hook()]
    assert data["hooks"]["afterFileEdit"] == original["hooks"]["afterFileEdit"]
    assert all(not data["hooks"].get(e) for e in ("beforeMCPExecution", "preToolUse"))
    assert "prompt_injection_guard" not in machine.hooks.read_text()


def test_uninstall_is_not_blocked_by_later_user_hooks(machine):
    """Cursor has no per-hook approval, so no later-hook warning or --force is needed."""
    machine.seed()
    data = read_json(machine.hooks)
    data["hooks"]["beforeShellExecution"].append(user_hook())
    write_json(machine.hooks, data)
    result = machine.run("uninstall")
    assert result.returncode == 0, result.stdout + result.stderr
    assert read_json(machine.hooks)["hooks"]["beforeShellExecution"] == [user_hook()]


def test_uninstall_refuses_an_edited_entry_and_keeps_state(machine):
    machine.seed()
    data = read_json(machine.hooks)
    data["hooks"]["beforeShellExecution"][0]["timeout"] = 99
    write_json(machine.hooks, data)
    result = machine.run("uninstall")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Cursor: FAILED" in result.stdout
    assert read_json(machine.hooks) == data
    assert machine.manifest.exists()


def test_cursor_is_no_longer_named_as_unprotected(machine):
    machine.stub(machine.path / "gemini")
    machine.env["SAMURAI_HARNESS"] = ""
    for command in ("install", "doctor"):
        result = machine.run(command)
        note = next((row for row in result.stdout.splitlines() if "not protected by Order Samurai" in row), "")
        assert "Gemini CLI" in note and "Cursor" not in note.split(". It protects")[0], result.stdout
        assert "Claude Code, Codex and Cursor" in note
