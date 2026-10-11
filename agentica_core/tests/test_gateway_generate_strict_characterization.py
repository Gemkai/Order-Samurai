"""Characterization of LLMGateway.generate_strict() before the W4 refactor.

Plan: docs/plans/2026-10-07-order-samurai-hotspot-split/PLAN.md, W4. generate_strict is CC 41.
The acceptance tests in test_fleet_strict_transport.py / test_fleet_strict_format.py replace
Popen in-process; these cases instead put a real fake `claude` executable on PATH, so the
subprocess, pipe-thread, timeout and kill paths run for real. Each case pins the outcome
(StrictCallError kind, or the reply minus its wall-clock latency) and what the CLI received
(argv after the executable, the filtered environment keys, stdin). No network, no real CLI.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest

import agentica_core.llm.gateway as gw

MODEL = "claude-sonnet-4-6"
PROMPT = "PROMPT-MARKER {\"q\": 1}"
SYSTEM = "Answer from evidence only."
SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}}


def ok_doc(text="Fleet answer.", model=MODEL, **extra) -> dict:
    doc = {"type": "result", "subtype": "success", "is_error": False, "result": text,
           "total_cost_usd": 0.0123, "modelUsage": {model: {"inputTokens": 5}}}
    doc.update(extra)
    return doc


FAKE_CLI = textwrap.dedent('''\
    #!{python}
    import json, os, sys, time
    here = os.path.dirname(os.path.abspath(sys.argv[0]))
    spec = json.load(open(os.path.join({spec_dir!r}, "spec.json")))
    record = {{"argv": sys.argv[1:], "env": sorted(os.environ),
              "cwd_is_tmp": "fleet-claude-" in os.getcwd()}}
    record["stdin"] = sys.stdin.read()
    json.dump(record, open(os.path.join({spec_dir!r}, "record.json"), "w"))
    if spec.get("orphan"):
        import subprocess
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    time.sleep(spec.get("sleep", 0))
    sys.stdout.write(spec.get("stdout", ""))
    sys.stdout.flush()
    sys.exit(spec.get("exit", 0))
''')


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """Install the fake CLI on PATH; returns a function that configures one run."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "claude"
    exe.write_text(FAKE_CLI.format(python=sys.executable, spec_dir=str(tmp_path)),
                   encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    spec, record = tmp_path / "spec.json", tmp_path / "record.json"
    home = tmp_path / "home"
    home.mkdir()
    for key in list(os.environ):
        if key not in ("PATH", "TMPDIR", "LANG"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}/usr/bin:/bin")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-reach-the-cli")
    monkeypatch.setattr(gw, "_emit_telemetry", lambda *a, **k: None)

    def configure(**run):
        spec.write_text(json.dumps(run), encoding="utf-8")
        return record
    return configure


def call(**kwargs):
    gateway = gw.LLMGateway.__new__(gw.LLMGateway)  # no provider setup needed
    args = {"prompt": PROMPT, "system": SYSTEM}
    args.update(kwargs)
    try:
        reply = gateway.generate_strict(**args)
    except gw.StrictCallError as exc:
        return {"error": str(exc.args[0]) if exc.args else repr(exc)}
    latency = reply.pop("latency_ms")
    assert isinstance(latency, int) and latency >= 0
    return {"reply": reply}


# The allowlist generate_strict forwards, intersected with what this fixture sets.
# macOS adds LC_CTYPE / __CF_USER_TEXT_ENCODING to every child; those are not ours.
_OS_INJECTED = {"LC_CTYPE", "__CF_USER_TEXT_ENCODING"}
ENV_SEEN = "HOME, PATH (+TMPDIR/LANG when the runner has them), nothing else"


def received(record: Path) -> dict:
    data = json.loads(record.read_text(encoding="utf-8"))
    env = set(data["env"]) - _OS_INJECTED
    allowed = {"HOME", "PATH", "TMPDIR", "LANG"}
    env_ok = {"HOME", "PATH"} <= env <= allowed
    return {"argv": data["argv"], "env": ENV_SEEN if env_ok else sorted(env),
            "stdin": data["stdin"], "cwd_is_tmp": data["cwd_is_tmp"]}


# --------------------------------------------------------------------------- refusals before launch

@pytest.mark.parametrize("kwargs, kind", [
    ({"model": "claude-opus-4-1"}, "model_not_allowed"),
    ({"prompt": b"bytes"}, "invalid_request"),
    ({"prompt": "x" * 24001}, "invalid_request"),
    ({"system": None}, "invalid_request"),
    ({"system": "s" * 8001}, "invalid_request"),
    ({"timeout_s": "45"}, "invalid_request"),
    ({"timeout_s": float("nan")}, "invalid_request"),
    ({"timeout_s": 0}, "invalid_request"),
    ({"timeout_s": 46}, "invalid_request"),
    ({"max_output_chars": 0}, "invalid_request"),
    ({"max_output_chars": 4001}, "invalid_request"),
    ({"json_schema": ["not", "a", "dict"]}, "invalid_request"),
    ({"json_schema": {"x": float("nan")}}, "invalid_request"),
    ({"json_schema": {"x": "y" * 8000}}, "invalid_request"),
    ({"json_schema": {"x": "y" * 7993}}, "invalid_request"),   # compact JSON 8001 chars
])
def test_invalid_requests_are_refused_before_any_launch(cli, kwargs, kind):
    record = cli(stdout=json.dumps(ok_doc()))
    assert call(**kwargs) == {"error": kind}
    assert not record.exists()


