"""Independent acceptance tests for the strict Claude CLI transport, written before implementation.

Fleet Status may send authorized evidence to the explicitly requested approved Sonnet model through the
installed Claude CLI, with no tools, no MCP, no session persistence, no provider fallback and
no logging. The CLI is replaced by a realistic fake; no real process, network or credential
is touched. The argv flags asserted here are the fixed security contract.
"""
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
from types import SimpleNamespace

import pytest

import agentica_core.llm.gateway as gw

MODEL = "claude-sonnet-4-6"
NEW_MODEL = "claude-sonnet-5-5"
PROMPT = '{"question": "PROMPT-MARKER-7781", "evidence": {}, "history": []}'
SYSTEM = "Answer only from the supplied fleet evidence."
LEAKY = {
    "ANTHROPIC_API_KEY": "sk-ant-should-not-leak", "ANTHROPIC_AUTH_TOKEN": "leak",
    "ANTHROPIC_BASE_URL": "https://proxy.invalid", "ANTHROPIC_MODEL": "claude-opus-4-1",
    "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_USE_VERTEX": "1",
    "HTTPS_PROXY": "http://proxy.invalid:1", "HTTP_PROXY": "http://proxy.invalid:1",
    "ALL_PROXY": "socks5://proxy.invalid:1", "https_proxy": "http://proxy.invalid:1",
    "OPENROUTER_API_KEY": "leak",
}


def _result(text="Fleet answer.", model=MODEL, **extra):
    doc = {"type": "result", "subtype": "success", "is_error": False, "duration_ms": 900,
           "num_turns": 1, "result": text, "session_id": "00000000-0000-0000-0000-000000000000",
           "total_cost_usd": 0.0123, "usage": {"input_tokens": 50, "output_tokens": 9},
           "modelUsage": {model: {"inputTokens": 50, "outputTokens": 9, "costUSD": 0.0123}}}
    doc.update(extra)
    return json.dumps(doc)


class Cli:
    """Stand-in for the installed `claude` CLI; records exactly how it was launched."""

    def __init__(self, stdout=None, returncode=0, hang=False):
        self.stdout = _result() if stdout is None else stdout
        self.returncode, self.hang = returncode, hang
        self.launches, self.stdin, self.waits, self.signals, self.procs = [], [], [], [], []

    def install(self, monkeypatch):
        cli = self

        class Pipe:
            closed = False

            def write(self, data):
                cli.stdin.append(data if isinstance(data, str) else data.decode())
                return len(data)

            def flush(self):
                pass

            def close(self):
                self.closed = True

        class FakePopen:
            def __init__(self, args, **kw):
                cwd = kw.get("cwd")
                cli.launches.append({"args": args, "kw": kw,
                                     "cwd_existed": bool(cwd) and os.path.isdir(cwd)})
                cli.procs.append(self)
                self.args, self.pid, self.returncode, self.killed = args, 987654, None, False
                self.text = bool(kw.get("text") or kw.get("universal_newlines") or kw.get("encoding"))
                self.stdin = Pipe()
                self.stdout = io.StringIO(cli.stdout) if self.text else io.BytesIO(cli.stdout.encode())
                self.stderr = io.StringIO() if self.text else io.BytesIO()

            def _finish(self, timeout):
                cli.waits.append(timeout)
                if cli.hang and not self.killed:
                    raise subprocess.TimeoutExpired(self.args, timeout)
                self.returncode = -9 if self.killed else cli.returncode

            def communicate(self, input=None, timeout=None):
                if input:
                    self.stdin.write(input)
                self._finish(timeout)
                out = "" if self.killed else cli.stdout
                return (out, "") if self.text else (out.encode(), b"")

            def wait(self, timeout=None):
                self._finish(timeout)
                return self.returncode

            def poll(self):
                return self.returncode

            def kill(self):
                self.killed = True
                cli.signals.append("kill")

            def terminate(self):
                self.killed = True
                cli.signals.append("terminate")

            def send_signal(self, sig):
                self.killed = True
                cli.signals.append(("signal", sig))

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def killpg(pgid, sig):
            cli.signals.append(("killpg", pgid, sig))
            for proc in cli.procs:
                proc.killed = True

        monkeypatch.setattr(subprocess, "Popen", FakePopen)
        monkeypatch.setattr(os, "killpg", killpg)
        monkeypatch.setattr(os, "getpgid", lambda pid: pid)
        monkeypatch.setattr(os, "kill", lambda pid, sig: cli.signals.append(("kill", pid, sig)))
        monkeypatch.setattr(shutil, "which",
                            lambda name, *a, **k: "/opt/fake/bin/claude" if name == "claude" else None)
        return self


