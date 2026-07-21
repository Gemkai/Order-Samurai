"""Shared reliability guards for local Ollama calls (CLAUDE.md "Local LLM Routing").

One authoritative implementation of the two rules every local call must follow:
  1. max_tokens >= 512 — smaller budgets truncate thinking models into
     unparseable output.
  2. Read thinking/reasoning as a fallback when content is empty — thinking
     models (deepseek-r1, qwen3.6) can return the answer there; missing this
     made the local tier look silently dead for a month.

Callers: agentica_core/llm/gateway.py and agentica_core/model_router.py.
governance_review.py keeps an inline copy by design — it is a standalone
stdlib-only script; keep its logic in sync with this module.
"""
from __future__ import annotations

MIN_LOCAL_TOKENS = 512


def floor_max_tokens(requested: object) -> int:
    """Clamp a requested token budget to the local-call floor."""
    try:
        n = int(requested or 0)
    except (TypeError, ValueError):
        n = 0
    return max(n, MIN_LOCAL_TOKENS)


def extract_message_text(message: dict) -> str:
    """Message text with thinking-model fallback; '' when the reply is truly empty.

    Callers must treat '' as a failure (raise, return None, or fall through to
    the next backend) — never as a valid empty answer.
    """
    return (
        (message.get("content") or "").strip()
        or (message.get("thinking") or "").strip()
        or (message.get("reasoning") or "").strip()
    )
