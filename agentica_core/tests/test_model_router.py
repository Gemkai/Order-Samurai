"""Contract tests for the stable model_router facade and canonical gateway."""
from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from agentica_core import model_router
from agentica_core.llm import gateway as gateway_module


@pytest.fixture(autouse=True)
def no_provider_keys(monkeypatch):
    monkeypatch.setattr(gateway_module, "load_dotenv", lambda *a, **k: False)
    for var in (
        "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY",
        "GEMINI_PAID_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


def _response(body: dict, status: int = 200):
    response = MagicMock()
    response.status_code = status
    response.text = ""
    response.json.return_value = body
    response.raise_for_status.return_value = None
    return response


def _local_body(content: str = "", **extra) -> dict:
    return {"message": {"content": content, **extra}}


def test_facade_points_at_the_canonical_router_contract():
    assert model_router.__router_facade__ is True
    assert model_router.call_llm is gateway_module.call_routed_llm
    assert model_router._MODELS is gateway_module.ROUTED_MODELS


def test_call_llm_returns_none_when_all_backends_fail():
    with patch.object(gateway_module.requests, "post", side_effect=OSError("down")):
        assert model_router.call_llm("sys", "user") is None


def test_keyless_call_uses_local_ollama():
    with patch.object(gateway_module.requests, "post", return_value=_response(_local_body("local says hi"))) as post:
        out = model_router.call_llm("sys", "user", task="classification")
    assert out == "local says hi"
    assert post.call_count == 1
    assert post.call_args.args[0].endswith("/api/chat")


def test_local_failure_falls_through_to_openrouter(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    def fake_post(url, **kwargs):
        if url.endswith("/api/chat"):
            raise OSError("ollama down")
        return _response({"choices": [{"message": {"content": "openrouter answer"}}]})

    with patch.object(gateway_module.requests, "post", side_effect=fake_post) as post:
        assert model_router.call_llm("sys", "user") == "openrouter answer"
    assert post.call_count == 2


def test_unknown_task_fails_without_network_call():
    with patch.object(gateway_module.requests, "post") as post:
        assert model_router.call_llm("sys", "user", task="bogus-task") is None
    post.assert_not_called()


def test_local_request_has_explicit_timeout_and_token_floor():
    with patch.object(gateway_module.requests, "post", return_value=_response(_local_body("ok"))) as post:
        model_router.call_llm("sys", "user", max_tokens=64)
    assert post.call_args.kwargs["timeout"] == model_router.LOCAL_TIMEOUT_SEC
    assert post.call_args.kwargs["json"]["options"]["num_predict"] >= 512


def test_local_thinking_response_uses_reasoning_fallback():
    with patch.object(
        gateway_module.requests,
        "post",
        return_value=_response(_local_body("", reasoning="the reasoned answer")),
    ):
        assert model_router.call_llm("sys", "user", task="analysis") == "the reasoned answer"


def test_task_selects_the_documented_local_model():
    with patch.object(gateway_module.requests, "post", return_value=_response(_local_body("ok"))) as post:
        model_router.call_llm("sys", "user", task="classification")
        fast = post.call_args.kwargs["json"]["model"]
        model_router.call_llm("sys", "user", task="analysis")
        deep = post.call_args.kwargs["json"]["model"]
    assert fast == gateway_module.ROUTED_MODELS["ollama"]["classification"]
    assert deep == gateway_module.ROUTED_MODELS["ollama"]["analysis"]


def test_local_only_skips_cloud_even_when_keys_exist(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    with patch.object(gateway_module.requests, "post", return_value=_response(_local_body("local only"))) as post:
        assert model_router.call_llm("sys", "user", local_only=True) == "local only"
    assert post.call_count == 1
    assert post.call_args.args[0].endswith("/api/chat")


def test_local_only_fails_closed_when_ollama_is_down(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    with patch.object(gateway_module.requests, "post", side_effect=OSError("down")) as post:
        assert model_router.call_llm("sys", "user", local_only=True) is None
    assert post.call_count == 1


def test_brain_context_is_prepended_to_the_system_message():
    fake_brain = ModuleType("agentica_core.brain_context")
    fake_brain.load_brain_context = MagicMock(return_value="BRAIN CONTEXT")
    with (
        patch.dict(sys.modules, {"agentica_core.brain_context": fake_brain}),
        patch.object(gateway_module.requests, "post", return_value=_response(_local_body("ok"))) as post,
    ):
        assert model_router.call_llm("BASE SYSTEM", "user", brain=True) == "ok"
    messages = post.call_args.kwargs["json"]["messages"]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"].startswith("BRAIN CONTEXT")
    assert "BASE SYSTEM" in messages[0]["content"]


def test_local_calls_disable_thinking_by_default():
    """think=False must be sent even when no caller asked (2026-08-16 audit, P2).

    Previously `think` was only added to the payload when a caller passed it, and
    the public facade (call_routed_llm / call_llm) has a fixed signature with no
    `think` parameter — so no facade caller could turn thinking off. task="analysis"
    routes to qwen3.6:35b, which then returns empty content and makes
    extract_message_text fall back to the `thinking` field, handing the caller a
    chain-of-thought trace as if it were the answer.
    """
    with patch.object(gateway_module.requests, "post",
                      return_value=_response(_local_body("ok"))) as post:
        model_router.call_llm("sys", "user", task="analysis")
    payload = post.call_args.kwargs["json"]
    assert "think" in payload, "think must always be sent, not only when requested"
    assert payload["think"] is False


def test_thinking_can_still_be_opted_into_explicitly():
    """The default is off, not a removal — a caller that genuinely wants a
    reasoning trace can still ask for one."""
    with patch.object(gateway_module.requests, "post",
                      return_value=_response(_local_body("ok"))) as post:
        gateway_module.LLMGateway().generate_text(
            prompt="user", system_instruction="sys",
            model_chain=["gemma4:4b"], local_only=True, think=True,
        )
    assert post.call_args.kwargs["json"]["think"] is True