@pytest.fixture()
def gateway(monkeypatch):
    for var in ("GEMINI_API_KEY", "GEMINI_PAID_API_KEY", "ANTHROPIC_API_KEY",
                "OPENROUTER_API_KEY", "OPENAI_API_KEY", "LANGFUSE_PUBLIC_KEY"):
        monkeypatch.delenv(var, raising=False)
    instance = gw.LLMGateway()
    side = []

    def no_http(*args, **kwargs):
        side.append("http")
        raise RuntimeError("no HTTP provider may be used")

    monkeypatch.setattr(gw.requests, "post", no_http)
    monkeypatch.setattr(gw, "_emit_telemetry", lambda *a, **k: side.append("telemetry"))
    monkeypatch.setattr(gw, "_emit_governance_telemetry", lambda *a, **k: side.append("telemetry"))
    monkeypatch.setattr(gw.LLMGateway, "generate_text",
                        lambda self, *a, **k: side.append("fallback") or "fallback")
    instance.langfuse = SimpleNamespace(generation=lambda **k: side.append("langfuse"))
    for key, value in LEAKY.items():
        monkeypatch.setenv(key, value)
    instance.side_effects = side
    return instance


def _strict(gateway):
    error = getattr(gw, "StrictCallError", None)
    assert isinstance(error, type) and issubclass(error, Exception), \
        "gateway.StrictCallError(kind) is the strict transport's failure type"
    call = getattr(gateway, "generate_strict", None)
    assert callable(call), "LLMGateway.generate_strict is the strict Claude CLI transport"
    return call, error


def _flag(argv, name):
    for i, arg in enumerate(argv):
        if arg == name:
            return argv[i + 1] if i + 1 < len(argv) else None
        if arg.startswith(name + "="):
            return arg[len(name) + 1:]
    return None


def _quiet(capsys):
    captured = capsys.readouterr()
    return "PROMPT-MARKER-7781" not in captured.out + captured.err


def test_strict_call_runs_one_isolated_tool_free_claude_cli(gateway, monkeypatch, capsys):
    generate, _ = _strict(gateway)
    cli = Cli().install(monkeypatch)
    first = generate(prompt=PROMPT, system=SYSTEM)
    generate(prompt=PROMPT, system=SYSTEM)
    assert first["text"] == "Fleet answer." and first["model"] == MODEL
    assert first["cost_usd"] == pytest.approx(0.0123)
    assert isinstance(first["latency_ms"], (int, float)) and first["latency_ms"] >= 0
    assert len(cli.launches) == 2
    argv, kw = cli.launches[0]["args"], cli.launches[0]["kw"]
    assert isinstance(argv, list) and all(isinstance(a, str) for a in argv)
    assert not kw.get("shell") and os.path.basename(argv[0]) == "claude"
    assert "-p" in argv or "--print" in argv
    assert _flag(argv, "--model") == MODEL
    assert _flag(argv, "--system-prompt") == SYSTEM
    assert _flag(argv, "--tools") == ""
    assert "--strict-mcp-config" in argv
    if _flag(argv, "--mcp-config") is not None:
        assert json.loads(_flag(argv, "--mcp-config")) == {"mcpServers": {}}
    assert "--no-session-persistence" in argv and "--safe-mode" in argv
    assert not any("PROMPT-MARKER-7781" in a for a in argv)
    assert kw.get("stdin") == subprocess.PIPE and PROMPT in "".join(cli.stdin)
    env = kw.get("env")
    assert isinstance(env, dict) and env.get("HOME") and env.get("PATH")
    assert not set(LEAKY) & set(env)
    assert kw.get("start_new_session") is True or kw.get("process_group") == 0
    bounded = [w for w in cli.waits if w is not None]
    assert bounded and max(bounded) <= 45
    cwds = [launch["kw"].get("cwd") for launch in cli.launches]
    assert all(launch["cwd_existed"] for launch in cli.launches) and cwds[0] != cwds[1]
    temp_root = os.path.realpath(tempfile.gettempdir())
    assert all(os.path.realpath(c).startswith(temp_root) for c in cwds)
    assert os.path.realpath(os.getcwd()) not in {os.path.realpath(c) for c in cwds}
    assert gateway.side_effects == [] and _quiet(capsys)


