"""Drift guards for the single canonical router plus compatibility facade."""
from agentica_core import model_router
from agentica_core.llm import gateway

SONNET = "claude-sonnet-4-6"


def test_both_routers_use_the_same_current_sonnet():
    assert model_router._MODELS is gateway.ROUTED_MODELS
    assert gateway.ROUTED_MODELS["claude"]["analysis"] == SONNET
    assert SONNET in gateway.ANTHROPIC_MODEL_ALIASES


def test_gateway_premium_is_claude_first():
    # Quality-first: the strongest Claude leads the premium chain.
    assert gateway.PREMIUM_CHAIN[0] == f"anthropic/{SONNET}"


def test_model_router_is_a_facade_not_a_second_implementation():
    assert model_router.__router_facade__ is True
    assert model_router.call_llm is gateway.call_routed_llm


def test_cloud_timeout_gives_quality_headroom():
    assert model_router._TIMEOUT_S == gateway.CLOUD_TIMEOUT_SEC >= 60
