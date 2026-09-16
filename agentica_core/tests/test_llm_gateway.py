"""Tests for agentica_core/llm/gateway.py — the multi-provider LLM gateway.

Focus: the pure routing/parsing helpers, plus the documented Ollama reliability
guards (CLAUDE.md "Local LLM Routing"): a local call must set max_tokens >= 512,
fall back to the reasoning/thinking field when a thinking model returns empty
content, carry an explicit timeout, and treat unparseable output as failure.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

import agentica_core.llm.gateway as gw
from agentica_core.emit import emit as real_emit
from agentica_core.llm.gateway import (
    LLMGateway,
    OLLAMA_TIMEOUT_SEC,
    _dedupe_chain,
    _is_local_model,
    call_routed_llm,
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


def test_parse_jsonish_payload_ignores_second_brace_fragment_after_object(gateway):
    # A trailing prose aside that itself contains braces (a very common LLM
    # habit -- "for example {...}") must not get glued onto the real object.
    raw = 'Sure thing! {"foo": 1} Note: for example {"bar": 2} is another format.'
    assert gateway.parse_jsonish_payload(raw) == {"foo": 1}


def test_parse_jsonish_payload_preserves_nested_object(gateway):
    raw = 'Result: {"outer": {"inner": 1}} — done.'
    assert gateway.parse_jsonish_payload(raw) == {"outer": {"inner": 1}}


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


def test_call_openrouter_auto_sentinel_reaches_the_api_unmangled(gateway):
    # "openrouter/auto" is OpenRouter's own auto-routing pseudo-model id and
    # is the FREE-tier chain's default (_call_openrouter's own default kwarg,
    # and _build_legacy_chain inserts it for FREE tier). It must reach the
    # API verbatim -- normalizing it like an ordinary routed model strips the
    # "openrouter/" prefix and sends the meaningless bare id "auto" instead.
    with patch("agentica_core.llm.gateway.requests.post") as post:
        post.return_value = _openai_style_response("ok")
        gateway._call_openrouter("hi")
    sent_body = json.loads(post.call_args.kwargs["data"])
    assert sent_body["model"] == "openrouter/auto"


# ---------------------------------------------------------- _call_anthropic

def _anthropic_response(text) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"content": [{"type": "text", "text": text}]}
    resp.raise_for_status.return_value = None
    resp.status_code = 200
    return resp


def test_call_anthropic_empty_content_is_failure_not_success(gateway):
    # A stop-sequence hit or safety-filtered generation returns HTTP 200 with
    # content[0].text = "". Unlike _call_openai and _call_openrouter, which
    # already raise on a falsy completion so generate_text's fallback chain
    # advances to the next model, _call_anthropic returned "" as a success —
    # the exact silently-dead-tier failure mode CLAUDE.md's local-guard rule
    # exists to prevent, just on the Anthropic backend instead of Ollama.
    gateway.anthropic_key = "test-key"
    with patch("agentica_core.llm.gateway.requests.post") as post:
        post.return_value = _anthropic_response("")
        with pytest.raises(Exception):
            gateway._call_anthropic("anthropic/claude-3.5-sonnet", "hi")


# --------------------------------------------------- _is_local_model

def test_is_local_model_true_for_bare_ollama_tag():
    assert _is_local_model("gemma4:12b") is True
    assert _is_local_model("qwen3.6:35b") is True


def test_is_local_model_false_for_provider_prefixed_model():
    assert _is_local_model("anthropic/claude-sonnet-4-6") is False
    assert _is_local_model("gemini-2.5-flash") is False  # no ":" -> not a bare tag


def test_is_local_model_false_for_none():
    assert _is_local_model(None) is False


# -------------------------------------------------- generate_text telemetry
#
# Until 2026-08-19 no call through this gateway emitted any telemetry at all —
# every real per-project Local_Routing_Share call was invisible. Instrumented
# INSIDE generate_text() itself (not call_routed_llm/call_llm) because that is
# the one method every real caller funnels through: call_routed_llm, call_llm,
# AND the direct gateway.generate_text() callers (tools/local_ui_patch.py,
# Order Samurai/execution/audit_remediation_patch.py, dashboard-ui/qa/local_audit.py,
# agentica_core/evals/judge.py). An earlier pass instrumented call_routed_llm
# only, which silently missed all four of those direct callers — this is the
# corrected, structurally-complete version.
#
# These tests exercise the real agentica_core.emit.emit() pipeline (schema
# validation included), not a mock of it, redirected to a tmp_path file via the
# same `path=` override test_emit.py already uses: prove a real record lands,
# not that a mock was called. Only the HTTP layer (requests.post) is mocked, so
# generate_text's own code — including the telemetry call — actually runs.

def _redirect_telemetry(monkeypatch, tmp_path):
    target = tmp_path / "governance_llm.jsonl"

    def _emit_to_tmp(platform, task_name, **kwargs):
        return real_emit(platform, task_name, path=target, **kwargs)

    monkeypatch.setattr(gw, "_emit_telemetry", _emit_to_tmp)
    return target


def test_generate_text_emits_local_tier_telemetry_with_real_project(gateway, monkeypatch, tmp_path):
    target = _redirect_telemetry(monkeypatch, tmp_path)
    with patch("agentica_core.llm.gateway.requests.post") as post:
        post.return_value = _ollama_response({"content": "classified: yes"})
        result = gateway.generate_text("classify this", local_only=True,
                                       task_name="classify.triage", project="agentica_core")

    assert result == "classified: yes"
    rec = json.loads(target.read_text(encoding="utf-8").strip())
    assert rec["platform"] == "governance-local"
    assert rec["project"] == "agentica_core"
    assert rec["task_name"] == "classify.triage"
    assert rec["model_tier"] == "LOCAL"


def test_generate_text_emits_cloud_tier_telemetry_for_non_ollama_model(gateway, monkeypatch, tmp_path):
    target = _redirect_telemetry(monkeypatch, tmp_path)
    gateway.anthropic_key = "test-key"
    with patch("agentica_core.llm.gateway.requests.post") as post:
        post.return_value = _anthropic_response("analysis result")
        gateway.generate_text("analyze this", model="anthropic/claude-3.5-sonnet",
                              task_name="analysis", project="Order Samurai")

    rec = json.loads(target.read_text(encoding="utf-8").strip())
    assert rec["model_tier"] == "CLOUD"
    assert rec["project"] == "Order Samurai"


def test_generate_text_defaults_task_name_and_project_when_not_passed(gateway, monkeypatch, tmp_path):
    """Covers the direct gateway.generate_text() callers that don't yet pass
    either kwarg (tools/local_ui_patch.py, dashboard-ui/qa/local_audit.py,
    Order Samurai/execution/audit_remediation_patch.py, agentica_core/evals/judge.py)
    -- they must still show up, honestly bucketed, not silently invisible."""
    target = _redirect_telemetry(monkeypatch, tmp_path)
    with patch("agentica_core.llm.gateway.requests.post") as post:
        post.return_value = _ollama_response({"content": "ok"})
        gateway.generate_text("no project given", local_only=True)

    rec = json.loads(target.read_text(encoding="utf-8").strip())
    assert rec["project"] == "unknown"
    assert rec["task_name"] == "generate_text"


def test_generate_text_survives_telemetry_emission_failure(gateway, monkeypatch):
    """A telemetry-side bug must never turn a successful LLM call into a failed
    one -- this is a fire-and-forget side channel, not part of the contract."""
    monkeypatch.setattr(gw, "_emit_telemetry",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("telemetry backend exploded")))
    with patch("agentica_core.llm.gateway.requests.post") as post:
        post.return_value = _ollama_response({"content": "still works"})
        result = gateway.generate_text("hi", local_only=True)

    assert result == "still works"


def test_generate_text_emits_nothing_when_the_call_itself_fails(gateway, monkeypatch, tmp_path):
    """No result, no telemetry record -- a total failure has nothing real to
    report, and emitting a fabricated record would corrupt the metric."""
    target = _redirect_telemetry(monkeypatch, tmp_path)
    with patch("agentica_core.llm.gateway.requests.post") as post:
        post.return_value = _ollama_response({"content": ""})  # empty -> failure, not success
        with pytest.raises(Exception):
            gateway.generate_text("hi", local_only=True)

    assert not target.exists()


# ------------------------------ call_routed_llm / call_llm telemetry wiring
#
# Telemetry emission itself is fully covered above at its real source
# (generate_text). These just prove each public entry point threads its own
# project-naming kwarg through to generate_text's task_name/project -- a pure
# wiring check, generate_text mocked wholesale on purpose since re-proving
# emission here would be redundant with the tests above.

def test_call_routed_llm_passes_task_and_project_through_to_generate_text(monkeypatch):
    captured = {}

    def _fake_generate_text(self, **kw):
        captured.update(kw)
        return {"text": "ok", "model": "gemma4:4b"}

    monkeypatch.setattr(LLMGateway, "generate_text", _fake_generate_text)

    call_routed_llm("sys", "user prompt", task="analysis", project="agentica_core")

    assert captured["task_name"] == "analysis"
    assert captured["project"] == "agentica_core"


def test_call_routed_llm_defaults_project_to_unknown_when_not_passed(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        LLMGateway, "generate_text",
        lambda self, **kw: (captured.update(kw), {"text": "ok", "model": "gemma4:4b"})[1],
    )

    call_routed_llm("sys", "no project given", task="classification")

    assert captured["project"] == "unknown"


def test_call_routed_llm_emits_nothing_when_the_call_itself_fails(monkeypatch, tmp_path):
    target = tmp_path / "governance_llm.jsonl"

    def _fake_generate_text(self, **kw):
        raise RuntimeError("all providers failed")

    monkeypatch.setattr(LLMGateway, "generate_text", _fake_generate_text)

    result = call_routed_llm("sys", "user prompt", task="classification")

    assert result is None
    assert not target.exists()


def test_call_llm_passes_project_context_through_to_generate_text_as_project(gateway, monkeypatch):
    captured = {}

    def _fake_generate_text(self, **kw):
        captured.update(kw)
        return {"text": "ok", "fallback_index": 0}

    monkeypatch.setattr(LLMGateway, "generate_text", _fake_generate_text)

    gateway.call_llm("my_task", "prompt", project_context="Order Samurai")

    assert captured["task_name"] == "my_task"
    assert captured["project"] == "Order Samurai"
