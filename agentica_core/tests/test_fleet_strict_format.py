"""Strict Claude CLI structured-output transport regressions for Fleet Status."""
from __future__ import annotations

import io
import json
import shutil
import subprocess

import pytest

import agentica_core.llm.gateway as gateway_module


MODEL = "claude-sonnet-4-6"
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "answer": {"type": "string"},
        "source_refs": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
    },
    "required": ["answer", "source_refs"],
}
STRUCTURED = {"answer": "Claude is reviewing the mock release.", "source_refs": ["s1"]}


def _envelope(*, structured=STRUCTURED, result="wrapper text", include_structured=True):
    doc = {
        "type": "result", "subtype": "success", "is_error": False,
        "result": result, "total_cost_usd": 0.01,
        "modelUsage": {MODEL: {"inputTokens": 1, "outputTokens": 1}},
    }
    if include_structured:
        doc["structured_output"] = structured
    return json.dumps(doc)


class _Cli:
    def __init__(self, stdout):
        self.stdout = stdout
        self.launches = []

    def install(self, monkeypatch):
        owner = self

        class Pipe:
            def write(self, data):
                return len(data)

            def close(self):
                pass

        class Process:
            def __init__(self, args, **kwargs):
                owner.launches.append((args, kwargs))
                self.pid, self.returncode = 123456, 0
                self.stdin = Pipe()
                self.stdout = io.BytesIO(owner.stdout.encode())

            def wait(self, timeout=None):
                return 0

        monkeypatch.setattr(subprocess, "Popen", Process)
        monkeypatch.setattr(shutil, "which", lambda name: "/opt/fake/bin/claude")
        return self


def _gateway(monkeypatch, stdout):
    cli = _Cli(stdout).install(monkeypatch)
    instance = gateway_module.LLMGateway()
    instance.langfuse = None
    return instance, cli


def _flag(argv, name):
    index = argv.index(name)
    return argv[index + 1]


def test_strict_transport_passes_schema_to_cli_and_returns_structured_output(monkeypatch):
    gateway, cli = _gateway(monkeypatch, _envelope())
    result = gateway.generate_strict(
        prompt='{"question":"fictional"}', system="Return the schema.", json_schema=SCHEMA)
    argv = cli.launches[0][0]
    assert json.loads(_flag(argv, "--json-schema")) == SCHEMA
    assert result["structured"] == STRUCTURED
    assert result["model"] == MODEL


def test_schema_request_rejects_cli_success_without_structured_output(monkeypatch):
    gateway, _ = _gateway(monkeypatch, _envelope(include_structured=False,
                                                  result='prose\n{"answer":"x"}'))
    with pytest.raises(gateway_module.StrictCallError) as caught:
        gateway.generate_strict(
            prompt='{"question":"fictional"}', system="Return the schema.", json_schema=SCHEMA)
    assert caught.value.kind == "invalid_reply"


def test_legacy_strict_text_call_remains_supported_without_a_schema(monkeypatch):
    gateway, cli = _gateway(monkeypatch, _envelope())
    assert gateway.generate_strict(
        prompt='{"question":"fictional"}', system="Plain text.")["text"] == "wrapper text"
    assert len(cli.launches) == 1


@pytest.mark.parametrize("schema", [[], "not an object"])
def test_non_object_schema_is_rejected_before_launch(monkeypatch, schema):
    gateway, cli = _gateway(monkeypatch, _envelope())
    with pytest.raises(gateway_module.StrictCallError) as caught:
        gateway.generate_strict(
            prompt='{"question":"fictional"}', system="Return schema.", json_schema=schema)
    assert caught.value.kind == "invalid_request"
    assert cli.launches == []
