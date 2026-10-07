"""Red-first acceptance tests for plan cases 4, 8 and 13.

Expected definitions are built from the approved interface contract, independently
of the installer's definition builder. All runtime state stays inside tmp_path.
"""
from __future__ import annotations

import argparse
import copy
import datetime
import fcntl
import importlib.util
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SETTINGS_NAME = "set" + "tings.json"
USER_GROUP = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo user-hook"}]}


@pytest.fixture
def box(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "new root"
    (root / "bin").mkdir(parents=True)
    for name in ("samurai", "prompt_injection_guard.py", "secret_scrubber_realtime.py"):
        shutil.copy2(ROOT / "bin" / name, root / "bin" / name)
    shutil.copytree(ROOT / "agentica_core", root / "agentica_core",
                    ignore=shutil.ignore_patterns("__pycache__"))
    runtime_bin = tmp_path / "runtime-bin"
    runtime_bin.mkdir()
    env = {
        "HOME": str(home),
        "PATH": str(runtime_bin) + ":/usr/bin:/bin",
        "CODEX_HOME": str(tmp_path / "codex-state"),
        "SAMURAI_CODEX_APP_BIN": str(tmp_path / "absent-app" / "codex"),
        "SAMURAI_HOME": str(home / ".samurai"),
        "SAMURAI_ROOT": str(root),
        "SAMURAI_NO_PROMPT": "1",
    }
    for key in list(os.environ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    loader = SourceFileLoader("samurai_safe_writes", str(root / "bin" / "samurai"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, loader.name, module)
    loader.exec_module(module)
    return SimpleNamespace(
        cli=module, home=home, root=root, env=env, runtime_bin=runtime_bin,
        state=Path(env["SAMURAI_HOME"]),
        configs={"claude": home / ".claude" / SETTINGS_NAME,
                 "codex": Path(env["CODEX_HOME"]) / "hooks.json"},
    )


def _args(harness=None, keep_data=True):
    return argparse.Namespace(harness=harness, no_activate=True, keep_data=keep_data, force=False)


def _seed(box, harnesses):
    originals = {}
    for harness in harnesses:
        if harness == "codex":
            runtime = box.runtime_bin / "codex"
            runtime.write_text("#!/bin/sh\nexit 0\n")
            runtime.chmod(0o755)
        path = box.configs[harness]
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"user_setting": harness, "hooks": {
            "PreToolUse": [copy.deepcopy(USER_GROUP)],
            "PostToolUse": [copy.deepcopy(USER_GROUP)],
        }}
        path.write_text(json.dumps(data))
        originals[harness] = data
    return originals


def _definitions(harness, root, legacy=False):
    def group(matcher, script):
        return {"matcher": matcher, "hooks": [{
            "type": "command", "command": "python3 " + shlex.quote(str(root / "bin" / script)),
            "timeout": 5000 if legacy else 10,
        }]}
    if harness == "codex":
        return [("PreToolUse", group("^(Bash|apply_patch)$", "prompt_injection_guard.py"))]
    return [
        ("PreToolUse", group("Bash|Write|Edit|MultiEdit|WebFetch", "prompt_injection_guard.py")),
        ("PostToolUse", group("Bash|Read|WebFetch" if legacy else "Bash|Read", "secret_scrubber_realtime.py")),
    ]


def _manifest(box):
    path = box.state / "install.json"
    assert path.is_file(), "install did not write the required ownership manifest"
    return json.loads(path.read_text())


def _installed_config(box, harness, original):
    expected = copy.deepcopy(original)
    for event, group in _definitions(harness, box.root):
        expected["hooks"][event].append(group)
    return expected


def _assert_installed(box, originals):
    manifest = _manifest(box)
    assert set(manifest["harnesses"]) == set(originals)
    for harness, original in originals.items():
        entry = manifest["harnesses"][harness]
        assert entry["state"] == "installed"
        assert entry["config_path"] == str(box.configs[harness])
        expected = copy.deepcopy(original)
        definitions = _definitions(harness, box.root)
        assert len(entry["groups"]) == len(definitions)
        for event, group in definitions:
            index = len(expected["hooks"][event])
            expected["hooks"][event].append(group)
            records = [r for r in entry["groups"] if r["event"] == event]
            assert len(records) == 1
            assert records[0]["index"] == index
            assert records[0]["definition"] == group
        assert json.loads(box.configs[harness].read_text()) == expected


@pytest.mark.parametrize("existing", [False, True], ids=["new-0600", "existing-0640"])
def test_json_replace_uses_same_directory_temp_and_preserves_mode(box, tmp_path, monkeypatch, existing):
    """Case 8: atomic writes use unique same-directory temps, preserve mode and clean up."""
    path = tmp_path / "document.json"
    if existing:
        path.write_text('{"user": true}')
        path.chmod(0o640)
    before = set(tmp_path.iterdir())
    replace = os.replace
    sources = []

    def observe(source, target, *args, **kwargs):
        source, target = Path(source), Path(target)
        if target == path:
            assert source.parent == path.parent
            assert source != target and source.is_file() and not source.is_symlink()
            assert json.loads(source.read_text())["installed"] == len(sources) + 1
            assert stat.S_IMODE(source.stat().st_mode) == (0o640 if existing else 0o600)
            sources.append(source)
        return replace(source, target, *args, **kwargs)

    monkeypatch.setattr(os, "replace", observe)
    for number in (1, 2):
        assert box.cli._update_json_file(path, lambda data: {**data, "installed": number}) == "written"
    assert len(sources) == len(set(sources)) == 2
    assert json.loads(path.read_text()) == ({"user": True, "installed": 2} if existing else {"installed": 2})
    assert stat.S_IMODE(path.stat().st_mode) == (0o640 if existing else 0o600)
    assert set(tmp_path.iterdir()) == before | {path}


@pytest.mark.parametrize("kind", ["symlink", "directory"])
def test_json_update_refuses_unsafe_target(box, tmp_path, kind):
    """Case 8: a symlink or directory raises UnsafeFile without changing either target."""
    path = tmp_path / "unsafe.json"
    other = tmp_path / "other.json"
    other.write_bytes(b'{"user":"untouched"}')
    if kind == "symlink":
        path.symlink_to(other)
    else:
        path.mkdir()
        (path / "keep").write_bytes(b"keep")
    before = set(tmp_path.iterdir())
    with pytest.raises(box.cli.UnsafeFile):
        box.cli._update_json_file(path, lambda data: {"clobbered": True})
    assert other.read_bytes() == b'{"user":"untouched"}'
    if kind == "symlink":
        assert path.is_symlink() and path.readlink() == other
    else:
        assert (path / "keep").read_bytes() == b"keep"
    assert set(tmp_path.iterdir()) == before


def test_json_update_retries_once_and_preserves_concurrent_edit(box, tmp_path, monkeypatch):
    """Case 8: a changed file is reread and the mutation retried without losing user edits."""
    path = tmp_path / "race.json"
    path.write_text('{"user": "before"}')
    before = set(tmp_path.iterdir())
    reads = []
    checkpoints = []

    def edit(step, harness=None, path=None):
        if step == "before_replace" and path == target:
            checkpoints.append(step)
            if len(checkpoints) == 1:
                path.write_text('{"user": "concurrent", "keep": 7}')

    def mutate(data):
        reads.append(copy.deepcopy(data))
        return {**data, "installed": True}

    target = path
    monkeypatch.setattr(box.cli, "_checkpoint", edit)
    assert box.cli._update_json_file(path, mutate) == "written"
    assert reads == [{"user": "before"}, {"user": "concurrent", "keep": 7}]
    assert len(checkpoints) == 2
    assert json.loads(path.read_text()) == {"user": "concurrent", "keep": 7, "installed": True}
    assert set(tmp_path.iterdir()) == before


def test_json_update_stops_after_second_conflict(box, tmp_path, monkeypatch):
    """Case 8: two conflicting writes raise ConcurrentModification and retain the writer's bytes."""
    path = tmp_path / "race.json"
    path.write_text("{}")
    before = set(tmp_path.iterdir())
    writes = []

    def edit(step, harness=None, path=None):
        if step == "before_replace" and path == target:
            payload = ('{ "writer": %d }\n' % (len(writes) + 1)).encode()
            writes.append(payload)
            path.write_bytes(payload)

    target = path
    monkeypatch.setattr(box.cli, "_checkpoint", edit)
    with pytest.raises(box.cli.ConcurrentModification):
        box.cli._update_json_file(path, lambda data: {**data, "installed": True})
    assert len(writes) == 2
    assert path.read_bytes() == writes[-1]
    assert set(tmp_path.iterdir()) == before


def test_interrupted_json_replace_preserves_original_and_cleans_temp(box, tmp_path, monkeypatch):
    """Cases 8, 13: a crash before replacement leaves original bytes and no temporary file."""
    path = tmp_path / "interrupted.json"
    original = b'{ "user": "keep" }\n'
    path.write_bytes(original)
    before = set(tmp_path.iterdir())

    def crash(step, harness=None, path=None):
        if step == "before_replace":
            raise KeyboardInterrupt("before_replace")

    monkeypatch.setattr(box.cli, "_checkpoint", crash)
    with pytest.raises(KeyboardInterrupt, match="before_replace"):
        box.cli._update_json_file(path, lambda data: {**data, "installed": True})
    assert path.read_bytes() == original
    assert set(tmp_path.iterdir()) == before


def test_backups_with_same_label_in_same_second_do_not_collide(box, tmp_path, monkeypatch):
    """Case 8: equal labels and timestamps still produce two distinct backups."""
    class FrozenDateTime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 6, 12, 0, 0, tzinfo=tz)

    monkeypatch.setattr(datetime, "datetime", FrozenDateTime)
    path = tmp_path / "source.json"
    backups = box.state / "backups"
    path.write_bytes(b'{"version":1}')
    box.cli._backup(path, backups, "claude-settings")
    path.write_bytes(b'{"version":2}')
    box.cli._backup(path, backups, "claude-settings")
    files = list(backups.glob("claude-settings.bak.*"))
    assert len(files) == 2, "backup names collide within one second"
    assert {p.read_bytes() for p in files} == {b'{"version":1}', b'{"version":2}'}


def test_install_cannot_write_configs_while_state_lock_is_held(box):
    """Case 8: an external flock holder prevents every install config write."""
    originals = _seed(box, ("claude", "codex"))
    before = {h: p.read_bytes() for h, p in box.configs.items()}
    box.state.mkdir()
    with (box.state / ".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        child = subprocess.Popen(
            [sys.executable, str(box.root / "bin" / "samurai"), "install", "--no-activate"],
            env=box.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            try:
                stdout, stderr = child.communicate(timeout=0.6)
            except subprocess.TimeoutExpired:
                stdout = stderr = None
            assert {h: p.read_bytes() for h, p in box.configs.items()} == before
            assert not (box.state / SETTINGS_NAME).exists()
            assert not (box.state / "install.json").exists()
            if stdout is not None:
                assert child.returncode != 0
                assert "lock" in (stdout + stderr).lower()
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)
                stdout, stderr = child.communicate(timeout=2)
                assert child.returncode == 0, stdout + stderr
                _assert_installed(box, originals)
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=1)