def test_schema_of_exactly_the_limit_is_launched(cli):
    """Added after the W4b review: compact JSON of exactly 8000 chars is accepted and passed
    through verbatim (the 8001-char case above is refused)."""
    record = cli(stdout=json.dumps(ok_doc()))
    schema = {"x": "y" * 7992}
    text = json.dumps(schema, separators=(",", ":"))
    assert len(text) == 8000
    call(json_schema=schema)
    argv = received(record)["argv"]
    assert argv[argv.index("--json-schema") + 1] == text


def test_missing_cli_is_unavailable(cli, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert call() == {"error": "unavailable"}


def test_cli_in_home_local_bin_is_the_fallback(cli, monkeypatch, tmp_path):
    record = cli(stdout=json.dumps(ok_doc()))
    local = tmp_path / "home" / ".local" / "bin"
    local.mkdir(parents=True)
    (local / "claude").write_text((tmp_path / "bin" / "claude").read_text(), encoding="utf-8")
    (local / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert call() == {"reply": {"text": "Fleet answer.", "model": MODEL, "cost_usd": 0.0123}}
    assert received(record)["argv"][:2] == ["-p", "--model"]


# --------------------------------------------------------------------------- launched runs

def test_successful_text_call_pins_reply_and_cli_contract(cli):
    record = cli(stdout=json.dumps(ok_doc("  Fleet answer.  ")))
    assert call(model="claude-sonnet-5-5") == {"error": "invalid_reply"}  # modelUsage mismatch
    assert call() == {"reply": {"text": "Fleet answer.", "model": MODEL, "cost_usd": 0.0123}}
    assert received(record) == {
        "argv": ["-p", "--model", MODEL, "--effort", "low", "--output-format", "json",
                 "--safe-mode", "--tools", "", "--strict-mcp-config", "--mcp-config",
                 '{"mcpServers":{}}', "--permission-mode", "plan", "--no-session-persistence",
                 "--disable-slash-commands", "--system-prompt", SYSTEM],
        "env": ENV_SEEN,
        "stdin": PROMPT,
        "cwd_is_tmp": True,
    }


def test_successful_structured_call_returns_compact_json_text(cli):
    record = cli(stdout=json.dumps(ok_doc("ignored prose",
                                          structured_output={"answer": "é ok"})))
    assert call(json_schema=SCHEMA) == {"reply": {
        "text": '{"answer":"é ok"}', "model": MODEL, "cost_usd": 0.0123,
        "structured": {"answer": "é ok"}}}
    argv = received(record)["argv"]
    assert argv[-2:] == ["--json-schema", json.dumps(SCHEMA, separators=(",", ":"))]


@pytest.mark.parametrize("stdout, kwargs, expected", [
    (json.dumps(ok_doc(is_error=True)), {}, "invalid_reply"),
    (json.dumps(ok_doc(subtype="error_max_turns")), {}, "invalid_reply"),
    (json.dumps(ok_doc(modelUsage={MODEL: {}, "other": {}})), {}, "invalid_reply"),
    (json.dumps(ok_doc("   ")), {}, "invalid_reply"),
    (json.dumps(ok_doc(result=None)), {}, "invalid_reply"),
    (json.dumps(ok_doc("x" * 11)), {"max_output_chars": 10}, "invalid_reply"),
    (json.dumps(ok_doc(total_cost_usd=-1)), {}, "invalid_reply"),
    (json.dumps(ok_doc(total_cost_usd="NaN")), {}, "invalid_reply"),
    (json.dumps(ok_doc(total_cost_usd="cheap")), {}, "invalid_reply"),
    (json.dumps(ok_doc(structured_output="not-a-dict")), {"json_schema": SCHEMA}, "invalid_reply"),
    (json.dumps(ok_doc()), {"json_schema": SCHEMA}, "invalid_reply"),
    ("not json at all", {}, "invalid_reply"),
    (json.dumps(["a", "list"]), {}, "invalid_reply"),
])
def test_malformed_replies_are_invalid_reply(cli, stdout, kwargs, expected):
    cli(stdout=stdout)
    assert call(**kwargs) == {"error": expected}


def test_zero_cost_field_defaults_to_free(cli):
    doc = ok_doc()
    del doc["total_cost_usd"]
    cli(stdout=json.dumps(doc))
    assert call() == {"reply": {"text": "Fleet answer.", "model": MODEL, "cost_usd": 0.0}}


def test_nonzero_exit_is_unavailable(cli):
    cli(stdout=json.dumps(ok_doc()), exit=1)
    assert call() == {"error": "unavailable"}


def test_oversized_output_is_output_limit(cli):
    cli(stdout="x" * 70000)
    assert call() == {"error": "output_limit"}


def test_slow_cli_is_killed_and_reported_as_timeout(cli):
    cli(stdout=json.dumps(ok_doc()), sleep=5)
    assert call(timeout_s=0.5) == {"error": "timeout"}


def test_unlaunchable_cli_is_unavailable(cli, tmp_path):
    exe = tmp_path / "bin" / "claude"
    exe.write_text("#!/nonexistent/interpreter\n", encoding="utf-8")
    assert call() == {"error": "unavailable"}


def test_cli_that_leaves_stdout_open_after_exit_is_a_timeout(cli):
    """The CLI exits but a child it spawned still holds stdout: the reader never sees EOF, so
    the call times out and the whole process group is killed."""
    cli(stdout=json.dumps(ok_doc()), orphan=True)
    assert call(timeout_s=1) == {"error": "timeout"}
