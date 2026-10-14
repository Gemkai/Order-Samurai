"""License backups are private at creation and never follow planted symlinks."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LICENSE = json.dumps({
    "license_key": "DUMMY-KEY-0000", "customer_email": "dummy@example.invalid",
}).encode("utf-8")


@pytest.fixture
def uninstall_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    state = home / ".samurai"
    state.mkdir(parents=True)
    monkeypatch.setattr(os, "environ", {"HOME": str(home), "SAMURAI_HOME": str(state)})
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    loader = SourceFileLoader("samurai_cli_backup_mode", str(ROOT / "bin" / "samurai"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    cli = importlib.util.module_from_spec(spec)
    loader.exec_module(cli)
    old_umask = os.umask(0o022)
    try:
        fd = os.open(state / "license.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as license_file:
            license_file.write(LICENSE)
        yield cli, cli.get_paths(), home / "order-samurai-license-backup.json"
    finally:
        os.umask(old_umask)


def _assert_backup(backup):
    assert backup.is_file()
    assert not backup.is_symlink()
    assert backup.read_bytes() == LICENSE
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600


def test_backup_is_private_at_creation_and_state_removed(uninstall_env, monkeypatch):
    cli, paths, backup = uninstall_env
    # No post-creation permission tightening can conceal an unsafe creation mode.
    for name in ("chmod", "fchmod", "lchmod"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, lambda *a, **k: None)
    monkeypatch.setattr(shutil, "copystat", lambda *a, **k: None)
    monkeypatch.setattr(shutil, "copymode", lambda *a, **k: None)
    assert cli._remove_state(paths, keep_data=False) == 0
    assert not paths["home"].exists()
    _assert_backup(backup)


def test_existing_private_backup_is_replaced(uninstall_env):
    cli, paths, backup = uninstall_env
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as existing:
        existing.write(b"old license content")
    assert cli._remove_state(paths, keep_data=False) == 0
    _assert_backup(backup)


def test_backup_symlink_does_not_overwrite_victim(uninstall_env):
    cli, paths, backup = uninstall_env
    victim = backup.parent / "victim.txt"
    victim.write_text("untouched", encoding="utf-8")
    backup.symlink_to(victim)
    assert cli._remove_state(paths, keep_data=False) == 0
    assert victim.read_text() == "untouched"
    _assert_backup(backup)
