"""samurai install / doctor / uninstall for Gemini CLI, the fourth supported harness.

Gemini CLI keeps hooks under a "hooks" key of ~/.gemini/settings.json, a file shared with
every other Gemini setting (auth, theme, MCP servers...). Hooks are matcher groups like
Claude Code's, but the pre-tool event is BeforeTool, the matcher is a regex over Gemini
tool names, and "timeout" is in MILLISECONDS (Claude Code and Cursor use seconds)."""

import copy
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
EVENT = "BeforeTool"
MATCHER = "^(run_shell_command|write_file|replace|mcp_.+)$"
HOOK_NAME = "order-samurai-guard"


def definition(root):
    command = "python3 " + shlex.quote(str(root / "bin/prompt_injection_guard.py"))
    return {"matcher": MATCHER, "hooks": [
        {"name": HOOK_NAME, "type": "command", "command": command, "timeout": 10000}]}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def read_json(path):
    return json.loads(path.read_text())


def user_group(command="echo user-hook", matcher="write_file"):
    return {"matcher": matcher, "hooks": [{"name": "user-hook", "type": "command",
                                           "command": command, "timeout": 5000}]}


def realistic_settings():
    """A settings.json the way a real user has it: Samurai must not disturb any of it."""
    return {
        "general": {"preferredEditor": "vim", "previewFeatures": True, "vimMode": False},
        "security": {"auth": {"selectedType": "oauth-personal"}},
        "ui": {"theme": "Dracula", "hideBanner": True, "footer": {"hideCWD": False}},
        "model": {"name": "gemini-2.5-pro", "maxSessionTurns": -1},
        "tools": {"allowed": ["run_shell_command(git status)"], "sandbox": False},
        "mcpServers": {"github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
                                  "env": {"GITHUB_TOKEN": "$GITHUB_TOKEN"}, "trust": False}},
        "hooksConfig": {"enabled": True, "notifications": True, "disabled": ["some-other-hook"]},
        "hooks": {
            "BeforeTool": [user_group(), user_group("echo prompt_injection_guard", "run_shell_command")],
            "AfterTool": [user_group("echo format")],
            "SessionStart": [{"hooks": [{"type": "command", "command": "echo hello"}]}],
        },
        "notes": {"unicode": "café ☕", "nested": [1, None, {"k": False}]},
    }


