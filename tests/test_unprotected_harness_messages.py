"""Unsupported-agent warnings must not reintroduce unrelated harness names."""
import pytest

from test_harness_aware_reports import machine, _runtime, _run


@pytest.mark.parametrize("command", ["install", "doctor"])
def test_codex_with_goose_names_only_present_agents(machine, command):
    _runtime(machine, "codex")
    _runtime(machine, "goose")
    if command == "doctor":
        installed = _run(machine, "install")
        assert installed.returncode == 0, installed.stdout
    result = _run(machine, command)
    assert result.returncode == 0, result.stdout
    assert "Codex" in result.stdout
    assert "Goose" in result.stdout
    assert "not protected by Order Samurai" in result.stdout
    assert "Claude Code" not in result.stdout
    assert "Cursor" not in result.stdout
    assert "Gemini CLI" not in result.stdout
