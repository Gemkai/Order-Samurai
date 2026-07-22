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
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from agentica_core.llm.local_guards import extract_message_text, floor_max_tokens

_TIMEOUT_S = 30

_OLLAMA_BASE = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/") + "/v1"

_MODELS: dict[str, dict[str, str]] = {
    "claude": {
        "classification": "claude-haiku-4-5-20251001",
        "analysis": "claude-sonnet-4-6",
    },
    "gemini": {
        "classification": "gemini-2.0-flash",
        "analysis": "gemini-2.5-flash",
    },
    "ollama": {
        "classification": "gemma4:4b",
        "analysis": "qwen3.6:35b",
    },
    "openrouter": {
        "classification": "google/gemma-2-9b-it:free",
        "analysis": "meta-llama/llama-3.1-8b-instruct:free",
    },
}


def call_llm(
    system: str,
    user: str,
    task: str = "classification",
    max_tokens: int = 2048,
    temperature: float = 0.0,
) -> str | None:
    """Call the best available LLM with an automatic fallback chain.

    Returns the response text, or None when every backend is unavailable or errors.
    """
    for backend in (_call_claude, _call_gemini, _call_ollama, _call_openrouter):
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
        f"/{model_id}:generateContent?key={api_key}"
    )
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
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
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
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
            "HTTP-Referer": "https://github.com/Gemkai/order-samurai",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
        body = json.loads(resp.read())
        content = body["choices"][0]["message"]["content"].strip()
        return content if content else None
