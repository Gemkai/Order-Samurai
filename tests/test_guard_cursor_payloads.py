"""The guard understands Cursor hook payloads and answers in Cursor's block format.

Cursor treats exit code 2 as a block and expects stdout to be empty or valid JSON:
invalid JSON from a permission hook blocks the action, and any exit code other than
0 or 2 lets the action through unless the hook entry sets failClosed."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "cursor_payloads"
INJ = "jail" + "break"


def _fixture(name, **changes):
    return {**json.loads((FIXTURES / f"{name}.json").read_text()), **changes}


@pytest.fixture
def run_guard(tmp_path):
    root = tmp_path / "root"
    (root / "bin").mkdir(parents=True)
    script = root / "bin" / "prompt_injection_guard.py"
    shutil.copyfile(REPO / "bin" / script.name, script)
    env = {
        "HOME": str(tmp_path / "home"),
        "PATH": str(tmp_path),
        "SAMURAI_ROOT": str(root),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PIG_LMSTUDIO_URL": "http://127.0.0.1:1/v1/chat/completions",
    }

    def run(payload):
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.run([sys.executable, str(script)], input=raw, text=True,
                              capture_output=True, cwd=root, env=env, timeout=10)

    return run


def _verdict(result):
    assert result.stdout.strip(), "Cursor payloads need an explicit JSON verdict on stdout"
    return json.loads(result.stdout)


def test_benign_shell_command_allowed_with_json_verdict(run_guard):
    result = run_guard(_fixture("before_shell_execution"))
    assert result.returncode == 0, result.stderr
    assert _verdict(result) == {"permission": "allow"}


def test_shell_command_with_injection_is_blocked_in_cursor_format(run_guard):
    result = run_guard(_fixture("before_shell_execution", command=f"echo {INJ}"))
    assert result.returncode == 2, result.stderr
    verdict = _verdict(result)
    assert verdict["permission"] == "deny"
    assert verdict["user_message"].strip() and verdict["agent_message"].strip()
    assert result.stderr.strip(), "blocking must also explain itself on stderr"


def test_mcp_tool_input_json_string_is_scanned(run_guard):
    payload = _fixture("before_mcp_execution", tool_input=json.dumps({"query": INJ}))
    result = run_guard(payload)
    assert result.returncode == 2, result.stderr
    assert _verdict(result)["permission"] == "deny"


def test_benign_mcp_call_allowed(run_guard):
    result = run_guard(_fixture("before_mcp_execution"))
    assert result.returncode == 0, result.stderr
    assert _verdict(result) == {"permission": "allow"}


def test_pre_tool_use_write_contents_are_scanned(run_guard):
    payload = _fixture("pre_tool_use_write")
    payload["tool_input"] = {**payload["tool_input"], "contents": INJ}
    result = run_guard(payload)
    assert result.returncode == 2, result.stderr
    assert _verdict(result)["permission"] == "deny"
    assert _verdict(run_guard(_fixture("pre_tool_use_write"))) == {"permission": "allow"}


def test_cursor_payload_without_scannable_text_still_gets_a_verdict(run_guard):
    payload = _fixture("before_shell_execution")
    del payload["command"]
    result = run_guard(payload)
    assert result.returncode == 0, result.stderr
    assert _verdict(result) == {"permission": "allow"}


def test_internal_error_fails_closed_for_cursor(run_guard, tmp_path):
    """A guard crash must block, not fall through Cursor's fail-open exit codes."""
    script = tmp_path / "root" / "bin" / "prompt_injection_guard.py"
    source = script.read_text().replace("def evaluate_input(", "def _boom(*a):\n    raise RuntimeError('x')\n\n"
                                        "def evaluate_input(", 1)
    source = source.replace("    confidence, detail = evaluate_input(input_str)", "    confidence, detail = _boom(input_str)")
    assert "_boom(input_str)" in source
    script.write_text(source)
    result = run_guard(_fixture("before_shell_execution"))
    assert result.returncode == 2, result.stderr
    assert _verdict(result)["permission"] == "deny"


def test_claude_and_codex_payloads_keep_silent_stdout(run_guard):
    """Claude and Codex ignore or reject unexpected stdout: leave them untouched."""
    for payload, code in (
        ({"tool_name": "Bash", "tool_input": {"command": "true"}}, 0),
        ({"hook_event_name": "PreToolUse", "session_id": "s", "tool_name": "Bash",
          "tool_input": {"command": INJ}}, 2),
    ):
        result = run_guard(payload)
        assert (result.returncode, result.stdout) == (code, ""), result.stderr