def test_sonnet_55_is_passed_exactly_and_its_returned_model_is_validated(gateway,
                                                                        monkeypatch):
    generate, error = _strict(gateway)
    cli = Cli(stdout=_result(model=NEW_MODEL)).install(monkeypatch)

    result = generate(prompt=PROMPT, system=SYSTEM, model=NEW_MODEL)

    assert result["model"] == NEW_MODEL
    assert _flag(cli.launches[0]["args"], "--model") == NEW_MODEL

    mismatch = Cli(stdout=_result(model=MODEL)).install(monkeypatch)
    with pytest.raises(error) as caught:
        generate(prompt=PROMPT, system=SYSTEM, model=NEW_MODEL)
    assert caught.value.kind == "invalid_reply"
    assert _flag(mismatch.launches[0]["args"], "--model") == NEW_MODEL


def test_only_the_two_canonical_models_are_accepted(gateway, monkeypatch):
    generate, error = _strict(gateway)
    cli = Cli().install(monkeypatch)
    for other in ("claude-opus-4-1", "anthropic/claude-sonnet-5-5", "gemini-2.5-flash"):
        with pytest.raises(error):
            generate(prompt=PROMPT, system=SYSTEM, model=other)
    assert cli.launches == [] and gateway.side_effects == []


@pytest.mark.parametrize("stdout,returncode", [
    (_result(model="claude-opus-4-1"), 0), (_result(text=""), 0), (_result(text="   "), 0),
    (_result(is_error=True, subtype="error_during_execution"), 0), (_result(), 1),
    ("not json at all", 0),
], ids=["model-mismatch", "empty", "blank", "cli-error", "nonzero-exit", "unparseable"])
def test_unusable_cli_results_fail_explicitly_without_fallback(gateway, monkeypatch, capsys,
                                                               stdout, returncode):
    generate, error = _strict(gateway)
    Cli(stdout=stdout, returncode=returncode).install(monkeypatch)
    with pytest.raises(error) as caught:
        generate(prompt=PROMPT, system=SYSTEM)
    assert isinstance(caught.value.kind, str) and caught.value.kind
    assert gateway.side_effects == [] and _quiet(capsys)


def test_deadline_kills_the_process_group_and_reports_timeout(gateway, monkeypatch):
    generate, error = _strict(gateway)
    cli = Cli(hang=True).install(monkeypatch)
    started = time.monotonic()
    with pytest.raises(error) as caught:
        generate(prompt=PROMPT, system=SYSTEM, timeout_s=0.3)
    assert time.monotonic() - started < 10
    assert "timeout" in caught.value.kind
    assert any(isinstance(s, tuple) and s[0] == "killpg" for s in cli.signals)
    assert gateway.side_effects == []


def test_output_is_bounded(gateway, monkeypatch):
    generate, error = _strict(gateway)
    Cli(stdout=_result(text="word " * 1000)).install(monkeypatch)
    try:
        result = generate(prompt=PROMPT, system=SYSTEM, max_output_chars=4000)
    except error:
        return
    assert len(result["text"]) <= 4000
