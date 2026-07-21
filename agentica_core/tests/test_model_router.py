"""Tests for agentica_core/model_router.py — the scout/mechanism fallback router.

Focus: the fallback chain semantics (first success wins, errors skip to the next
backend, all-fail returns None) and the documented Ollama reliability guards
(CLAUDE.md "Local LLM Routing"): reasoning-field fallback for thinking models,
max_tokens >= 512 floor, empty output treated as failure.
"""
import io
import json
from unittest.mock import MagicMock, patch

import pytest

from agentica_core import model_router


@pytest.fixture(autouse=True)
def no_provider_keys(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def _http_response(body: dict):
    """Context-manager mock mimicking urllib.request.urlopen."""
    resp = MagicMock()
    resp.read.return_value = json.dumps(body).encode()
    resp.__enter__ = lambda self: self
    resp.__exit__ = lambda self, *a: False
    return resp


def _openai_chat_body(content: str, **extra) -> dict:
    message = {"content": content, **extra}
    return {"choices": [{"message": message}]}


# ------------------------------------------------------- fallback chain

def test_call_llm_returns_none_when_all_backends_fail():
    with patch.object(model_router.urllib.request, "urlopen", side_effect=OSError("down")):
        assert model_router.call_llm("sys", "user") is None


def test_call_llm_skips_keyless_backends_and_uses_ollama():
    # No API keys set -> claude/gemini return None without any HTTP call;
    # ollama (keyless) is the first backend that issues a request.
    with patch.object(model_router.urllib.request, "urlopen") as urlopen:
        urlopen.return_value = _http_response(_openai_chat_body("local says hi"))
        out = model_router.call_llm("sys", "user", task="classification")
    assert out == "local says hi"
    assert urlopen.call_count == 1
    req = urlopen.call_args.args[0]
    assert "/v1/chat/completions" in req.full_url
    assert "11434" in req.full_url or "chat/completions" in req.full_url


def test_call_llm_backend_exception_falls_through_to_next(monkeypatch):
    # Ollama raises -> chain continues to openrouter (key present) and succeeds.
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        if "11434" in req.full_url or "localhost" in req.full_url:
            raise OSError("ollama down")
        return _http_response(_openai_chat_body("openrouter answer"))

    with patch.object(model_router.urllib.request, "urlopen", side_effect=fake_urlopen):
        out = model_router.call_llm("sys", "user")
    assert out == "openrouter answer"
    assert len(calls) == 2


def test_call_llm_unknown_task_returns_none():
    # Unknown task key raises inside every backend; call_llm swallows and
    # returns None rather than crashing the calling scout.
    with patch.object(model_router.urllib.request, "urlopen") as urlopen:
        urlopen.return_value = _http_response(_openai_chat_body("x"))
        assert model_router.call_llm("sys", "user", task="bogus-task") is None


def test_all_backends_carry_explicit_timeout():
    # Release It! hard rule: every remote call has an explicit timeout.
    with patch.object(model_router.urllib.request, "urlopen") as urlopen:
        urlopen.return_value = _http_response(_openai_chat_body("hi"))
        model_router.call_llm("sys", "user")
    assert urlopen.call_args.kwargs.get("timeout") == model_router._TIMEOUT_S


# ------------------------------------------------ ollama-specific guards

def test_ollama_empty_content_is_failure_not_success():
    # Empty local output must return None (-> chain continues), never "".
    with patch.object(model_router.urllib.request, "urlopen") as urlopen:
        urlopen.return_value = _http_response(_openai_chat_body("   "))
        out = model_router._call_ollama("sys", "user", "classification", 2048, 0.0)
    assert out is None


def test_ollama_reads_reasoning_field_when_content_empty():
    # deepseek-r1 (the "analysis" model) can return empty content with the
    # answer in the reasoning field — the documented CLAUDE.md guard. Missing
    # this fallback is what made the local tier silently dead for a month.
    with patch.object(model_router.urllib.request, "urlopen") as urlopen:
        urlopen.return_value = _http_response(
            _openai_chat_body("", reasoning="the reasoned answer")
        )
        out = model_router._call_ollama("sys", "user", "analysis", 2048, 0.0)
    assert out == "the reasoned answer"


def test_ollama_enforces_max_tokens_floor():
    # CLAUDE.md guard: "set max_tokens >= 512" on every local call.
    with patch.object(model_router.urllib.request, "urlopen") as urlopen:
        urlopen.return_value = _http_response(_openai_chat_body("ok"))
        model_router._call_ollama("sys", "user", "classification", 64, 0.0)
    req = urlopen.call_args.args[0]
    sent = json.loads(req.data)
    assert sent["max_tokens"] >= 512


def test_ollama_task_selects_documented_model():
    with patch.object(model_router.urllib.request, "urlopen") as urlopen:
        urlopen.return_value = _http_response(_openai_chat_body("ok"))
        model_router._call_ollama("sys", "user", "classification", 2048, 0.0)
        sent_fast = json.loads(urlopen.call_args.args[0].data)
        model_router._call_ollama("sys", "user", "analysis", 2048, 0.0)
        sent_deep = json.loads(urlopen.call_args.args[0].data)
    assert sent_fast["model"] == model_router._MODELS["ollama"]["classification"]
    assert sent_deep["model"] == model_router._MODELS["ollama"]["analysis"]


def test_claude_and_gemini_return_none_without_keys():
    assert model_router._call_claude("s", "u", "analysis", 100, 0.0) is None
    assert model_router._call_gemini("s", "u", "analysis", 100, 0.0) is None
