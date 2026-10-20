"""Customer-visible acceptance for harness-aware guidance and local session reports."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
LABELS = {"claude": "Claude Code", "codex": "Codex", "cursor": "Cursor", "gemini": "Gemini CLI"}
HINTS = {
    "claude": "Restart Claude Code so it loads the security hooks.",
    "codex": "Restart Codex, then run /hooks in Codex and approve the Order Samurai hooks (Codex asks once before it runs new hooks).",
    "cursor": "Restart Cursor so it loads the security hooks.",
    "gemini": "Restart Gemini CLI so it loads the security hooks.",
}


@pytest.fixture
def machine(tmp_path, monkeypatch):
    home, root, path = tmp_path / "home", tmp_path / "core", tmp_path / "path"
    home.mkdir()
    path.mkdir()
    for directory in ("bin", "agentica_core", "config"):
        shutil.copytree(ROOT / directory, root / directory,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (root / "state").mkdir()
    shutil.copy2(ROOT / "state" / "kill_chain_taxonomy.json", root / "state")
    (path / "python3").symlink_to(sys.executable)
    env = {
        "HOME": str(home), "PATH": str(path), "CODEX_HOME": str(home / "custom-codex"),
        "SAMURAI_HOME": str(home / ".samurai"), "SAMURAI_ROOT": str(root),
        "SAMURAI_REPORTS_DIR": str(tmp_path / "reports"), "SAMURAI_NO_PROMPT": "1",
        "SAMURAI_NO_UPDATE_CHECK": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "SAMURAI_CODEX_APP_BIN": str(tmp_path / "absent-app" / "codex"),
        "SAMURAI_APPLICATIONS_DIR": str(tmp_path / "Applications"),
    }
    for key in tuple(os.environ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return SimpleNamespace(home=home, root=root, path=path, env=env, cwd=tmp_path,
                           claude=home / ".claude" / "projects",
                           codex=Path(env["CODEX_HOME"]) / "sessions",
                           reports=tmp_path / "reports")


def _runtime(machine, harness):
    executable = machine.path / harness
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)


def _run(machine, *args, first_blood=False):
    script = "first_blood.py" if first_blood else "samurai"
    result = subprocess.run([sys.executable, str(machine.root / "bin" / script), *args],
                            cwd=machine.cwd, env=machine.env, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode in (0, 1), result.stdout + result.stderr
    return result


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(row if isinstance(row, str) else json.dumps(row) for row in rows) + "\n")


def _claude(directory, name="claude-session"):
    _write(directory / "-Users-buyer-shop" / f"{name}.jsonl", [
        {"type": "assistant", "timestamp": "2026-10-19T12:00:00Z", "message": {
            "model": "claude-sonnet-4", "usage": {"input_tokens": 1000000, "output_tokens": 1000000},
            "content": [{"type": "tool_use", "name": "Bash"}]}}
    ])


def _tokens(input_tokens, cached, output):
    return {"type": "event_msg", "payload": {"type": "token_count", "info": {
        "total_token_usage": {"input_tokens": input_tokens, "cached_input_tokens": cached,
                              "output_tokens": output, "reasoning_output_tokens": 7,
                              "total_tokens": input_tokens + output},
        "last_token_usage": {"input_tokens": 999999, "output_tokens": 999999}}}}


def _codex(directory, name="codex-session", tokens=True, malformed=False):
    rows = [
        {"type": "session_meta", "payload": {"id": name, "session_id": name,
          "timestamp": "2026-10-19T12:00:00Z", "cwd": "/projects/codex-project"}},
        {"type": "turn_context", "payload": {"model": "gpt-6-astra", "cwd": "/projects/codex-project"}},
        {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command"}},
        {"type": "turn_context", "payload": {"model": "gpt-6-sol"}},
        {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "apply_patch"}},
        {"type": "response_item", "payload": {"type": "function_call_output", "output": "ok"}},
    ]
    if tokens:
        rows += [_tokens(11, 3, 5), _tokens(321, 123, 45)]
    if malformed:
        rows += ["not JSON", "{\"type\":", [], None,
                 {"type": "event_msg", "payload": {"type": "token_count", "info": None}}]
    _write(directory / "2026" / "10" / "19" / f"rollout-{name}.jsonl", rows)


def _only_harnesses(text, harnesses):
    for harness, label in LABELS.items():
        assert (label in text) == (harness in harnesses), text


def _number(text, label, value):
    """Accept either prose or table metrics, with ordinary numeric separators."""
    lines = [line.replace(",", "") for line in text.splitlines() if label in line.lower()]
    assert any(re.search(rf"(?<!\d){value}(?!\d)", line) for line in lines), text


@pytest.mark.parametrize("harnesses", [("codex",), ("claude",), ("claude", "codex")])
def test_install_doctor_and_restart_use_the_actual_harnesses(machine, harnesses):
    for harness in harnesses:
        _runtime(machine, harness)
    installed = _run(machine, "install")
    assert installed.returncode == 0, installed.stdout
    _only_harnesses(installed.stdout, harnesses)
    for harness in harnesses:
        assert HINTS[harness] in installed.stdout
    doctor = _run(machine, "doctor")
    _only_harnesses(doctor.stdout, harnesses)
    hint = _run(machine, "restart-hint")
    assert hint.returncode == 0
    assert hint.stdout.splitlines() == [HINTS[h] for h in harnesses]


@pytest.mark.parametrize("harness", ["claude", "codex", "cursor", "gemini"])
def test_restart_hint_falls_back_to_detected_runtime(machine, harness):
    _runtime(machine, harness)
    result = _run(machine, "restart-hint")
    assert result.returncode == 0
    assert result.stdout == HINTS[harness] + "\n"


def test_restart_hint_prefers_installed_manifest_over_detection(machine):
    _runtime(machine, "claude")
    manifest = machine.home / ".samurai" / "install.json"
    manifest.parent.mkdir()
    manifest.write_text(json.dumps({"version": 1, "harnesses": {
        "codex": {"state": "installed", "config_path": str(machine.codex.parent / "hooks.json"), "groups": []},
        "gemini": {"state": "pending", "config_path": "unused", "groups": []}}}))
    result = _run(machine, "restart-hint")
    assert result.returncode == 0
    assert result.stdout == HINTS["codex"] + "\n"


def test_restart_hint_without_any_known_harness_is_plain_generic_text(machine):
    result = _run(machine, "restart-hint")
    assert result.returncode == 0
    assert result.stdout == "Restart your coding agent (Claude Code, Codex, Cursor or Gemini CLI) so it loads the security hooks.\n"


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_no_logs_messages_name_only_the_detected_harness(machine, harness):
    _runtime(machine, harness)
    first = _run(machine, first_blood=True)
    assert first.returncode == 0
    assert first.stdout.startswith("Order Samurai -- ")
    _only_harnesses(first.stdout, (harness,))
    audit = _run(machine, "audit")
    body = next(machine.reports.glob("audit__*.md")).read_text()
    _only_harnesses(audit.stdout + body, (harness,))
    assert "no" in body.lower() and "session logs" in body.lower()


@pytest.mark.parametrize("harnesses", [("codex",), ("claude",), ("claude", "codex")])
def test_first_report_counts_harnesses_without_inventing_codex_cost(machine, harnesses):
    for harness in harnesses:
        _runtime(machine, harness)
    if "claude" in harnesses:
        _claude(machine.claude)
    if "codex" in harnesses:
        _codex(machine.codex)
    result = _run(machine, first_blood=True)
    assert result.returncode == 0
    assert result.stdout.startswith("Order Samurai -- ")
    _only_harnesses(result.stdout, harnesses)
    data = json.loads(_run(machine, "--json", first_blood=True).stdout)
    assert data["sessions_scanned"] == len(harnesses)
    assert data["total_cost"] == (18.0 if "claude" in harnesses else None)
    assert len(data["spend_spikes"]) == (1 if "claude" in harnesses else 0)
    if "codex" in harnesses:
        assert "unavailable" in result.stdout.lower()
        _number(result.stdout, "input", 321)
        _number(result.stdout, "cached", 123)
        _number(result.stdout, "output", 45)
        _number(result.stdout, "tool", 2)
        assert all(s["session_id"] != "codex-session" for s in data["spend_spikes"])
    if harnesses == ("codex",):
        assert "$" not in result.stdout


@pytest.mark.parametrize("harnesses", [("codex",), ("claude",), ("claude", "codex")])
def test_audit_reports_sessions_and_per_harness_counts(machine, harnesses):
    for harness in harnesses:
        _runtime(machine, harness)
    if "claude" in harnesses:
        _claude(machine.claude)
    if "codex" in harnesses:
        _codex(machine.codex)
    result = _run(machine, "audit")
    _number(result.stdout, "sessions scanned", len(harnesses))
    _only_harnesses(result.stdout, harnesses)
    for harness in harnesses:
        _number(result.stdout, LABELS[harness].lower(), 1)
    body = next(machine.reports.glob("audit__*.md")).read_text()
    _only_harnesses(body, harnesses)
    if "codex" in harnesses:
        assert re.search(r"^#{2,4} .*Codex", body, re.MULTILINE), body
        assert "unavailable" in body.lower()
        _number(body, "input", 321)
        _number(body, "cached", 123)
        _number(body, "output", 45)
        _number(body, "tool", 2)
    if harnesses == ("codex",):
        assert "$" not in body


def test_codex_parser_uses_last_cumulative_totals_and_skips_bad_lines(machine):
    _runtime(machine, "codex")
    _codex(machine.codex, malformed=True)
    result = _run(machine, first_blood=True)
    assert result.returncode == 0
    _number(result.stdout, "input", 321)
    _number(result.stdout, "cached", 123)
    _number(result.stdout, "output", 45)
    _number(result.stdout, "tool", 2)
    data = json.loads(_run(machine, "--json", first_blood=True).stdout)
    assert data["sessions_scanned"] == 1
    assert data["total_cost"] is None


def test_codex_session_without_token_events_still_counts_session_and_tools(machine):
    _runtime(machine, "codex")
    _codex(machine.codex, tokens=False)
    result = _run(machine, first_blood=True)
    assert result.returncode == 0
    _number(result.stdout, "tool", 2)
    assert "unavailable" in result.stdout.lower() or "not recorded" in result.stdout.lower()
    data = json.loads(_run(machine, "--json", first_blood=True).stdout)
    assert data["sessions_scanned"] == 1
    assert data["total_cost"] is None
    assert data["spend_spikes"] == []


def test_explicit_log_directories_override_defaults_independently(machine):
    _claude(machine.claude, "ignored-claude")
    _codex(machine.codex, "ignored-codex")
    explicit_claude, explicit_codex = machine.cwd / "claude-logs", machine.cwd / "codex-logs"
    _claude(explicit_claude)
    _codex(explicit_codex)
    _codex(explicit_codex, "second-codex")
    args = ("--logs-dir", str(explicit_claude), "--codex-dir", str(explicit_codex))
    data = json.loads(_run(machine, *args, "--json", first_blood=True).stdout)
    assert data["sessions_scanned"] == 3
    assert data["total_cost"] == 18.0
    assert len(data["spend_spikes"]) == 1
    result = _run(machine, "audit", *args)
    _number(result.stdout, "sessions scanned", 3)
    body = next(machine.reports.glob("audit__*.md")).read_text()
    assert "ignored-claude" not in body
    assert "ignored-codex" not in body


def test_forked_codex_rollouts_keep_their_own_session_id(machine):
    # A forked/subagent rollout carries the parent's session_id and a copied parent
    # session_meta; its own thread id is payload.id of the FIRST session_meta.
    _runtime(machine, "codex")
    _codex(machine.codex, "parent")
    _write(machine.codex / "2026" / "10" / "19" / "rollout-child.jsonl", [
        {"type": "session_meta", "payload": {"id": "child", "session_id": "parent", "cwd": "/p"}},
        {"type": "session_meta", "payload": {"id": "parent", "session_id": "parent", "cwd": "/p"}},
        {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command"}},
        _tokens(1000, 10, 100),
    ])
    data = json.loads(_run(machine, "--json", first_blood=True).stdout)
    codex = data["harnesses"]["codex"]
    assert codex["sessions_scanned"] == 2
    assert codex["tokens_input"] == 321 + 1000 and codex["tokens_output"] == 45 + 100
    assert data["token_spend"] == codex["tokens_input"] + codex["tokens_output"]


@pytest.mark.parametrize("harness", ["cursor", "gemini"])
def test_harnesses_without_a_log_reader_are_not_reported_as_measured(machine, harness):
    _runtime(machine, harness)
    _runtime(machine, "codex")
    _codex(machine.codex)
    data = json.loads(_run(machine, "--json", first_blood=True).stdout)
    assert set(data["harnesses"]) == {"codex"}
    text = _run(machine, first_blood=True).stdout
    assert LABELS[harness] not in text
    empty = _run(machine, "--codex-dir", str(machine.cwd / "none"), first_blood=True).stdout
    assert f"No {LABELS[harness]} session logs found" not in empty


def test_claude_text_report_keeps_token_spend_and_density_lines(machine):
    _runtime(machine, "claude")
    _claude(machine.claude)
    text = _run(machine, first_blood=True).stdout
    assert re.search(r"token spend:\s+2000000", text), text
    assert "token execution density:" in text