class Machine:
    def __init__(self, base, gemini_evidence="path"):
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
        self.gemini = self.home / ".gemini"
        self.settings = self.gemini / "settings.json"
        self.state = self.home / ".samurai"
        self.manifest = self.state / "install.json"
        self.env = {
            "HOME": str(self.home), "PATH": str(self.path),
            "CODEX_HOME": str(self.home / ".codex"),
            "SAMURAI_CODEX_APP_BIN": str(base / "absent-app/codex"),
            "SAMURAI_APPLICATIONS_DIR": str(self.apps),
            "SAMURAI_HOME": str(self.state), "SAMURAI_ROOT": str(self.root),
            "SAMURAI_HARNESS": "gemini", "SAMURAI_NO_PROMPT": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if gemini_evidence == "path":
            self.stub(self.path / "gemini")

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
        assert "Gemini CLI: installed" in result.stdout, result.stdout
        return result

    def seed(self, before=(), base=None):
        """Recorded state, so doctor/uninstall tests do not depend on install."""
        defn = definition(self.root)
        data = copy.deepcopy(base) if base is not None else {}
        data.setdefault("hooks", {})[EVENT] = [*before, defn]
        write_json(self.settings, data)
        write_json(self.manifest, {
            "version": 1, "selected": ["gemini"], "selection_source": "flag",
            "roots": [str(self.root)], "harnesses": {"gemini": {
                "state": "installed", "config_path": str(self.settings),
                "groups": [{"event": EVENT, "index": len(before), "definition": defn, "previous": []}],
            }},
        })
        return defn


@pytest.fixture
def machine(tmp_path):
    return Machine(tmp_path)


def line(result, label="Gemini CLI"):
    found = [row for row in result.stdout.splitlines() if row.startswith(label + ":")]
    assert len(found) == 1, result.stdout + result.stderr
    return found[0]


# --- the registered entry -----------------------------------------------------------

def test_matcher_covers_shell_file_write_and_mcp_tools_but_not_reads():
    for name in ("run_shell_command", "write_file", "replace", "mcp_github_create_issue", "mcp_docs_search"):
        assert re.search(MATCHER, name), name
    # read_file would block reading Samurai's own pattern files; the rest are not write paths.
    for name in ("read_file", "read_many_files", "glob", "grep_search", "list_directory",
                 "web_fetch", "google_web_search", "write_todos", "run_shell_command2", "my_replace"):
        assert not re.search(MATCHER, name), name


def test_timeout_is_in_milliseconds_not_seconds(machine):
    """Gemini CLI's timeout is milliseconds (default 60000). A seconds value such as 10
    would give the guard 10 ms and time it out on every call."""
    machine.install()
    timeout = read_json(machine.settings)["hooks"][EVENT][0]["hooks"][0]["timeout"]
    assert timeout == 10000


# --- install ------------------------------------------------------------------------

def test_install_creates_private_settings_file_with_the_before_tool_hook_only(machine):
    assert not machine.gemini.exists()
    machine.install()
    assert read_json(machine.settings) == {"hooks": {EVENT: [definition(machine.root)]}}
    assert stat.S_IMODE(machine.settings.stat().st_mode) == 0o600


def test_install_preserves_every_other_setting_and_user_hook(machine):
    original = realistic_settings()
    write_json(machine.settings, original)
    machine.settings.chmod(0o640)
    machine.install()
    expected = copy.deepcopy(original)
    expected["hooks"][EVENT].append(definition(machine.root))
    assert read_json(machine.settings) == expected
    assert stat.S_IMODE(machine.settings.stat().st_mode) == 0o640
    backups = list((machine.state / "backups").glob("gemini-settings.bak.*"))
    assert len(backups) == 1 and json.loads(backups[0].read_text()) == original


def test_install_adds_the_hooks_key_to_settings_that_have_none(machine):
    original = {key: value for key, value in realistic_settings().items() if key != "hooks"}
    write_json(machine.settings, original)
    machine.install()
    assert read_json(machine.settings) == {**original, "hooks": {EVENT: [definition(machine.root)]}}


def test_manifest_records_the_hook(machine):
    machine.install()
    entry = read_json(machine.manifest)["harnesses"]["gemini"]
    assert entry["state"] == "installed" and entry["config_path"] == str(machine.settings)
    assert entry["groups"] == [{"event": EVENT, "index": 0, "definition": definition(machine.root),
                                "previous": []}]


def test_reinstall_is_idempotent_and_does_not_write(machine):
    write_json(machine.settings, realistic_settings())
    machine.install()
    before = machine.settings.read_bytes()
    os.utime(machine.settings, ns=(1_700_000_000_123456789,) * 2)
    result = machine.run("install")
    assert result.returncode == 0, result.stdout + result.stderr
    assert machine.settings.read_bytes() == before
    assert machine.settings.stat().st_mtime_ns == 1_700_000_000_123456789
    assert "Gemini CLI: unchanged" in result.stdout


def test_reinstall_after_losing_the_manifest_adopts_the_exact_entry(machine):
    machine.install()
    machine.manifest.unlink()
    result = machine.run("install")
    assert result.returncode == 0, result.stdout + result.stderr
    assert read_json(machine.settings)["hooks"][EVENT] == [definition(machine.root)]


def test_reinstall_from_new_root_updates_only_our_entry(machine, tmp_path):
    machine.seed([user_group()], base=realistic_settings())
    second = tmp_path / "second core"
    shutil.copytree(machine.root, second)
    machine.env["SAMURAI_ROOT"] = str(second)
    result = machine.run("install")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Gemini CLI: updated" in result.stdout
    hooks = read_json(machine.settings)["hooks"]
    assert hooks[EVENT] == [user_group(), definition(second)]
    assert hooks["AfterTool"] == realistic_settings()["hooks"]["AfterTool"]


@pytest.mark.parametrize("raw", [b'{"hooks": {"BeforeTool": {}}}', b'{"hooks": []}', b"[1, 2]",
                                 b'{"ui": {"theme": "x"}, // a comment\n "hooks": {}}'])
def test_install_refuses_unreadable_gemini_settings_untouched(machine, raw):
    machine.gemini.mkdir()
    machine.settings.write_bytes(raw)
    result = machine.run("install")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Gemini CLI: FAILED" in result.stdout
    assert machine.settings.read_bytes() == raw


def test_install_refuses_a_symlinked_settings_file(machine, tmp_path):
    target = tmp_path / "dotfiles-settings.json"
    target.write_text("{}\n")
    machine.gemini.mkdir()
    machine.settings.symlink_to(target)
    result = machine.run("install")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Gemini CLI: FAILED" in result.stdout
    assert target.read_text() == "{}\n"


def test_install_refuses_a_root_path_gemini_would_expand(machine, tmp_path):
    """Gemini expands $VAR in string values of settings.json; a path with a dollar sign
    would register a different command."""
    odd = tmp_path / "core$HOME"
    shutil.copytree(machine.root, odd)
    machine.env["SAMURAI_ROOT"] = str(odd)
    result = machine.run("install")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Gemini CLI: FAILED" in result.stdout and "$" in line(result)
    assert not machine.settings.exists()


def test_flag_gemini_fails_when_not_detected(tmp_path):
    machine = Machine(tmp_path, gemini_evidence=None)
    result = machine.run("install")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Gemini CLI: FAILED" in result.stdout and "not detected" in result.stdout
    assert not machine.gemini.exists()


# --- detection ----------------------------------------------------------------------

@pytest.mark.parametrize("evidence,expect", [
    ("path", "installed"), (None, "skipped (not detected)")])
def test_auto_detection_uses_runtime_evidence(tmp_path, evidence, expect):
    machine = Machine(tmp_path, gemini_evidence=evidence)
    machine.env["SAMURAI_HARNESS"] = ""
    result = machine.run("install")
    assert result.returncode == 0, result.stdout + result.stderr
    assert expect in line(result)
    assert machine.settings.exists() == (evidence is not None)


def test_config_directory_alone_is_not_an_install(tmp_path):
    """~/.gemini outlives an uninstalled CLI and is also written by other Google tools
    (Antigravity keeps its config under ~/.gemini/antigravity); like Codex's and Cursor's,
    it is reported and skipped, never written to."""
    machine = Machine(tmp_path, gemini_evidence=None)
    machine.gemini.mkdir()
    machine.env["SAMURAI_HARNESS"] = ""
    result = machine.run("install")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Gemini CLI config found but Gemini CLI not installed — skipped" in line(result)
    assert list(machine.gemini.iterdir()) == []


# --- doctor -------------------------------------------------------------------------

def test_doctor_reports_gemini_without_claiming_enforcement(machine):
    machine.install()
    result = machine.run("doctor")
    assert result.returncode == 0, result.stdout + result.stderr
    assert ("Gemini CLI: guard installed and working when run directly; "
            "Gemini CLI enforcement not verified by doctor") in result.stdout


def test_doctor_unselected_but_present_gemini_is_skipped(tmp_path):
    machine = Machine(tmp_path)
    machine.env["SAMURAI_HARNESS"] = "claude"
    assert machine.run("install").returncode == 0
    assert "skipped (not selected)" in line(machine.run("doctor"))


@pytest.mark.parametrize("tamper", ["matcher", "timeout-seconds", "missing-event", "other-root", "async"])
def test_doctor_fails_on_changed_registration(machine, tmp_path, tamper):
    machine.install()
    data = read_json(machine.settings)
    group = data["hooks"][EVENT][0]
    if tamper == "matcher":
        group["matcher"] = "run_shell_command"
    elif tamper == "timeout-seconds":
        group["hooks"][0]["timeout"] = 10
    elif tamper == "missing-event":
        del data["hooks"][EVENT]
    elif tamper == "async":
        group["hooks"][0]["async"] = True
    else:
        data["hooks"][EVENT][0] = definition(tmp_path / "elsewhere")
    write_json(machine.settings, data)
    result = machine.run("doctor")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAILED" in line(result)


@pytest.mark.parametrize("change", [
    {"hooksConfig": {"enabled": False}},
    {"hooksConfig": {"enabled": True, "disabled": [HOOK_NAME]}},
])
def test_doctor_fails_when_gemini_settings_switch_the_hook_off(machine, change):
    machine.install()
    data = read_json(machine.settings)
    data.update(change)
    write_json(machine.settings, data)
    result = machine.run("doctor")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAILED" in line(result) and "hooksConfig" in line(result)


def test_doctor_is_fine_with_unrelated_hooks_config(machine):
    write_json(machine.settings, realistic_settings())
    machine.install()
    result = machine.run("doctor")
    assert result.returncode == 0, result.stdout + result.stderr


def test_doctor_probes_guard_with_gemini_payloads(machine):
    """A guard that blocks but prints something other than JSON is a broken hook to Gemini."""
    machine.install()
    guard = machine.root / "bin/prompt_injection_guard.py"
    source = guard.read_text()
    broken = source.replace("sys.stdout.write(json.dumps(verdict) + \"\\n\")", "sys.stdout.write('not json')")
    assert broken != source
    guard.write_text(broken)
    result = machine.run("doctor")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Gemini benign probe" in result.stdout and "Gemini blocking probe" in result.stdout


def test_doctor_probe_fails_when_guard_allows_a_gemini_attack(machine):
    machine.install()
    guard = machine.root / "bin/prompt_injection_guard.py"
    source = guard.read_text()
    broken = source.replace('    elif harness == "gemini":\n        _emit_gemini_verdict(code == 2, detail)',
                            '    elif harness == "gemini":\n        _emit_gemini_verdict(False, detail)\n        return 0')
    assert broken != source
    guard.write_text(broken)
    result = machine.run("doctor")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Gemini blocking probe" in result.stdout


# --- uninstall ----------------------------------------------------------------------

def test_uninstall_removes_only_our_entry_and_nothing_else(machine):
    original = realistic_settings()
    write_json(machine.settings, original)
    machine.install()
    result = machine.run("uninstall")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Gemini CLI: removed" in result.stdout
    assert read_json(machine.settings) == original
    assert "prompt_injection_guard.py" not in machine.settings.read_text().replace("echo prompt_injection_guard", "")


def test_uninstall_leaves_an_empty_event_list_but_no_samurai_entry(machine):
    machine.install()
    machine.run("uninstall")
    assert read_json(machine.settings) == {"hooks": {EVENT: []}}


def test_uninstall_is_not_blocked_by_later_user_hooks(machine):
    """Gemini has no per-hook approval for user hooks, so removal needs no --force."""
    machine.seed()
    data = read_json(machine.settings)
    data["hooks"][EVENT].append(user_group())
    write_json(machine.settings, data)
    result = machine.run("uninstall")
    assert result.returncode == 0, result.stdout + result.stderr
    assert read_json(machine.settings)["hooks"][EVENT] == [user_group()]


def test_uninstall_refuses_an_edited_entry_and_keeps_state(machine):
    machine.seed()
    data = read_json(machine.settings)
    data["hooks"][EVENT][0]["hooks"][0]["timeout"] = 99
    write_json(machine.settings, data)
    result = machine.run("uninstall")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Gemini CLI: FAILED" in result.stdout
    assert read_json(machine.settings) == data
    assert machine.manifest.exists()


def test_gemini_is_no_longer_named_as_unprotected(machine):
    machine.stub(machine.path / "goose")
    machine.env["SAMURAI_HARNESS"] = ""
    for command in ("install", "doctor"):
        result = machine.run(command)
        note = next((row for row in result.stdout.splitlines() if "not protected by Order Samurai" in row), "")
        assert "Goose" in note and "Gemini" not in note.split(". It protects")[0], result.stdout
        assert "Claude Code, Codex, Cursor and Gemini CLI" in note


def test_no_unprotected_note_names_gemini_even_with_only_its_folder(tmp_path):
    machine = Machine(tmp_path, gemini_evidence=None)
    machine.gemini.mkdir()
    machine.env["SAMURAI_HARNESS"] = ""
    result = machine.run("install")
    assert "not protected by Order Samurai" not in result.stdout, result.stdout