@pytest.mark.parametrize("harness", ["claude", "codex"])
@pytest.mark.parametrize("step", ["manifest_pending", "config_written", "manifest_installed"])
def test_install_recovers_after_each_write_step(box, monkeypatch, harness, step):
    """Case 4: a single-harness install resumes each crash point without losing user hooks."""
    originals = _seed(box, (harness,))

    def crash(current, harness=None, path=None):
        if current == step:
            raise KeyboardInterrupt(step)

    monkeypatch.setattr(box.cli, "_checkpoint", crash)
    with pytest.raises(KeyboardInterrupt, match=step):
        box.cli.cmd_install(_args(harness))
    entry = _manifest(box)["harnesses"][harness]
    assert entry["state"] == ("installed" if step == "manifest_installed" else "pending")
    if step == "manifest_pending":
        assert json.loads(box.configs[harness].read_text()) == originals[harness]
    else:
        assert json.loads(box.configs[harness].read_text()) == _installed_config(box, harness, originals[harness])
    monkeypatch.setattr(box.cli, "_checkpoint", lambda *args, **kwargs: None)
    assert box.cli.cmd_install(_args(harness)) == 0
    _assert_installed(box, originals)
    assert box.cli.cmd_install(_args(harness)) == 0
    _assert_installed(box, originals)


