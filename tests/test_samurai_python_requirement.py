"""Acceptance contract for the standalone CLI's supported Python runtime."""

from __future__ import annotations

import importlib.util
import json
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest


SAMURAI_PATH = Path(__file__).resolve().parents[1] / "bin" / "samurai"
LOADER = SourceFileLoader("samurai_python_requirement", str(SAMURAI_PATH))
SPEC = importlib.util.spec_from_loader("samurai_python_requirement", LOADER)
assert SPEC
samurai_cli = importlib.util.module_from_spec(SPEC)
sys.modules["samurai_python_requirement"] = samurai_cli
LOADER.exec_module(samurai_cli)


def healthy_paths(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "order-samurai"
    taxonomy = root / "state" / "kill_chain_taxonomy.json"
    taxonomy.parent.mkdir(parents=True)
    taxonomy.write_text(
        json.dumps({"chains": [{"id": index} for index in range(14)]}),
        encoding="utf-8",
    )
    (root / "agentica_core").mkdir()

    samurai_home = tmp_path / ".samurai"
    claude_settings = tmp_path / ".claude" / "settings.json"
    claude_settings.parent.mkdir(parents=True)
    claude_settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python3 hooks/prompt_injection_guard.py",
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    return {
        "root": root,
        "home": samurai_home,
        "samurai_settings": samurai_home / "settings.json",
        "claude_hooks": tmp_path / ".claude" / "hooks",
        "claude_settings": claude_settings,
        "backups": samurai_home / "backups",
        "state": root / "state",
        "taxonomy": taxonomy,
    }


@pytest.mark.parametrize(
    ("version_info", "expected_code", "expected_badge"),
    [
        ((3, 10, 14), 1, "[❌ FAIL] Python Version"),
        ((3, 11, 0), 0, "[✅ PASS] Python Version"),
        ((3, 13, 7), 0, "[✅ PASS] Python Version"),
    ],
)
def test_doctor_enforces_python_311_minimum(
    tmp_path, monkeypatch, capsys, version_info, expected_code, expected_badge
):
    monkeypatch.setattr(samurai_cli, "get_paths", lambda: healthy_paths(tmp_path))
    monkeypatch.setattr(samurai_cli.sys, "version_info", version_info)
    monkeypatch.setattr(
        samurai_cli.sys, "version", ".".join(str(part) for part in version_info)
    )
    monkeypatch.delenv("BUSHIDO_FAIL_OPEN", raising=False)

    assert samurai_cli.cmd_doctor(None) == expected_code
    output = capsys.readouterr().out
    assert expected_badge in output
    assert ">= 3.11 required" in output
