"""Drift guard: model_router and llm.gateway must stay quality-first-consistent.

The two routers historically drifted (different Claude versions, different
fallback order). Rather than merge them (a hot-path refactor), this test fails
loudly if their quality-first roster/order stops agreeing — cheap insurance
against silent divergence.
"""
from agentica_core import model_router
from agentica_core.llm import gateway

SONNET = "claude-sonnet-4-6"


def test_both_routers_use_the_same_current_sonnet():
    assert model_router._MODELS["claude"]["analysis"] == SONNET
    # gateway resolves this alias for the direct-Anthropic path
    assert SONNET in gateway.ANTHROPIC_MODEL_ALIASES


def test_gateway_premium_is_claude_first():
    # Quality-first: the strongest Claude leads the premium chain.
    assert gateway.PREMIUM_CHAIN[0] == f"anthropic/{SONNET}"


def test_model_router_fallback_order_is_claude_first():
    # call_llm builds (claude, gemini, ollama, openrouter) for the cloud path.
    import inspect
    src = inspect.getsource(model_router.call_llm)
    order = [b for b in ("_call_claude", "_call_gemini", "_call_ollama", "_call_openrouter") if b in src]
    assert order[0] == "_call_claude", "Claude must be tried first (quality-first)"


def test_cloud_timeout_gives_quality_headroom():
    assert model_router._TIMEOUT_S >= 60