def test_install_recovers_when_second_harness_is_pending(box, monkeypatch):
    """Case 4: a two-harness install resumes after one completed and the other became pending."""
    originals = _seed(box, ("claude", "codex"))
    completed = []
    pending = []

    def crash(step, harness=None, path=None):
        if step == "manifest_installed":
            completed.append(harness)
        if step == "manifest_pending" and completed:
            pending.append(harness)
            raise KeyboardInterrupt("second harness pending")

    monkeypatch.setattr(box.cli, "_checkpoint", crash)
    with pytest.raises(KeyboardInterrupt, match="second harness pending"):
        box.cli.cmd_install(_args("claude,codex"))
    assert len(completed) == len(pending) == 1
    assert completed[0] != pending[0]
    entries = _manifest(box)["harnesses"]
    assert entries[completed[0]]["state"] == "installed"
    assert entries[pending[0]]["state"] == "pending"
    assert json.loads(box.configs[completed[0]].read_text()) == _installed_config(box, completed[0], originals[completed[0]])
    assert json.loads(box.configs[pending[0]].read_text()) == originals[pending[0]]
    monkeypatch.setattr(box.cli, "_checkpoint", lambda *args, **kwargs: None)
    assert box.cli.cmd_install(_args("claude,codex")) == 0
    _assert_installed(box, originals)


