"""Red-first acceptance tests for hook-mirror plan cases 1, 2, 3, and 12."""

import builtins
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO = Path(__file__).resolve().parents[1]
SETTINGS_NAME = "set" + "tings.json"
LABELS = {"claude": "Claude Code", "codex": "Codex"}
DETECTION_CASES = [
    pytest.param("claude-dir", True, False, False, id="claude-only-directory"),
    pytest.param("claude-path", True, False, False, id="claude-only-path"),
    pytest.param("codex-path", False, True, False, id="codex-only-path"),
    pytest.param("codex-app", False, True, False, id="codex-app-only"),
    pytest.param("both", True, True, False, id="both"),
    pytest.param("neither", False, False, False, id="neither"),
    pytest.param("stale", False, False, True, id="stale-codex-home"),
    pytest.param("app-not-executable", False, False, False, id="nonexecutable-app"),
    pytest.param("app-directory", False, False, False, id="app-is-directory"),
]


@pytest.fixture
def machine(tmp_path, monkeypatch):
    home, root, path = tmp_path / "home", tmp_path / "core", tmp_path / "path"
    home.mkdir()
    path.mkdir()
    # Copy runtime dependencies so probes and logs cannot touch the checkout.
    for directory in ("bin", "agentica_core", "config"):
        shutil.copytree(REPO / directory, root / directory,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (root / "state").mkdir()
    shutil.copy2(REPO / "state" / "kill_chain_taxonomy.json", root / "state")
    (path / "python3").symlink_to(sys.executable)
    app = tmp_path / "app" / "codex"
    app.parent.mkdir()
    env = {
        "HOME": str(home),
        "PATH": str(path),
        "CODEX_HOME": str(home / ".codex"),
        "SAMURAI_CODEX_APP_BIN": str(app),
        "SAMURAI_APPLICATIONS_DIR": str(tmp_path / "Applications"),
        "SAMURAI_HOME": str(home / ".samurai"),
        "SAMURAI_ROOT": str(root),
        "SAMURAI_NO_PROMPT": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "LANG": "C.UTF-8",
    }
    for key in tuple(os.environ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(tmp_path)
    return SimpleNamespace(
        home=home, root=root, path=path, app=app, env=env, cwd=tmp_path,
        claude=home / ".claude" / SETTINGS_NAME,
        codex=home / ".codex" / "hooks.json",
        manifest=home / ".samurai" / "install.json",
    )


def _runtime(path):
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)


def _prepare(machine, kind):
    if kind in {"claude-dir", "both"}:
        machine.claude.parent.mkdir()
    if kind == "claude-path":
        _runtime(machine.path / "claude")
    if kind in {"codex-path", "both"}:
        _runtime(machine.path / "codex")
    if kind in {"codex-app", "app-not-executable"}:
        _runtime(machine.app)
        if kind == "app-not-executable":
            machine.app.chmod(0o644)
    if kind == "app-directory":
        machine.app.mkdir()
    if kind == "stale":
        machine.codex.parent.mkdir()


def _load(machine):
    loader = SourceFileLoader("samurai_harness_detection", str(machine.root / "bin" / "samurai"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _run(machine, *args, selection=None):
    env = dict(machine.env)
    if selection is not None:
        env["SAMURAI_HARNESS"] = selection
    return subprocess.run(
        [sys.executable, str(machine.root / "bin" / "samurai"), *args],
        cwd=machine.cwd, env=env, capture_output=True, text=True, timeout=4,
    )


def _lines(result):
    lines = {}
    for harness, label in LABELS.items():
        found = [line for line in result.stdout.splitlines() if line.startswith(label + ": ")]
        assert len(found) == 1, f"Expected one {label} result line:\n{result.stdout}\n{result.stderr}"
        lines[harness] = found[0]
    return lines


def _assert_hooks(path, count=1):
    assert path.is_file(), f"Missing hook config: {path}"
    groups = json.loads(path.read_text())["hooks"]["PreToolUse"]
    guards = [group for group in groups if any(
        "prompt_injection_guard.py" in hook.get("command", "")
        for hook in group.get("hooks", [])
    )]
    assert len(guards) == count, groups


def _assert_selected(machine, selected):
    for harness, path in (("claude", machine.claude), ("codex", machine.codex)):
        if harness in selected:
            _assert_hooks(path)
        else:
            assert not path.exists(), f"Unselected {harness} was written"


@pytest.mark.parametrize("kind,claude,codex,config_only", DETECTION_CASES)
def test_detect_harnesses_reads_only_allowed_locations(machine, monkeypatch, kind, claude, codex, config_only):
    """Case 1: Detect independent runtime evidence without reading other locations."""
    _prepare(machine, kind)
    module = _load(machine)
    detect = module.detect_harnesses
    allowed = {
        machine.claude.parent, machine.codex.parent, machine.path,
        machine.path / "claude", machine.path / "codex", machine.app,
        machine.home / ".cursor", machine.path / "cursor", machine.path / "cursor-agent",
        machine.cwd / "Applications" / "Cursor.app",
    }
    touched = []

    def record(original):
        def checked(path, *args, **kwargs):
            assert not isinstance(path, int), "Detection must not inspect open descriptors"
            absolute = Path(os.path.abspath(os.fsdecode(path)))
            touched.append(absolute)
            assert absolute in allowed, f"Detection read outside allowed locations: {absolute}"
            return original(path, *args, **kwargs)
        return checked

    with monkeypatch.context() as reads:
        for name in ("stat", "lstat", "access", "listdir", "scandir", "open", "readlink"):
            reads.setattr(os, name, record(getattr(os, name)))
        reads.setattr(builtins, "open", record(builtins.open))
        reads.setattr(io, "open", record(io.open))
        result = detect()

    assert touched, "Detection made no filesystem checks"
    assert set(touched) <= allowed
    assert result["claude"]["present"] is claude
    assert result["codex"]["present"] is codex
    assert result["codex"]["config_only"] is config_only
    assert result["cursor"] == {"present": False, "evidence": result["cursor"]["evidence"], "config_only": False}
    assert isinstance(result["claude"]["evidence"], str)
    assert isinstance(result["codex"]["evidence"], str)


@pytest.mark.parametrize("kind,claude,codex,config_only", DETECTION_CASES)
def test_install_auto_detects_harnesses(machine, kind, claude, codex, config_only):
    """Cases 1, 12: Auto-install only detected harnesses and report both outcomes.
    Installing nothing is a failure: scripts and installers must not read it as success."""
    _prepare(machine, kind)
    result = _run(machine, "install")
    assert result.returncode == (0 if claude or codex else 1), result.stdout + result.stderr
    _assert_selected(machine, {h for h, present in (("claude", claude), ("codex", codex)) if present})
    lines = _lines(result)
    assert ("installed" if claude else "skipped (not detected)") in lines["claude"]
    if config_only:
        assert "Codex config found but Codex not installed — skipped" in lines["codex"]
    else:
        expected = "installed — approve it in Codex's /hooks screen" if codex else "skipped (not detected)"
        assert expected in lines["codex"]


@pytest.mark.parametrize("value", ["absolute", "relative", "empty", "unset"])
def test_codex_home_resolver(machine, monkeypatch, value):
    """Case 2: Resolve explicit, relative, empty, and unset CODEX_HOME consistently."""
    expected = machine.home / ".codex"
    if value == "absolute":
        expected = machine.cwd / "relocated"
        monkeypatch.setenv("CODEX_HOME", str(expected))
    elif value == "relative":
        expected = machine.cwd / "relative" / "codex"
        monkeypatch.setenv("CODEX_HOME", "relative/codex")
    elif value == "empty":
        monkeypatch.setenv("CODEX_HOME", "")
    else:
        monkeypatch.delenv("CODEX_HOME")
    result = _load(machine).codex_home()
    assert isinstance(result, Path)
    assert result == expected
    assert result.is_absolute()


@pytest.mark.parametrize("default_exists", [False, True], ids=["default-absent", "default-untouched"])
def test_install_uses_relocated_codex_home(machine, default_exists):
    """Case 2: Relocated install leaves the default Codex home absent or unchanged."""
    _prepare(machine, "codex-path")
    if default_exists:
        machine.codex.parent.mkdir()
        machine.codex.write_bytes(b'{"user": "leave default alone"}\n')
        before = machine.codex.stat()
    relocated = machine.cwd / "relocated"
    machine.env["CODEX_HOME"] = str(relocated)
    result = _run(machine, "install")
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_hooks(relocated / "hooks.json")
    if default_exists:
        assert machine.codex.read_bytes() == b'{"user": "leave default alone"}\n'
        assert machine.codex.stat().st_mtime_ns == before.st_mtime_ns
        assert list(machine.codex.parent.iterdir()) == [machine.codex]
    else:
        assert not machine.codex.parent.exists()


@pytest.mark.parametrize("flag,env,selected", [
    ("claude", "codex", {"claude"}),
    ("codex", "claude", {"codex"}),
    (None, "claude", {"claude"}),
    (None, "codex", {"codex"}),
    ("claude", "gemini", {"claude"}),
], ids=["flag-claude", "flag-codex", "env-claude", "env-codex", "flag-beats-invalid-env"])
def test_harness_selection_precedence(machine, flag, env, selected):
    """Case 3: The flag overrides the environment, which overrides auto-detection."""
    _prepare(machine, "both")
    args = ("--harness", flag) if flag else ()
    result = _run(machine, "install", *args, selection=env)
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_selected(machine, selected)
    lines = _lines(result)
    for harness in LABELS:
        assert ("installed" if harness in selected else "skipped (not selected)") in lines[harness]


@pytest.mark.parametrize("source", ["flag", "env"])
def test_comma_list_trims_whitespace_and_ignores_duplicates(machine, source):
    """Case 3: Comma lists select each harness once despite whitespace and duplicates."""
    _prepare(machine, "both")
    selection = " claude, codex,claude, codex "
    args = ("--harness", selection) if source == "flag" else ()
    result = _run(machine, "install", *args, selection=selection if source == "env" else None)
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_selected(machine, {"claude", "codex"})
    assert all("installed" in line for line in _lines(result).values())
    manifest = json.loads(machine.manifest.read_text())
    assert sorted(manifest["selected"]) == ["claude", "codex"]


@pytest.mark.parametrize("source", ["flag", "env"])
def test_unknown_harness_rejected_before_any_write(machine, source):
    """Case 3: An unknown list member exits 2 before writing any install state."""
    _prepare(machine, "both")
    args = ("--harness", "claude,gemini") if source == "flag" else ()
    result = _run(machine, "install", *args, selection="claude,gemini" if source == "env" else None)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "unknown harness" in (result.stdout + result.stderr).lower()
    assert not machine.manifest.parent.exists()
    assert not machine.claude.exists()
    assert not machine.codex.exists()
    assert list(machine.claude.parent.iterdir()) == []


@pytest.mark.parametrize("present", ["claude", "codex"])
def test_explicit_absent_harness_fails_without_blocking_present_harness(machine, present):
    """Cases 3, 12: An absent requested harness fails while its present peer installs."""
    _prepare(machine, "claude-dir" if present == "claude" else "codex-path")
    result = _run(machine, "install", "--harness", "claude,codex")
    assert result.returncode == 1, result.stdout + result.stderr
    lines = _lines(result)
    absent = "codex" if present == "claude" else "claude"
    assert "installed" in lines[present]
    assert "FAILED" in lines[absent] and "not detected" in lines[absent]
    _assert_selected(machine, {present})
    absent_path = machine.codex if absent == "codex" else machine.claude
    assert not absent_path.parent.exists()
    assert absent not in json.loads(machine.manifest.read_text())["harnesses"]


@pytest.mark.parametrize("selected", ["claude", "codex"])
def test_doctor_skips_detected_but_unselected_harness(machine, selected):
    """Case 3: Doctor treats a detected but deliberately unselected harness as skipped."""
    _prepare(machine, "both")
    installed = _run(machine, "install", "--harness", selected)
    assert installed.returncode == 0, installed.stdout + installed.stderr
    result = _run(machine, "doctor")
    assert result.returncode == 0, result.stdout + result.stderr
    unselected = "codex" if selected == "claude" else "claude"
    line = _lines(result)[unselected]
    assert "skipped (not selected)" in line
    assert "FAIL" not in line
    _assert_selected(machine, {selected})


@pytest.mark.parametrize("override", [None, "env", "flag"], ids=["remember", "env-overrides-record", "flag-overrides-record"])
def test_later_install_remembers_or_overrides_explicit_selection(machine, override):
    """Case 3: Plain install remembers selection; a new environment or flag overrides it."""
    _prepare(machine, "both")
    first = _run(machine, "install", "--harness", "claude")
    assert first.returncode == 0, first.stdout + first.stderr
    _assert_selected(machine, {"claude"})
    args = ("--harness", "claude,codex") if override == "flag" else ()
    env = "claude,codex" if override == "env" else None
    result = _run(machine, "install", *args, selection=env)
    assert result.returncode == 0, result.stdout + result.stderr
    selected = {"claude", "codex"} if override else {"claude"}
    _assert_selected(machine, selected)
    lines = _lines(result)
    assert "unchanged" in lines["claude"]
    assert ("installed" if override else "skipped (not selected)") in lines["codex"]


@pytest.mark.parametrize("command", ["install", "doctor", "uninstall"])
def test_success_prints_one_line_per_harness(machine, command):
    """Case 12: Successful install, doctor, and uninstall each report both harnesses once."""
    _prepare(machine, "both")
    if command != "install":
        installed = _run(machine, "install")
        assert installed.returncode == 0, installed.stdout + installed.stderr
    result = _run(machine, command)
    assert result.returncode == 0, result.stdout + result.stderr
    lines = _lines(result)
    assert all("FAILED" not in line for line in lines.values())
    if command == "install":
        assert all("installed" in line for line in lines.values())
    elif command == "doctor":
        assert "guard installed and working when run directly; Codex enforcement not verified by doctor" in lines["codex"]
    else:
        assert all("removed" in line for line in lines.values())


def test_invalid_codex_hooks_fail_install_while_claude_installs(machine):
    """Case 12: Invalid Codex JSON produces a failed result and exit 1 despite Claude success."""
    _prepare(machine, "both")
    machine.codex.parent.mkdir()
    invalid = b'{"hooks": broken\n'
    machine.codex.write_bytes(invalid)
    result = _run(machine, "install")
    assert result.returncode == 1, result.stdout + result.stderr
    lines = _lines(result)
    assert "Claude Code: installed" in lines["claude"]
    assert "Codex: FAILED" in lines["codex"]
    _assert_hooks(machine.claude)
    assert machine.codex.read_bytes() == invalid


@pytest.mark.parametrize("command", ["doctor", "uninstall"])
def test_invalid_recorded_codex_hooks_fail_with_one_line_per_harness(machine, command):
    """Case 12: A broken recorded Codex config makes doctor or uninstall return failure."""
    _prepare(machine, "both")
    installed = _run(machine, "install")
    assert installed.returncode == 0, installed.stdout + installed.stderr
    _assert_hooks(machine.codex)
    machine.codex.write_bytes(b'{"hooks": broken\n')
    result = _run(machine, command)
    assert result.returncode == 1, result.stdout + result.stderr
    lines = _lines(result)
    assert "FAILED" in lines["codex"]
    assert "FAILED" not in lines["claude"]


UNPROTECTED_NOTE = "not protected by Order Samurai"


def _add_other_agents(machine):
    """Gemini CLI on PATH, a Goose config dir and a Windsurf app bundle."""
    _runtime(machine.path / "gemini")
    (machine.home / ".config" / "goose").mkdir(parents=True)
    (machine.cwd / "Applications" / "Windsurf.app").mkdir(parents=True)


def test_install_and_doctor_name_detected_agents_they_cannot_protect(machine):
    _prepare(machine, "claude-dir")
    _add_other_agents(machine)
    install = _run(machine, "install")
    assert install.returncode == 0, install.stdout + install.stderr
    doctor = _run(machine, "doctor")
    for result in (install, doctor):
        line = next((l for l in result.stdout.splitlines() if UNPROTECTED_NOTE in l), "")
        for label in ("Gemini CLI", "Goose", "Windsurf"):
            assert label in line, result.stdout
        assert "Cursor" not in line.split(". It protects")[0], "Cursor is protected now"


def test_no_unprotected_note_when_only_supported_harnesses_exist(machine):
    _prepare(machine, "claude-dir")
    for command in ("install", "doctor"):
        result = _run(machine, command)
        assert UNPROTECTED_NOTE not in result.stdout, result.stdout


def test_unprotected_agents_named_when_no_supported_harness_exists(machine):
    (machine.home / ".gemini").mkdir()
    result = _run(machine, "install")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Gemini CLI" in next(l for l in result.stdout.splitlines() if UNPROTECTED_NOTE in l)


@pytest.mark.parametrize("evidence", ["cn-on-path", "continue-dir"])
def test_continue_is_named_as_unprotected(machine, evidence):
    """Continue loads ~/.claude/settings.json but never fires PreToolUse/PostToolUse
    (continuedev/continue#11029 wired no tool-execution call), so it is not protected."""
    _prepare(machine, "claude-dir")
    if evidence == "cn-on-path":
        _runtime(machine.path / "cn")
    else:
        (machine.home / ".continue").mkdir()
    result = _run(machine, "install")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Continue" in next((l for l in result.stdout.splitlines() if UNPROTECTED_NOTE in l), ""), result.stdout
