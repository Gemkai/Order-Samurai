"""Acceptance contract for the standalone CLI's supported Python runtime."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from argparse import Namespace
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest


SAMURAI_PATH = Path(__file__).resolve().parents[1] / "bin" / "samurai"


@pytest.fixture
def samurai_cli(tmp_path, monkeypatch):
    root = tmp_path / "order-samurai"
    for directory in ("bin", "agentica_core"):
        shutil.copytree(
            SAMURAI_PATH.parent.parent / directory,
            root / directory,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    taxonomy = root / "state" / "kill_chain_taxonomy.json"
    taxonomy.parent.mkdir(parents=True)
    taxonomy.write_text(
        json.dumps({"chains": [{"id": index} for index in range(14)]}),
        encoding="utf-8",
    )
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    runtime_bin = tmp_path / "runtime-bin"
    runtime_bin.mkdir()
    env = {
        "HOME": str(home),
        "PATH": str(runtime_bin),
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

    loader = SourceFileLoader("samurai_python_requirement", str(root / "bin" / "samurai"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, loader.name, module)
    loader.exec_module(module)
    assert module.cmd_install(Namespace(harness="claude", no_activate=True)) == 0
    return module


@pytest.mark.parametrize(
    ("version_info", "expected_code", "expected_badge"),
    [
        ((3, 10, 14), 1, "[❌ FAIL] Python Version"),
        ((3, 11, 0), 0, "[✅ PASS] Python Version"),
        ((3, 13, 7), 0, "[✅ PASS] Python Version"),
    ],
)
def test_doctor_enforces_python_311_minimum(
    samurai_cli, monkeypatch, capsys, version_info, expected_code, expected_badge
):
    """Cases 4 and 11: a healthy recorded install requires Python 3.11 or newer."""
    monkeypatch.setattr(samurai_cli.sys, "version_info", version_info)
    monkeypatch.setattr(
        samurai_cli.sys, "version", ".".join(str(part) for part in version_info)
    )
    assert samurai_cli.cmd_doctor(None) == expected_code
    output = capsys.readouterr().out
    assert expected_badge in output
    assert ">= 3.11 required" in output
