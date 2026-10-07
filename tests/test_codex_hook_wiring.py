"""Independent acceptance tests for the approved Codex hook mirror contract."""

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
SETTINGS_NAME = "set" + "tings.json"


def guard(root):
    return {"matcher": "^(Bash|apply_patch)$", "hooks": [{
        "type": "command",
        "command": "python3 " + shlex.quote(str(root / "bin/prompt_injection_guard.py")),
        "timeout": 10,
    }]}


def user_group(command="echo user-hook"):
    return {"matcher": "Bash", "hooks": [{"type": "command", "command": command}]}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def read_json(path):
    return json.loads(path.read_text())


class Machine:
    def __init__(self, base):
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
        for name in ("claude", "codex"):
            stub = self.path / name
            stub.write_text("#!/bin/sh\nexit 0\n")
            stub.chmod(0o755)
        self.codex = self.home / "custom-codex"
        self.hooks = self.codex / "hooks.json"
        self.state = self.home / ".samurai"
        self.manifest = self.state / "install.json"
        self.env = {
            "HOME": str(self.home), "PATH": str(self.path),
            "CODEX_HOME": str(self.codex),
            "SAMURAI_CODEX_APP_BIN": str(base / "absent-app/codex"),
            "SAMURAI_HOME": str(self.state), "SAMURAI_ROOT": str(self.root),
            "SAMURAI_HARNESS": "", "SAMURAI_NO_PROMPT": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def run(self, *args):
        return subprocess.run([sys.executable, str(self.root / "bin/samurai"), *args],
                              env=self.env, cwd=self.home, capture_output=True,
                              text=True, timeout=4)

    def install(self):
        result = self.run("install")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Codex: installed" in result.stdout, result.stdout
        return result

    def seed(self, before=()):
        # Recorded state lets doctor/uninstall tests exercise their own behavior
        # even when the install feature is still missing on the RED baseline.
        definition = guard(self.root)
        write_json(self.hooks, {"hooks": {"PreToolUse": [*before, definition]}})
        write_json(self.manifest, {
            "version": 1, "selected": ["codex"], "selection_source": "flag",
            "roots": [str(self.root)], "harnesses": {"codex": {
                "state": "installed", "config_path": str(self.hooks),
                "groups": [{"event": "PreToolUse", "index": len(before),
                            "definition": definition, "previous": []}],
            }},
        })
        return definition


@pytest.fixture
def machine(tmp_path):
    return Machine(tmp_path)


def codex_line(result):
    lines = [line for line in result.stdout.splitlines() if line.startswith("Codex")]
    assert len(lines) == 1, result.stdout + result.stderr
    assert "protected" not in lines[0].lower(), lines[0]
    return lines[0]


def test_install_preserves_user_content_and_appends_exact_group(machine):
    """Case 5: preserve all user content and indices, then append our exact group."""
    original = {"version": 7, "custom": {"nested": [1, False, "keep"]}, "hooks": {
        "SessionStart": [user_group("echo start")],
        "PreToolUse": [user_group(), user_group("echo prompt_injection_guard")],
        "PostToolUse": [user_group("echo post"), user_group("echo post-two")],
    }}
    write_json(machine.hooks, original)
    machine.install()
    expected = copy.deepcopy(original)
    expected["hooks"]["PreToolUse"].append(guard(machine.root))
    assert read_json(machine.hooks) == expected


def test_install_creates_private_hooks_file(machine):
    """Case 5: create a missing Codex home and hooks file with mode 0600."""
    assert not machine.codex.exists()
    machine.install()
    assert read_json(machine.hooks) == {"hooks": {"PreToolUse": [guard(machine.root)]}}
    assert stat.S_IMODE(machine.hooks.stat().st_mode) == 0o600


@pytest.mark.parametrize("kind", ["invalid-json", "top-level-array", "hooks-array",
                                      "event-object", "group-string", "symlink", "directory"])
def test_invalid_codex_target_is_untouched_and_claude_still_installs(machine, kind):
    """Case 5: reject unsafe or malformed Codex config without blocking Claude."""
    machine.codex.mkdir()
    body = {"invalid-json": b'{broken', "top-level-array": b'[]',
            "hooks-array": b'{"hooks": []}',
            "event-object": b'{"hooks": {"SessionStart": {}}}',
            "group-string": b'{"hooks": {"PreToolUse": ["bad"]}}'}.get(kind, b'{"hooks": {}}')
    target = machine.codex / "user-original.json"
    if kind == "symlink":
        target.write_bytes(body)
        machine.hooks.symlink_to(target)
        link = os.readlink(machine.hooks)
    elif kind == "directory":
        machine.hooks.mkdir()
        (machine.hooks / "keep").write_bytes(body)
    else:
        machine.hooks.write_bytes(body)
    result = machine.run("install")
    if kind == "symlink":
        assert machine.hooks.is_symlink() and os.readlink(machine.hooks) == link
        assert target.read_bytes() == body
    elif kind == "directory":
        assert machine.hooks.is_dir()
        assert list(machine.hooks.iterdir()) == [machine.hooks / "keep"]
        assert (machine.hooks / "keep").read_bytes() == body
    else:
        assert machine.hooks.read_bytes() == body
    settings = read_json(machine.home / ".claude" / SETTINGS_NAME)
    assert settings["hooks"]["PreToolUse"] and settings["hooks"]["PostToolUse"]
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Codex: FAILED" in result.stdout and "Claude Code: installed" in result.stdout


def test_manifest_records_full_codex_ownership(machine):
    """Case 4: record the resolved path, event, index, definition and prior definitions."""
    write_json(machine.hooks, {"hooks": {"PreToolUse": [user_group()]}})
    machine.install()
    manifest = read_json(machine.manifest)
    assert manifest["version"] == 1
    assert set(manifest["selected"]) == {"claude", "codex"}
    assert manifest["selection_source"] == "auto"
    assert str(machine.root) in manifest["roots"]
    assert manifest["harnesses"]["codex"] == {
        "state": "installed", "config_path": str(machine.hooks), "groups": [{
            "event": "PreToolUse", "index": 1, "definition": guard(machine.root), "previous": [],
        }],
    }
    assert stat.S_IMODE(machine.manifest.stat().st_mode) == 0o600


def test_reinstall_unchanged_preserves_bytes_and_mtime(machine):
    """Case 6: unchanged reinstall makes no write to hooks.json."""
    machine.install()
    before = machine.hooks.read_bytes()
    os.utime(machine.hooks, ns=(1_700_000_000_123456789, 1_700_000_000_123456789))
    mtime = machine.hooks.stat().st_mtime_ns
    result = machine.run("install")
    assert result.returncode == 0, result.stdout + result.stderr
    assert machine.hooks.read_bytes() == before
    assert machine.hooks.stat().st_mtime_ns == mtime
    assert "Codex: unchanged" in result.stdout


def test_reinstall_changed_root_replaces_only_owned_index(machine, tmp_path):
    """Cases 4/6: update our path in place and retain the previous definition."""
    old = machine.seed([user_group()])
    data = read_json(machine.hooks)
    data["hooks"]["PreToolUse"].append(user_group("echo after"))
    write_json(machine.hooks, data)
    second = tmp_path / "second core"
    shutil.copytree(machine.root, second)
    machine.env["SAMURAI_ROOT"] = str(second)
    result = machine.run("install")
    assert result.returncode == 0, result.stdout + result.stderr
    data["hooks"]["PreToolUse"][1] = guard(second)
    assert read_json(machine.hooks) == data
    line = codex_line(result)
    assert "Codex: updated" in line and "re-approve this hook only" in line
    recorded = read_json(machine.manifest)["harnesses"]["codex"]["groups"][0]
    assert recorded["index"] == 1 and recorded["definition"] == guard(second)
    assert old in recorded["previous"]


@pytest.mark.parametrize("change", ["insert-before", "move"])
@pytest.mark.parametrize("reinstall", [True, False], ids=["reinstall-first", "uninstall-directly"])
def test_revalidation_relocates_unique_group_for_reinstall_and_uninstall(machine, change, reinstall):
    """Case 4: find our unique moved group and preserve the user's inserted group."""
    lookalike = user_group("echo prompt_injection_guard")
    ours = machine.seed([lookalike])
    inserted = user_group("echo inserted")
    groups = [inserted, lookalike, ours] if change == "insert-before" else [ours, lookalike]
    write_json(machine.hooks, {"hooks": {"PreToolUse": groups}})
    if reinstall:
        result = machine.run("install")
        assert result.returncode == 0, result.stdout + result.stderr
        assert read_json(machine.hooks)["hooks"]["PreToolUse"] == groups
        recorded = read_json(machine.manifest)["harnesses"]["codex"]["groups"][0]
        assert recorded["index"] == groups.index(ours)
    # A moved group before user hooks needs the explicitly authorized force path.
    result = machine.run("uninstall", *(["--force"] if change == "move" else []))
    assert result.returncode == 0, result.stdout + result.stderr
    assert read_json(machine.hooks)["hooks"]["PreToolUse"] == [g for g in groups if g != ours]


@pytest.mark.parametrize("change", ["duplicate", "matcher", "timeout", "mixed", "missing"])
@pytest.mark.parametrize("operation", ["install", "uninstall"])
def test_revalidation_refuses_ambiguous_or_edited_ownership(machine, change, operation):
    """Cases 4/7: refuse ambiguous or edited ownership without rewriting config or deleting state."""
    ours = machine.seed([user_group("echo prompt_injection_guard")])
    data = read_json(machine.hooks)
    groups = data["hooks"]["PreToolUse"]
    if change == "duplicate":
        groups.append(copy.deepcopy(ours))
    elif change == "matcher":
        groups[-1]["matcher"] = "Bash"
    elif change == "timeout":
        groups[-1]["hooks"][0]["timeout"] = 11
    elif change == "mixed":
        groups[-1]["hooks"].append(user_group()["hooks"][0])
    else:
        groups.pop()
    write_json(machine.hooks, data)
    before = machine.hooks.read_bytes()
    sentinel = machine.state / "keep-data"
    sentinel.write_text("keep")
    result = machine.run(operation)
    assert machine.hooks.read_bytes() == before
    assert machine.manifest.exists() and sentinel.read_text() == "keep"
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Codex: FAILED" in result.stdout and "refus" in result.stdout.lower()


def test_uninstall_removes_last_owned_group_and_keeps_lookalike(machine):
    """Cases 4/7: remove only the recorded last group, never a marker lookalike."""
    lookalike = user_group("echo prompt_injection_guard")
    machine.seed([lookalike])
    result = machine.run("uninstall")
    assert result.returncode == 0, result.stdout + result.stderr
    assert read_json(machine.hooks)["hooks"]["PreToolUse"] == [lookalike]
    assert "Codex: removed" in result.stdout


@pytest.mark.parametrize("force", [False, True])
def test_uninstall_with_later_groups_requires_force_and_reports_count(machine, force):
    """Case 7: refuse index shifts unless forced, reporting the two affected later hooks."""
    ours = machine.seed()
    later = [user_group("echo after-one"), user_group("echo after-two")]
    write_json(machine.hooks, {"hooks": {"PreToolUse": [ours, *later]}})
    before = machine.hooks.read_bytes()
    sentinel = machine.state / "keep-data"
    sentinel.write_text("keep")
    result = machine.run("uninstall", *(["--force"] if force else []))
    if force:
        assert result.returncode == 0, result.stdout + result.stderr
        assert read_json(machine.hooks)["hooks"]["PreToolUse"] == later
        assert "re-ask" in result.stdout
    else:
        assert machine.hooks.read_bytes() == before
        assert machine.manifest.exists() and sentinel.read_text() == "keep"
        assert result.returncode == 1, result.stdout + result.stderr
        assert "refus" in result.stdout.lower() and "later hook" in result.stdout
    assert re.search(r"\b2\b", result.stdout), result.stdout


def test_uninstall_keeps_state_on_unreadable_json(machine):
    """Case 7: a deregistration parse failure keeps the manifest and all user data."""
    machine.seed()
    machine.hooks.write_bytes(b'{not json')
    sentinel = machine.state / "keep-data"
    sentinel.write_text("keep")
    result = machine.run("uninstall")
    assert machine.hooks.read_bytes() == b'{not json'
    assert machine.manifest.exists() and sentinel.read_text() == "keep"
    assert result.returncode != 0
    assert "Codex: FAILED" in result.stdout


def test_uninstall_uses_recorded_path_after_runtime_removed(machine):
    """Case 4: removal of the Codex runtime does not strand its installed hook."""
    machine.install()
    (machine.path / "codex").unlink()
    assert not Path(machine.env["SAMURAI_CODEX_APP_BIN"]).exists()
    result = machine.run("uninstall")
    assert result.returncode == 0, result.stdout + result.stderr
    assert read_json(machine.hooks)["hooks"]["PreToolUse"] == []
    assert "Codex: removed" in result.stdout


def test_doctor_and_uninstall_use_recorded_path_after_codex_home_changes(machine):
    """Case 4: doctor and uninstall use recorded config and trust paths after CODEX_HOME changes."""
    machine.install()
    machine.codex.joinpath("config.toml").write_text('[features]\nhooks = false\n')
    elsewhere = machine.home / "new-codex"
    machine.env["CODEX_HOME"] = str(elsewhere)
    doctor = machine.run("doctor")
    assert "hooks disabled in Codex config" in codex_line(doctor)
    assert not elsewhere.exists()
    result = machine.run("uninstall")
    assert result.returncode == 0, result.stdout + result.stderr
    assert read_json(machine.hooks)["hooks"]["PreToolUse"] == []
    assert not elsewhere.exists()


def trust_config(path, index=0):
    key = json.dumps(f"{path}:pre_tool_use:{index}:0")
    return ('# User config: retain bytes and comments.\nmodel = "gpt-5"\n'
            '[features]\nhooks = true\n'
            f'[hooks.state.{key}]\ntrusted_hash = "fake-test-hash"\n')


def test_config_toml_is_byte_identical_across_all_commands(machine):
    """Case 10: install, reinstall, doctor and uninstall never edit Codex trust config."""
    machine.codex.mkdir()
    config = machine.codex / "config.toml"
    original = trust_config(machine.hooks).encode()
    config.write_bytes(original)
    for command in ("install", "install", "doctor", "uninstall"):
        result = machine.run(command)
        assert config.read_bytes() == original, command
        assert result.returncode == 0, result.stdout + result.stderr
        assert codex_line(result).startswith("Codex: ")


@pytest.mark.parametrize("state, expected", [
    ("absent", "no trust record — approve in Codex /hooks"),
    ("present", "trust record present — current approval unverified"),
    ("wrong-index", "no trust record — approve in Codex /hooks"),
    ("disabled", "hooks disabled in Codex config"),
    ("invalid", "unknown"),
], ids=["absent", "present", "wrong-index", "disabled", "invalid"])
def test_doctor_reports_direct_probe_limits_and_trust(machine, state, expected):
    """Cases 11/14: report direct execution and trust honestly, without claiming Codex protection."""
    machine.seed([user_group()])
    config = machine.codex / "config.toml"
    bodies = {"present": trust_config(machine.hooks, 1),
              "wrong-index": trust_config(machine.hooks, 0),
              "disabled": trust_config(machine.hooks, 1).replace("hooks = true", "hooks = false"),
              "invalid": "[features\nthis is not toml"}
    if state != "absent":
        config.write_text(bodies[state])
    result = machine.run("doctor")
    line = codex_line(result)
    assert "guard installed and working when run directly; Codex enforcement not verified by doctor" in line
    assert expected in line
    if state == "absent":
        assert not config.exists()
    else:
        assert config.read_text() == bodies[state]


def test_doctor_reports_codex_packaged_guard_failure(machine):
    """Cases 11/14: a crashing packaged guard is not protecting and never earns a success claim."""
    machine.seed()
    (machine.root / "bin/prompt_injection_guard.py").write_text("raise SystemExit(1)\n")
    result = machine.run("doctor")
    assert result.returncode != 0
    line = codex_line(result)
    assert "not protecting" in line
    assert "guard installed and working" not in line
    assert "packaged script failed" in result.stdout


@pytest.mark.parametrize("change", ["command", "async", "matcher", "mixed"])
def test_doctor_rejects_codex_registration_mismatch_without_execution(machine, change):
    """Case 11: Codex registration must match its manifest and remain synchronous."""
    machine.seed()
    marker = machine.home / "config-command-ran"
    command = machine.home / "untrusted_prompt_injection_guard.py"
    command.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n")
    data = read_json(machine.hooks)
    group = data["hooks"]["PreToolUse"][0]
    handler = group["hooks"][0]
    if change == "command":
        handler["command"] = "python3 " + shlex.quote(str(command))
    elif change == "async":
        handler["async"] = True
    elif change == "matcher":
        group["matcher"] = "^Bash$"
    else:
        group["hooks"].append({"type": "command", "command": "python3 " + shlex.quote(str(command))})
    write_json(machine.hooks, data)
    before = machine.hooks.read_bytes()
    result = machine.run("doctor")
    assert not marker.exists(), "doctor executed a command from Codex config"
    assert machine.hooks.read_bytes() == before
    assert result.returncode != 0
    assert "registration mismatch" in codex_line(result)
    assert "packaged script failed" not in result.stdout
