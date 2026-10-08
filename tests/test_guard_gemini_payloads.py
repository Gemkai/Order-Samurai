"""The guard understands Gemini CLI BeforeTool payloads and answers in Gemini's format.

Gemini CLI parses stdout as JSON on exit 0 and treats anything else on stdout as a
broken hook; exit code 2 blocks (stderr is the reason); every other non-zero exit code
is only a warning and the tool call proceeds. So every outcome for a Gemini payload is
an explicit verdict: pure JSON on stdout, exit 2 to block, and exit 2 (never 1) when
the guard itself fails."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "gemini_payloads"
INJ = "jail" + "break"


def _fixture(name, **changes):
    return {**json.loads((FIXTURES / f"{name}.json").read_text()), **changes}


def _input(name, **changes):
    payload = _fixture(name)
    return dict(payload, tool_input={**payload["tool_input"], **changes})


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

    def run(payload, *args):
        raw = payload if isinstance(payload, (str, bytes)) else json.dumps(payload)
        result = subprocess.run([sys.executable, str(script), *args], input=raw.encode() if isinstance(raw, str) else raw,
                                capture_output=True, cwd=root, env=env, timeout=10)
        return subprocess.CompletedProcess(result.args, result.returncode, result.stdout.decode(),
                                           result.stderr.decode())

    return run


def _verdict(result):
    """stdout must be exactly one JSON object and nothing else."""
    assert result.stdout.strip(), "Gemini payloads need a JSON document on stdout"
    verdict = json.loads(result.stdout)
    assert isinstance(verdict, dict)
    return verdict


def _assert_denied(result):
    assert result.returncode == 2, result.stderr
    verdict = _verdict(result)
    assert verdict["decision"] == "deny"
    assert verdict["reason"].strip() and verdict["systemMessage"].strip()
    assert result.stderr.strip(), "exit 2 uses stderr as the rejection reason"


def _assert_not_blocked(result):
    assert result.returncode == 0, result.stderr
    assert _verdict(result).get("decision") not in ("deny", "block")


@pytest.mark.parametrize("name", ["before_tool_run_shell_command", "before_tool_write_file",
                                  "before_tool_replace", "before_tool_mcp"])
def test_benign_calls_get_pure_json_and_exit_zero(run_guard, name):
    result = run_guard(_fixture(name))
    _assert_not_blocked(result)
    assert json.loads(result.stdout) == {}, "benign calls must not carry a decision that could skip approval"


@pytest.mark.parametrize("name,key", [
    ("before_tool_run_shell_command", "command"),
    ("before_tool_write_file", "content"),
    ("before_tool_replace", "new_string"),
    ("before_tool_mcp", "query"),
])
def test_injection_in_any_tool_input_is_blocked_in_gemini_format(run_guard, name, key):
    _assert_denied(run_guard(_input(name, **{key: f"echo {INJ}"})))


def test_injection_in_an_unexpected_argument_is_still_scanned(run_guard):
    _assert_denied(run_guard(_input("before_tool_run_shell_command", description=INJ)))


def test_mcp_arguments_arriving_as_a_json_string_are_scanned_raw_and_decoded(run_guard):
    payload = _fixture("before_tool_mcp", tool_input=json.dumps({"query": INJ}))
    _assert_denied(run_guard(payload))
    escaped = json.dumps({"query": INJ}).replace(INJ, f"\\u{ord(INJ[0]):04x}{INJ[1:]}")
    assert INJ not in escaped and json.loads(escaped)["query"] == INJ
    _assert_denied(run_guard(_fixture("before_tool_mcp", tool_input=escaped)))


def test_json_string_nested_inside_an_argument_is_decoded(run_guard):
    escaped = json.dumps({"q": INJ}).replace(INJ, f"\\u{ord(INJ[0]):04x}{INJ[1:]}")
    _assert_denied(run_guard(_input("before_tool_mcp", payload=escaped)))


def test_injection_nested_in_lists_and_objects_is_scanned(run_guard):
    _assert_denied(run_guard(_input("before_tool_mcp", filters=[{"deep": {"text": INJ}}])))


def test_benign_escaped_mcp_input_still_allowed(run_guard):
    raw = json.dumps({"query": "café menu"})
    assert "\\u00e9" in raw
    _assert_not_blocked(run_guard(_fixture("before_tool_mcp", tool_input=raw)))


def test_non_json_string_tool_input_is_scanned_raw(run_guard):
    _assert_denied(run_guard(_fixture("before_tool_mcp", tool_input="not json " + INJ)))


def test_payload_without_scannable_text_still_gets_a_verdict(run_guard):
    result = run_guard(_fixture("before_tool_mcp", tool_input={}))
    _assert_not_blocked(result)
    assert json.loads(result.stdout) == {}


def test_internal_error_fails_closed_for_gemini(run_guard, tmp_path):
    """A guard crash must block: Gemini lets the call through on any other exit code."""
    script = tmp_path / "root" / "bin" / "prompt_injection_guard.py"
    source = script.read_text().replace("def evaluate_input(", "def _boom(*a):\n    raise RuntimeError('x')\n\n"
                                        "def evaluate_input(", 1)
    source = source.replace("    confidence, detail = evaluate_input(input_str)", "    confidence, detail = _boom(input_str)")
    assert "_boom(input_str)" in source
    script.write_text(source)
    _assert_denied(run_guard(_fixture("before_tool_run_shell_command")))


def test_claude_codex_and_cursor_outputs_are_unchanged(run_guard):
    """Other harnesses ignore or reject unexpected stdout: leave them untouched."""
    claude_codex = (
        ({"tool_name": "Bash", "tool_input": {"command": "true"}}, 0),
        ({"hook_event_name": "PreToolUse", "session_id": "s", "tool_name": "Bash",
          "tool_input": {"command": INJ}}, 2),
    )
    for payload, code in claude_codex:
        result = run_guard(payload)
        assert (result.returncode, result.stdout) == (code, ""), result.stderr
    cursor = {"conversation_id": "c", "hook_event_name": "beforeShellExecution", "command": "true"}
    result = run_guard(cursor)
    assert (result.returncode, json.loads(result.stdout)) == (0, {"permission": "allow"})


def test_only_before_tool_is_answered_as_gemini(run_guard):
    """AfterTool is non-blocking and never installed: do not answer as if covered."""
    payload = _fixture("before_tool_run_shell_command", hook_event_name="AfterTool")
    result = run_guard(payload)
    assert (result.returncode, result.stdout) == (0, ""), "not answered in Gemini JSON"


def test_gemini_event_with_claude_style_tool_name_is_still_gemini(run_guard):
    _assert_denied(run_guard(_fixture("before_tool_run_shell_command", tool_name="Bash",
                                      tool_input={"command": INJ})))


# --- the --gemini flag: the installed command form --------------------------------------

UNPARSEABLE = {
    "garbage": "this is not json",
    "truncated": '{"hook_event_name": "BeforeTool", "tool_input": {',
    "json-list": "[1, 2, 3]",
    "json-string": '"just a string"',
    "deep-nesting": "[" * 50000 + "]" * 50000,
}


@pytest.mark.parametrize("kind", sorted(UNPARSEABLE))
def test_flagged_guard_denies_input_it_cannot_parse(run_guard, kind):
    """Without a verdict Gemini proceeds, so --gemini turns every unreadable input into a deny."""
    _assert_denied(run_guard(UNPARSEABLE[kind], "--gemini"))


@pytest.mark.parametrize("kind", ["garbage", "json-list", "deep-nesting"])
def test_unflagged_guard_keeps_its_old_behaviour_on_unparseable_input(run_guard, kind):
    """Claude Code, Codex and Cursor entries carry no flag: nothing changes for them."""
    result = run_guard(UNPARSEABLE[kind])
    assert result.stdout == ""
    assert result.returncode in (0, 1), result.stderr


def test_flagged_guard_answers_a_normal_gemini_payload_as_before(run_guard):
    result = run_guard(_fixture("before_tool_run_shell_command"), "--gemini")
    _assert_not_blocked(result)
    assert json.loads(result.stdout) == {}
    _assert_denied(run_guard(_input("before_tool_run_shell_command", command=f"echo {INJ}"), "--gemini"))


def test_flagged_guard_treats_empty_stdin_as_nothing_to_scan(run_guard):
    result = run_guard("", "--gemini")
    _assert_not_blocked(result)

