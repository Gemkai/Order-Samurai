"""Tests for agentica_core/llm/gateway.py — the multi-provider LLM gateway.

Focus: the pure routing/parsing helpers, plus the documented Ollama reliability
guards (CLAUDE.md "Local LLM Routing"): a local call must set max_tokens >= 512,
fall back to the reasoning/thinking field when a thinking model returns empty
content, carry an explicit timeout, and treat unparseable output as failure.
"""
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


# ------------------------------------------------ no silent safety stubs

def test_gateway_has_no_silent_safety_stub_imports():
    """Audit W2 regression guard: the gateway must never carry safety controls
    (PII scrubber, guardrails, nuclear option, telemetry) behind ImportError
    fallbacks that silently no-op. The enforced privacy control is local_only /
    pinned-chain routing; anything stronger must fail loud, not pretend."""
    import inspect
    from agentica_core.llm import gateway as gateway_module

    src = inspect.getsource(gateway_module)
    # The module docstring documents the removal by name — drop everything through
    # its closing quotes before scanning (robust to reflowing/indenting the text).
    if src.lstrip().startswith('"""'):
        src = src.split('"""', 2)[2]
    for banned in ("scrub_text", "AIGuardrails", "NuclearOption", "log_execution",
                   "from safety.", "from execution."):
        assert banned not in src, f"silent safety stub reintroduced: {banned}"


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


# ---------------------------------------------------------------- local_only

def test_generate_text_local_only_uses_local_backend(gateway):
    with patch("agentica_core.llm.gateway.requests.post") as post:
        post.return_value = _ollama_response({"content": "private ok"})
        out = gateway.generate_text("hi", local_only=True, return_metadata=True)
    assert out["text"] == "private ok"
    assert out["fallback_index"] == 0
    assert post.call_count == 1
    assert "11434" in post.call_args.args[0]


def test_generate_text_local_only_strips_cloud_models_from_chain(gateway):
    # A cloud-heavy requested chain is filtered down to bare Ollama tags.
    with patch("agentica_core.llm.gateway.requests.post") as post:
        post.return_value = _ollama_response({"content": "ok"})
        out = gateway.generate_text(
            "hi",
            model_chain=[
                "gemini-2.5-flash",
                "anthropic/claude-3.5-sonnet",
                "google/gemma-2-9b-it:free",
                "gemma4:12b",
            ],
            local_only=True,
            return_metadata=True,
        )
    assert out["model"] == "gemma4:12b"
    assert post.call_count == 1


def test_generate_text_local_only_fails_closed_when_local_down(gateway):
    # Ollama down -> the call raises; it must never fail over to a cloud model.
    with patch(
        "agentica_core.llm.gateway.requests.post", side_effect=OSError("conn refused")
    ) as post:
        with pytest.raises(OSError):
            gateway.generate_text("hi", local_only=True)
    assert post.call_count == 1


# --------------------------------------------- _call_openai / _call_openrouter

def _openai_style_response(content) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    resp.raise_for_status.return_value = None
    resp.status_code = 200
    return resp


def test_call_openai_returns_content(gateway):
    gateway.openai_key = "test-key"
    with patch("agentica_core.llm.gateway.requests.post") as post:
        post.return_value = _openai_style_response("hello world")
        assert gateway._call_openai("gpt-4o", "hi") == "hello world"


def test_call_openai_null_content_is_failure_not_success(gateway):
    # A tool-call-only (or content-filtered) OpenAI response carries
    # message.content = null, not a missing key. Unlike _call_gemini and
    # _call_local, this must not be treated as a valid answer — it must raise
    # so generate_text's fallback chain moves to the next model, matching the
    # empty-response guard the other two providers already enforce.
    gateway.openai_key = "test-key"
    with patch("agentica_core.llm.gateway.requests.post") as post:
        post.return_value = _openai_style_response(None)
        with pytest.raises(Exception):
            gateway._call_openai("gpt-4o", "hi")


def test_call_openrouter_null_content_is_failure_not_success(gateway):
    with patch("agentica_core.llm.gateway.requests.post") as post:
        post.return_value = _openai_style_response(None)
        with pytest.raises(Exception):
            gateway._call_openrouter("hi")
