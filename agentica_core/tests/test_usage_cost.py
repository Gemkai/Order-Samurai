"""Acceptance tests for requirement 3: usage_records.cost(event, rate_card).

Contract: cost() returns {'value': number|None, 'basis': 'api_equivalent',
'reason': str|None}. Ordinary input = tokens_prompt - cache_read_tokens -
cache_creation_tokens (both platforms' tokens_prompt already includes cache
per the module docstring). Unknown model or an unresolved cache-write TTL
split when cache_creation_tokens > 0 must yield value=None, never 0. Existing
FIELDS sums (usage_records.FIELDS) must not change shape when new presence
metadata (usage_known, cache_creation_5m_tokens, cache_creation_1h_tokens) is
added to parser output. No source edits made here; RED until cost() and the
presence metadata exist.
"""
from agentica_core import usage_records as usage

RATE_CARD = {
    "as_of": "2026-09-01",
    "source_urls": ["https://platform.claude.com/docs/en/build-with-claude/prompt-caching"],
    "models": {
        "claude-sonnet-5": {
            "input": 3.0, "cache_read": 0.3, "cache_write_5m": 3.75,
            "cache_write_1h": 6.0, "output": 15.0,
        },
    },
}


def _event(**overrides):
    base = dict(model="claude-sonnet-5", tokens_prompt=1000, tokens_completion=100,
                cache_read_tokens=0, cache_creation_tokens=0, usage_known=True)
    base.update(overrides)
    return base


def test_cost_unknown_model_returns_none_with_reason():
    result = usage.cost(_event(model="some-untracked-model"), RATE_CARD)
    assert result["value"] is None
    assert result["basis"] == "api_equivalent"
    assert result["reason"]


def test_cost_known_model_zero_writes_computes_exact_value():
    event = _event(tokens_prompt=1000, cache_read_tokens=200, cache_creation_tokens=0,
                   tokens_completion=100)
    result = usage.cost(event, RATE_CARD)
    ordinary_input = 1000 - 200 - 0
    expected = (ordinary_input * 3.0 + 200 * 0.3 + 100 * 15.0) / 1_000_000
    assert result["value"] == expected
    assert result["basis"] == "api_equivalent"


def test_cost_writes_present_but_ttl_split_unknown_returns_none():
    event = _event(tokens_prompt=1000, cache_read_tokens=0, cache_creation_tokens=50,
                   usage_known=True)
    # No cache_creation_5m_tokens/cache_creation_1h_tokens supplied: split unresolved.
    result = usage.cost(event, RATE_CARD)
    assert result["value"] is None
    assert "ttl" in result["reason"].lower() or "unknown" in result["reason"].lower()


def test_cost_writes_present_with_known_ttl_split_computes_value():
    event = _event(tokens_prompt=1000, cache_read_tokens=0, cache_creation_tokens=50,
                   cache_creation_5m_tokens=30, cache_creation_1h_tokens=20)
    result = usage.cost(event, RATE_CARD)
    ordinary_input = 1000 - 0 - 50
    expected = (ordinary_input * 3.0 + 30 * 3.75 + 20 * 6.0 + 100 * 15.0) / 1_000_000
    assert result["value"] == expected


def test_cost_invalid_negative_counts_return_none():
    result = usage.cost(_event(tokens_prompt=-5), RATE_CARD)
    assert result["value"] is None


def test_cost_usage_known_false_returns_none():
    """An explicit usage_known=False (parser saw no usage block at all) must
    not be priced as if zero tokens were used."""
    result = usage.cost(_event(usage_known=False), RATE_CARD)
    assert result["value"] is None
    assert result["reason"]


def test_cost_overcommitted_cache_write_ttl_buckets_return_none():
    """cache_creation_5m_tokens + cache_creation_1h_tokens (80) exceeding the
    reported cache_creation_tokens total (50) is an inconsistent split, not a
    priceable one -- must not silently sum to a fabricated total."""
    event = _event(tokens_prompt=1000, cache_read_tokens=0, cache_creation_tokens=50,
                   cache_creation_5m_tokens=40, cache_creation_1h_tokens=40)
    result = usage.cost(event, RATE_CARD)
    assert result["value"] is None
    assert result["reason"]


def test_claude_parser_adds_presence_metadata_without_changing_existing_sums(tmp_path):
    """RED on the new key; PASS-today assertions guard the unchanged sums."""
    session = tmp_path / "session.jsonl"
    session.write_text(
        '{"cwd": "/repo", "type": "assistant", "message": {"id": "req1", "model": '
        '"claude-sonnet-5", "usage": {"input_tokens": 100, "cache_read_input_tokens": 50, '
        '"cache_creation_input_tokens": 20, "output_tokens": 10}, "content": []}}\n')
    _, _, _, events = usage._claude(session)
    usage_events = [e for e in events if "tokens_prompt" in e]
    assert len(usage_events) == 1
    event = usage_events[0]
    # Existing accounting is unchanged: 100 + 50 + 20 = 170.
    assert event["tokens_prompt"] == 170
    assert event["cache_read_tokens"] == 50
    assert event["cache_creation_tokens"] == 20
    # New presence metadata (definition 3) is additive.
    assert event.get("usage_known") is True