@pytest.mark.parametrize("harness", ["claude", "codex"])
@pytest.mark.parametrize("step", ["manifest_removing", "config_removed"])
def test_uninstall_recovers_after_each_write_step(box, monkeypatch, harness, step):
    """Case 4: interrupted removal finishes without leaving removing entries or losing user hooks."""
    originals = _seed(box, (harness,))

    def crash(current, harness=None, path=None):
        if current == step:
            raise KeyboardInterrupt(step)

    monkeypatch.setattr(box.cli, "_checkpoint", crash)
    assert box.cli.cmd_install(_args(harness)) == 0
    _assert_installed(box, originals)
    with pytest.raises(KeyboardInterrupt, match=step):
        box.cli.cmd_uninstall(_args())
    assert _manifest(box)["harnesses"][harness]["state"] == "removing"
    if step == "config_removed":
        assert json.loads(box.configs[harness].read_text()) == originals[harness]
    else:
        assert json.loads(box.configs[harness].read_text()) == _installed_config(box, harness, originals[harness])
    monkeypatch.setattr(box.cli, "_checkpoint", lambda *args, **kwargs: None)
    assert box.cli.cmd_uninstall(_args()) == 0
    assert json.loads(box.configs[harness].read_text()) == originals[harness]
    manifest = box.state / "install.json"
    assert not manifest.exists() or not json.loads(manifest.read_text())["harnesses"]
    assert box.cli.cmd_uninstall(_args()) == 0
    assert json.loads(box.configs[harness].read_text()) == originals[harness]


def _seed_legacy(box):
    originals = _seed(box, ("claude",))
    old_root = box.state / "core"
    (old_root / "bin").mkdir(parents=True)
    for script in ("prompt_injection_guard.py", "secret_scrubber_realtime.py"):
        shutil.copy2(box.root / "bin" / script, old_root / "bin" / script)
    legacy = _definitions("claude", old_root, legacy=True)
    data = copy.deepcopy(originals["claude"])
    for event, group in legacy:
        data["hooks"][event].append(group)
    box.configs["claude"].write_text(json.dumps(data))
    assert not (box.state / "install.json").exists()
    return originals, legacy, data


