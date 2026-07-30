"""The 24h "now tracking" clause must prioritize GRADED new metrics.

new_metrics keys are lower-cased for display (insights.py:569) while METRIC_CONFIG
keys are Title_Case or acronym-cased (MCP_Smoke_Fails) — a naive re-underscore
lookup never matches, leaving graded_new permanently empty (dead branch).
"""
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentica_core import insights


def _store(rows):
    tmp = tempfile.NamedTemporaryFile(  # noqa: SIM115 — path outlives the handle on purpose
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8")
    for r in rows:
        tmp.write(json.dumps(r) + "\n")
    tmp.close()
    return Path(tmp.name)


def test_graded_new_metric_outranks_ungraded_in_now_tracking():
    now = datetime.now(timezone.utc)
    store = _store([
        {"ts": (now - timedelta(hours=30)).isoformat(),
         "values": {"bow/Session_Count": 4}},
        {"ts": (now - timedelta(hours=1)).isoformat(),
         # Two first-ever metrics: one graded acronym key, one ungraded fake.
         "values": {"bow/Session_Count": 4,
                    "bow/MCP_Smoke_Fails": 3,
                    "bow/Aaa_Fake_Ungraded": 5}},
    ])
    try:
        clause = insights._24h_clause("bow", store)
        assert "mcp smoke fails" in clause
        # graded_new is non-empty, so the ungraded metric must NOT crowd the cap.
        assert "aaa fake ungraded" not in clause
    finally:
        store.unlink()


def test_large_deltas_are_human_formatted_not_raw_digits():
    now = datetime.now(timezone.utc)
    store = _store([
        {"ts": (now - timedelta(hours=30)).isoformat(),
         "values": {"brush/Total_Cost": 100.0, "brush/Token_Spend": 1_000_000.0}},
        {"ts": (now - timedelta(hours=1)).isoformat(),
         "values": {"brush/Total_Cost": 10_600.0, "brush/Token_Spend": 101_500_000.0}},
    ])
    try:
        clause = insights._24h_clause("brush", store)
        assert "$10,500.00" in clause      # cost delta shown as dollars
        assert "100.5M" in clause          # token count M-scaled
        assert "100500000" not in clause   # never the raw integer
    finally:
        store.unlink()


def test_per_session_metric_delta_is_not_labelled_worsened():
    # Rule_Violations is judged per session; its raw-total 24h delta is confounded by
    # session volume, so the clause reports movement without an improved/worsened verdict.
    now = datetime.now(timezone.utc)
    store = _store([
        {"ts": (now - timedelta(hours=30)).isoformat(),
         "values": {"sword/Rule_Violations": 100.0}},
        {"ts": (now - timedelta(hours=1)).isoformat(),
         "values": {"sword/Rule_Violations": 939.0}},
    ])
    try:
        clause = insights._24h_clause("sword", store)
        assert "rule violations moved up" in clause
        assert "worsened" not in clause
    finally:
        store.unlink()


def test_rising_cumulative_total_is_not_labelled_worsened():
    now = datetime.now(timezone.utc)
    store = _store([
        {"ts": (now - timedelta(hours=30)).isoformat(),
         "values": {"brush/Total_Cost": 100.0}},
        {"ts": (now - timedelta(hours=1)).isoformat(),
         "values": {"brush/Total_Cost": 5_100.0}},
    ])
    try:
        clause = insights._24h_clause("brush", store)
        assert "total cost moved up" in clause
        # A cumulative total rising is driven by work volume, not a regression.
        assert "worsened" not in clause
    finally:
        store.unlink()
