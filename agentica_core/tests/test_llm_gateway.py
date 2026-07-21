"""Tests for agentica_core/llm/gateway.py — the multi-provider LLM gateway.

Focus: the pure routing/parsing helpers, plus the documented Ollama reliability
guards (CLAUDE.md "Local LLM Routing"): a local call must set max_tokens >= 512,
fall back to the reasoning/thinking field when a thinking model returns empty
content, carry an explicit timeout, and treat unparseable output as failure.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from agentica_core.llm.gateway import (
    LLMGateway,
    OLLAMA_TIMEOUT_SEC,
    _dedupe_chain,
)


@pytest.fixture()
def gateway(monkeypatch):
    # Offline gateway: no provider keys, no langfuse, local tier enabled.
    for var in (
        "GEMINI_API_KEY", "GEMINI_PAID_API_KEY", "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY", "OPENAI_API_KEY", "LANGFUSE_PUBLIC_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    return LLMGateway()


# ---------------------------------------------------------------- chains

def test_dedupe_chain_preserves_first_occurrence_order():
    assert _dedupe_chain(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]


def test_dedupe_chain_empty():
    assert _dedupe_chain([]) == []


# ------------------------------------------------- model normalization

def test_normalize_legacy_gemini_alias(gateway):
    assert gateway._normalize_requested_model("gemini-1.5-flash", "PREMIUM") == "gemini-2.5-flash"
    assert gateway._normalize_requested_model("gemini-pro", "PREMIUM") == "gemini-2.5-pro"


def test_normalize_empty_returns_none(gateway):
    assert gateway._normalize_requested_model("", "PREMIUM") is None
    assert gateway._normalize_requested_model(None, "PREMIUM") is None
    assert gateway._normalize_requested_model("   ", "PREMIUM") is None


def test_normalize_anthropic_alias(gateway):
    assert (
        gateway._normalize_requested_model("anthropic/claude-3-sonnet", "PREMIUM")
        == "anthropic/claude-3-5-sonnet-latest"
    )


def test_normalize_fast_tier_downgrade_applies_to_non_aliased_pro_only(gateway):
    # The alias table wins before the FAST-tier check, so a known pro model is
    # honored as requested even in FAST tier; only a non-aliased "pro" name is
    # downgraded to flash.
    assert gateway._normalize_requested_model("gemini-2.5-pro", "FAST") == "gemini-2.5-pro"
    assert gateway._normalize_requested_model("some-pro-model", "FAST") == "gemini-2.5-flash"


def test_normalize_unknown_model_passthrough(gateway):
    assert gateway._normalize_requested_model("mystery-model", "PREMIUM") == "mystery-model"


def test_normalize_openrouter_prefix_kept(gateway):
    out = gateway._normalize_requested_model(
        "openrouter/qwen/qwen-2-72b-instruct:free", "PREMIUM"
    )
    assert out == "openrouter/qwen/qwen-2-72b-instruct:free"


def test_normalize_openrouter_anthropic_alias(gateway):
    assert (
        gateway._normalize_openrouter_model("anthropic/claude-3-haiku")
        == "anthropic/claude-3.5-haiku"
    )


# --------------------------------------------------------- tier classing

def test_classify_model_tier(gateway):
    from agentica_core.llm.gateway import LOCAL_MODEL
    assert gateway._classify_model_tier(LOCAL_MODEL, "PREMIUM") == "LOCAL"
    assert gateway._classify_model_tier("google/gemma-2-9b-it:free", "PREMIUM") == "FREE"
    assert gateway._classify_model_tier("gemini-2.5-flash", "PREMIUM") == "FAST"
    assert gateway._classify_model_tier("anthropic/claude-3.5-sonnet", "FAST") == "PREMIUM"
    assert gateway._classify_model_tier("gemini-2.5-pro", "FAST") == "PREMIUM"
    assert gateway._classify_model_tier("some-other-model", "FAST") == "FAST"


# --------------------------------------------------------- json parsing

def test_parse_jsonish_payload_dict_passthrough(gateway):
    assert gateway.parse_jsonish_payload({"a": 1}) == {"a": 1}


def test_parse_jsonish_payload_fenced_json(gateway):
    raw = '```json\n{"verdict": "pass"}\n```'
    assert gateway.parse_jsonish_payload(raw) == {"verdict": "pass"}


def test_parse_jsonish_payload_embedded_in_prose(gateway):
    raw = 'Sure! Here is the result: {"score": 3} — hope that helps.'
    assert gateway.parse_jsonish_payload(raw) == {"score": 3}


def test_parse_jsonish_payload_unparseable_is_failure_not_success(gateway):
    # CLAUDE.md guard: "treat unparseable output as a failure, not success".
    # The failure signal here is an empty dict — callers must not mistake
    # garbage for a valid payload.
    assert gateway.parse_jsonish_payload("total garbage, no json") == {}
    assert gateway.parse_jsonish_payload("[1, 2, 3]") == {}


def test_parse_legacy_content_without_required_keys(gateway):
    assert gateway._parse_legacy_content("hello", None) == {"content": "hello"}


def test_parse_legacy_content_missing_required_key_raises(gateway):
    with pytest.raises(Exception):
        gateway._parse_legacy_content('{"a": 1}', required_json_keys=["a", "b"])


# ---------------------------------------------------- tool-call shaping

def test_normalize_tool_calls_list(gateway):
    count, names = gateway._normalize_tool_calls(["grep", "read"], [])
    assert (count, names) == (2, ["grep", "read"])


def test_normalize_tool_calls_dict(gateway):
    count, names = gateway._normalize_tool_calls({"grep": 1, "read": 2}, [])
    assert count == 2
    assert set(names) == {"grep", "read"}


def test_normalize_tool_calls_count_with_latency_names(gateway):
    latencies = [{"tool": "bash", "ms": 5}, {"not_tool": "x"}]
    count, names = gateway._normalize_tool_calls(3, latencies)
    assert count == 3
    assert names == ["bash"]


def test_normalize_tool_calls_garbage_falls_back_to_names(gateway):
    latencies = [{"tool": "bash"}]
    count, names = gateway._normalize_tool_calls("not-a-number", latencies)
    assert count == 1
    assert names == ["bash"]


# ------------------------------------------- _call_local (Ollama guards)

def _ollama_response(message: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"message": message}
    resp.raise_for_status.return_value = None
    return resp


def test_call_local_passes_explicit_timeout(gateway):
    # Release It! hard rule: every remote call carries an explicit timeout.
    with patch("agentica_core.llm.gateway.requests.post") as post:
        post.return_value = _ollama_response({"content": "ok"})
        gateway._call_local("hi")
    assert post.call_args.kwargs["timeout"] == OLLAMA_TIMEOUT_SEC


def test_call_local_returns_content(gateway):
    with patch("agentica_core.llm.gateway.requests.post") as post:
        post.return_value = _ollama_response({"content": "hello world"})
        assert gateway._call_local("hi") == "hello world"


def test_call_local_falls_back_to_thinking_field_when_content_empty(gateway):
    # deepseek-r1-style thinking models can return empty content with the
    # actual answer in the reasoning/thinking field. The gateway must read it
    # instead of silently returning "" (the failure that killed the local
    # tier for a month — CLAUDE.md "Reliability caveat").
    with patch("agentica_core.llm.gateway.requests.post") as post:
        post.return_value = _ollama_response({"content": "", "thinking": "the answer is 42"})
        assert gateway._call_local("hi") == "the answer is 42"


def test_call_local_falls_back_to_reasoning_field_when_content_empty(gateway):
    with patch("agentica_core.llm.gateway.requests.post") as post:
        post.return_value = _ollama_response({"content": "", "reasoning": "because 6x7"})
        assert gateway._call_local("hi") == "because 6x7"


def test_call_local_empty_reply_is_failure_not_empty_answer(gateway):
    # A fully-empty message (no content/thinking/reasoning) must be treated as a
    # failure, never returned as a valid "" answer. Local is the last fallback
    # link, so a silent "" would reach the caller with no fallback firing — the
    # exact "silently dead" mode the local_guards contract exists to prevent.
    with patch("agentica_core.llm.gateway.requests.post") as post:
        post.return_value = _ollama_response({"content": "", "thinking": "", "reasoning": ""})
        with pytest.raises(Exception):
            gateway._call_local("hi")


def test_call_local_enforces_min_num_predict_floor(gateway):
    # CLAUDE.md guard: "set max_tokens >= 512" on every local call — small
    # budgets truncate thinking models into unparseable output.
    with patch("agentica_core.llm.gateway.requests.post") as post:
        post.return_value = _ollama_response({"content": "ok"})
        gateway._call_local("hi")
    options = post.call_args.kwargs["json"]["options"]
    assert options.get("num_predict", 0) >= 512


def test_call_local_json_format_flag(gateway):
    with patch("agentica_core.llm.gateway.requests.post") as post:
        post.return_value = _ollama_response({"content": "{}"})
        gateway._call_local("hi", response_schema={"type": "object"})
    assert post.call_args.kwargs["json"].get("format") == "json"