def test_install_adopts_exact_legacy_groups_from_previous_root(box):
    """Case 4: exact v2.1.x hooks under SAMURAI_HOME/core upgrade in place after a root change."""
    originals, legacy, _ = _seed_legacy(box)
    assert box.cli.cmd_install(_args("claude")) == 0
    _assert_installed(box, originals)
    records = _manifest(box)["harnesses"]["claude"]["groups"]
    for event, group in legacy:
        record = next(r for r in records if r["event"] == event)
        assert record["index"] == 1
        assert group in record["previous"]


def test_uninstall_removes_exact_legacy_groups_without_manifest(box):
    """Case 4: uninstall recognizes exact legacy hooks at a previous root without a manifest."""
    originals, _, _ = _seed_legacy(box)
    assert box.cli.cmd_uninstall(_args()) == 0
    assert json.loads(box.configs["claude"].read_text()) == originals["claude"]


@pytest.mark.parametrize("operation", ["install", "uninstall"])
@pytest.mark.parametrize("change", ["extra-key", "extra-handler", "edited-matcher", "outside-root"])
def test_legacy_lookalikes_are_neither_adopted_nor_deleted(box, operation, change):
    """Case 4: extra keys, mixed handlers, edited matchers and foreign roots are not owned."""
    _, legacy, data = _seed_legacy(box)
    for event, group in legacy:
        if change == "extra-key":
            group["user_note"] = "keep me"
        elif change == "extra-handler":
            group["hooks"].append(copy.deepcopy(USER_GROUP["hooks"][0]))
        elif change == "edited-matcher":
            group["matcher"] += "|UserTool"
        else:
            command = shlex.split(group["hooks"][0]["command"])
            group["hooks"][0]["command"] = "python3 " + shlex.quote(str(box.home / "foreign" / Path(command[-1]).name))
    box.configs["claude"].write_text(json.dumps(data))
    before = copy.deepcopy(data)
    result = getattr(box.cli, "cmd_" + operation)(_args("claude" if operation == "install" else None))
    assert result in (0, 1)
    after = json.loads(box.configs["claude"].read_text())
    assert after["user_setting"] == before["user_setting"]
    for event, groups in before["hooks"].items():
        assert after["hooks"][event][:len(groups)] == groups
    manifest = box.state / "install.json"
    if manifest.exists():
        for entry in json.loads(manifest.read_text())["harnesses"].values():
            for record in entry["groups"]:
                for _, group in legacy:
                    assert record["definition"] != group
                    assert group not in record["previous"]


@pytest.mark.parametrize("operation", ["install", "uninstall"])
def test_claude_marker_substring_is_never_ownership(box, operation):
    """Case 4: echo prompt_injection_guard is a user hook during install and uninstall."""
    originals = _seed(box, ("claude",))
    lookalike = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo prompt_injection_guard"}]}
    if operation == "install":
        data = originals["claude"]
    else:
        assert box.cli.cmd_install(_args("claude")) == 0
        data = json.loads(box.configs["claude"].read_text())
    data["hooks"]["PreToolUse"].insert(0, lookalike)
    box.configs["claude"].write_text(json.dumps(data))
    assert getattr(box.cli, "cmd_" + operation)(_args("claude" if operation == "install" else None)) == 0
    after = json.loads(box.configs["claude"].read_text())
    assert after["hooks"]["PreToolUse"][0] == lookalike
    assert after["hooks"]["PreToolUse"].count(lookalike) == 1
    assert USER_GROUP in after["hooks"]["PreToolUse"]


def test_install_backs_up_each_existing_config_before_replacement(box, monkeypatch):
    """Case 13: both harness configs have exact-byte backups before their atomic replacements."""
    originals = _seed(box, ("claude", "codex"))
    before = {path: path.read_bytes() for path in box.configs.values()}
    seen = set()

    def check_backup(step, harness=None, path=None):
        if step == "before_replace" and path in before:
            backups = list((box.state / "backups").glob("*.bak.*"))
            assert any(p.read_bytes() == before[path] for p in backups), f"no prior backup for {path}"
            assert path.read_bytes() == before[path]
            seen.add(path)

    monkeypatch.setattr(box.cli, "_checkpoint", check_backup)
    assert box.cli.cmd_install(_args("claude,codex")) == 0
    assert seen == set(before)
    _assert_installed(box, originals)
