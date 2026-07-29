"""Model-agnostic LLM router for Order Samurai scouts and mechanisms.

Fallback chain (tried in order, first success wins):
  1. Claude (Anthropic API)            — env: ANTHROPIC_API_KEY
  2. Antigravity / Gemini (Google AI)  — env: GOOGLE_API_KEY or GEMINI_API_KEY
  3. Local Ollama                      — http://localhost:11434/v1 (no key needed)
  4. OpenRouter free tier              — env: OPENROUTER_API_KEY

Task types (controls which model tier is selected per backend):
  "classification"  — fast/cheap; code scoring, quick checks, pattern matching
  "analysis"        — capable; clustering, multi-step reasoning, chain proposal

Usage:
    from agentica_core.model_router import call_llm
    text = call_llm(system="You are ...", user="Analyse this...", task="analysis")
    if text is None:
        # all backends failed or unavailable

Sensitive data: pass local_only=True — the chain is then Ollama only and fails
closed (returns None) instead of falling back to any cloud backend.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from agentica_core.brain_context import load_brain_context
from agentica_core.llm.local_guards import (
    LOCAL_TIMEOUT_SEC,
    extract_message_text,
    floor_max_tokens,
)

# Cloud-backend timeout. Quality-first: 60s gives the strongest models headroom
# to finish a full analysis response rather than being cut off mid-generation.
# Local Ollama uses LOCAL_TIMEOUT_SEC (180s) for the same reason on cold loads.
_TIMEOUT_S = 60

_OLLAMA_BASE = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/") + "/v1"

# Quality-first roster + fallback order. The chain (call_llm below) tries Claude
# first, then Gemini, then local Ollama, then OpenRouter — highest-capability
# provider first, cloud-free models only as a last resort. Where the two model
# families differed, the stronger option was chosen: Gemini 2.5 (not the stale
# 2.0 pin) and Llama 3.3-70B (not 3.1-8B) on the free tier. Analysis tasks route
# to each provider's strongest model; classification stays on the efficient tier
# (haiku/flash are already high-quality for that simple task).
_MODELS: dict[str, dict[str, str]] = {
    "claude": {
        "classification": "claude-haiku-4-5-20251001",
        "analysis": "claude-sonnet-4-6",
    },
    "gemini": {
        "classification": "gemini-2.5-flash",
        "analysis": "gemini-2.5-flash",
    },
    "ollama": {
        "classification": "gemma4:4b",
        "analysis": "qwen3.6:35b",
    },
    "openrouter": {
        "classification": "meta-llama/llama-3.3-70b-instruct:free",
        "analysis": "meta-llama/llama-3.3-70b-instruct:free",
    },
}


def call_llm(
    system: str,
    user: str,
    task: str = "classification",
    max_tokens: int = 2048,
    temperature: float = 0.0,
    local_only: bool = False,
    brain: bool = False,
) -> str | None:
    """Call the best available LLM with an automatic fallback chain.

    Returns the response text, or None when every backend is unavailable or errors.

    local_only=True restricts the chain to local Ollama and fails closed:
    prompts carrying sensitive data must never fall back to a cloud backend,
    so an Ollama failure returns None rather than retrying in the cloud.

    brain=True prepends the shared Brain³ context (the Knowledge/vault/me/ identity
    portfolio + long-term-memory index) to the system prompt, so any backend — local
    Ollama included — plugs into the same shared brain the file-reading harnesses use.
    A missing brain yields an empty preamble (no-op), never an error.
    """
    if brain:
        preamble = load_brain_context()
        if preamble:
            system = f"{preamble}\n\n---\n\n{system}"
    backends = (
        (_call_ollama,)
        if local_only
        else (_call_claude, _call_gemini, _call_ollama, _call_openrouter)
    )
    for backend in backends:
        try:
            result = backend(system, user, task, max_tokens, temperature)
            if result:
                return result
        except Exception:
            continue
    return None


def _call_claude(
    system: str, user: str, task: str, max_tokens: int, temperature: float
) -> str | None:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None
    payload = json.dumps({
        "model": _MODELS["claude"][task],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
        body = json.loads(resp.read())
        return body["content"][0]["text"]


def _call_gemini(
    system: str, user: str, task: str, max_tokens: int, temperature: float
) -> str | None:
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return None
    model_id = _MODELS["gemini"][task]
    payload = json.dumps({
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"parts": [{"text": user}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": temperature,
        },
    }).encode()
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models"
        f"/{model_id}:generateContent"
    )
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
        body = json.loads(resp.read())
        return body["candidates"][0]["content"]["parts"][0]["text"]


def _call_ollama(
    system: str, user: str, task: str, max_tokens: int, temperature: float
) -> str | None:
    payload = json.dumps({
        "model": _MODELS["ollama"][task],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": floor_max_tokens(max_tokens),
    }).encode()
    req = urllib.request.Request(
        f"{_OLLAMA_BASE}/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=LOCAL_TIMEOUT_SEC) as resp:
        body = json.loads(resp.read())
        # thinking models (qwen3.6, deepseek-r1) leave content empty on the
        # OpenAI-compat endpoint and put output in reasoning/thinking —
        # extract_message_text handles the fallback per the CLAUDE.md caveat
        content = extract_message_text(body["choices"][0]["message"])
        return content if content else None


def _call_openrouter(
    system: str, user: str, task: str, max_tokens: int, temperature: float
) -> str | None:
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        return None
    payload = json.dumps({
        "model": _MODELS["openrouter"][task],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://github.com/order-samurai",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
        body = json.loads(resp.read())
        content = body["choices"][0]["message"]["content"].strip()
        return content if content else None
