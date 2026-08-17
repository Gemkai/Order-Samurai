"""Tests for the one authoritative reflex-id parser (LEDGER-003).

Every id string below is a shape that actually occurs in exec_log.jsonl /
SENSEI_LEDGER.jsonl / autonomic_events.jsonl — not an invented one.
"""
from agentica_core.reflex_id import parse_reflex_id


# ---------------------------------------------------------------------------
# metric: / trajectory: — the only kinds that carry a pillar and name a metric
# ---------------------------------------------------------------------------

def test_metric_id():
    assert parse_reflex_id("metric:bow:Error_Rate") == ("metric", "bow", "Error_Rate")


def test_trajectory_id():
    assert parse_reflex_id("trajectory:arts:Slop_Density") == (
        "trajectory", "arts", "Slop_Density")


def test_metric_name_with_colon_is_not_truncated():
    assert parse_reflex_id("metric:brush:A:B").metric == "A:B"


def test_empty_pillar_segment_is_none_not_empty_string():
    assert parse_reflex_id("metric::Error_Rate").pillar is None


# ---------------------------------------------------------------------------
# correlation: / manual: — segment 2 is a LABEL, never a pillar or a metric.
# Conflating them is the 2026-08-14 bug this parser exists to prevent.
# ---------------------------------------------------------------------------

def test_correlation_id_has_no_pillar_or_metric():
    assert parse_reflex_id("correlation:cost_and_quality_tradeoff") == (
        "correlation", None, None)


def test_manual_id_has_no_pillar_or_metric():
    assert parse_reflex_id("manual:wiki") == ("manual", None, None)


# ---------------------------------------------------------------------------
# Malformed — reported as "unknown", never as a metric with missing parts
# ---------------------------------------------------------------------------

def test_metric_prefix_without_metric_segment_is_unknown():
    assert parse_reflex_id("metric:bow") == ("unknown", None, None)


def test_bare_token_is_unknown():
    assert parse_reflex_id("only_one") == ("unknown", None, None)


def test_empty_string_is_unknown():
    assert parse_reflex_id("") == ("unknown", None, None)


def test_label_kind_without_label_is_unknown():
    assert parse_reflex_id("correlation:") == ("unknown", None, None)
