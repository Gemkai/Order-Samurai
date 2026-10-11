"""Characterization of usage_records.cost() before the W3 refactor (D 22, above the plan's C
ceiling for W3 files). One case per outcome; expected dicts are the pre-refactor results
(origin/work @ f6e3a6859)."""
from __future__ import annotations

import pytest

from agentica_core.usage_records import cost

CARD = {"as_of": "2026-09-01", "models": {"opus": {
    "input": 15.0, "cache_read": 1.5, "output": 75.0, "cache_write_5m": 18.75, "cache_write_1h": 30.0},
    "partial": {"input": 3.0, "output": "x"}}}
BASE = {"model": "opus", "usage_known": True, "tokens_prompt": 1000, "tokens_completion": 200,
        "cache_read_tokens": 300, "cache_creation_tokens": 0}
UNKNOWN = {"value": None, "basis": "api_equivalent"}


def ev(**changes):
    e = dict(BASE)
    e.update(changes)
    return e


@pytest.mark.parametrize("event, card, expected", [
    (BASE, None, {**UNKNOWN, "reason": "rate_card_unavailable"}),
    (BASE, {"models": {}}, {**UNKNOWN, "reason": "rate_card_unavailable"}),
    (ev(model="mystery"), CARD, {**UNKNOWN, "reason": "model_rate_unknown"}),
    (ev(model=None), CARD, {**UNKNOWN, "reason": "model_rate_unknown"}),
    (BASE, {"as_of": "2026-09-01", "models": []}, {**UNKNOWN, "reason": "model_rate_unknown"}),
    (ev(usage_known=False), CARD, {**UNKNOWN, "reason": "usage_unknown"}),
    (ev(tokens_completion=-1), CARD, {**UNKNOWN, "reason": "invalid_usage"}),
    (ev(tokens_prompt=True), CARD, {**UNKNOWN, "reason": "invalid_usage"}),
    (ev(cache_read_tokens=2000), CARD, {**UNKNOWN, "reason": "inconsistent_cache_buckets"}),
    (ev(cache_creation_tokens=100), CARD, {**UNKNOWN, "reason": "cache_write_ttl_unknown"}),
    (ev(cache_creation_tokens=100, cache_creation_5m_tokens=60, cache_creation_1h_tokens=30), CARD,
     {**UNKNOWN, "reason": "inconsistent_cache_write_split"}),
    (ev(cache_creation_tokens=100, cache_creation_5m_tokens=-1, cache_creation_1h_tokens=101), CARD,
     {**UNKNOWN, "reason": "inconsistent_cache_write_split"}),
    (ev(model="partial", cache_read_tokens=0), CARD, {**UNKNOWN, "reason": "invalid_or_missing_rate"}),
    (BASE, CARD, {"value": (700 * 15.0 + 300 * 1.5 + 200 * 75.0) / 1_000_000, "basis": "api_equivalent",
                  "reason": None, "rate_card_as_of": "2026-09-01"}),
    (ev(cache_creation_tokens=100, cache_creation_5m_tokens=60, cache_creation_1h_tokens=40), CARD,
     {"value": (600 * 15.0 + 300 * 1.5 + 200 * 75.0 + 60 * 18.75 + 40 * 30.0) / 1_000_000,
      "basis": "api_equivalent", "reason": None, "rate_card_as_of": "2026-09-01"}),
    (ev(tokens_completion=0, cache_read_tokens=0, model="partial", tokens_prompt=10), CARD,
     {"value": 10 * 3.0 / 1_000_000, "basis": "api_equivalent", "reason": None,
      "rate_card_as_of": "2026-09-01"}),
])
def test_cost_matches_the_pre_refactor_result(event, card, expected):
    assert cost(event, card) == pytest.approx(expected) if expected["value"] else cost(event, card) == expected
