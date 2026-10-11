"""Characterization of usage_records.window() before the W3 refactor.

Plan: docs/plans/2026-10-07-order-samurai-hotspot-split/PLAN.md, W3. window() is CC 48 and
is reached through enrich() from knowledge_metrics. These cases pin its output for fixed
records and an explicit `end`, with model_tier() pinned to a stub table so a change to the
real tier map cannot move the snapshot. Expected output lives in
fixtures/usage_records_window_snapshots.json, recorded from the pre-refactor code
(origin/work @ f6e3a6859); never regenerate it to make a refactor pass.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentica_core import usage_records as ur

SNAPSHOTS = Path(__file__).with_name("fixtures") / "usage_records_window_snapshots.json"
END = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
START = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
TIERS = {"opus": "PREMIUM", "sonnet": "STANDARD", "local": "LOCAL"}


def event(stamp: str, model: str | None, **values) -> dict:
    e = {"id": f"usage:{stamp}:{model}", "timestamp": stamp, "model": model}
    e.update(values)
    return e


def mixed_session() -> dict:
    return {
        "session_id": "s1", "platform": "claude", "project": "p", "timestamp": "old",
        "_usage_events": [
            event("2026-09-01T10:00:00Z", "opus", tokens_prompt=100, tokens_completion=20,
                  cache_read_tokens=5, usage_known=True, context_max=900),
            event("2026-09-01T11:00:00+00:00", "sonnet", tokens_prompt=50,
                  cache_creation_tokens=7, usage_known=False, context_max=400),
            event("2026-09-01T12:00:00Z", "opus", tool_calls=1, tool_name="Bash",
                  output_words=0),
            event("2026-09-01T12:30:00", None, output_words=30, turns=1),
            event("2026-08-30T09:00:00Z", "opus", tokens_prompt=999),        # before START
            event("2026-09-03T09:00:00Z", "opus", tokens_prompt=999),        # after END
            event("not-a-date", "opus", tokens_prompt=999),
            event("2026-09-01T13:00:00Z", "local", tokens_prompt=-5, subagent_spawns=float("nan")),
        ],
    }


CASES = {
    "plain_records_without_events": dict(records=[
        {"session_id": "a", "timestamp": "2026-09-01T09:00:00Z", "tokens_prompt": 3},
        {"session_id": "b", "timestamp": "2026-08-01T09:00:00Z"},
        {"session_id": "c", "timestamp": "2026-09-05T09:00:00Z"},
        {"session_id": "d", "timestamp": None},
    ], start=START),
    "plain_records_no_start": dict(records=[
        {"session_id": "b", "timestamp": "2026-08-01T09:00:00Z"},
    ], start=None),
    "mixed_models_windowed": dict(records=[mixed_session()], start=START),
    "mixed_models_unbounded_start": dict(records=[mixed_session()], start=None),
    "single_model_all_known": dict(records=[{
        "session_id": "s2", "_usage_events": [
            event("2026-09-01T10:00:00Z", "sonnet", tokens_prompt=10, usage_known=True),
            event("2026-09-01T10:05:00Z", "sonnet", tokens_completion=4, usage_known=True),
        ]}], start=START),
    "events_outside_window_dropped": dict(records=[{
        "session_id": "s3", "_usage_events": [
            event("2026-08-01T10:00:00Z", "sonnet", tokens_prompt=10)]}], start=START),
    "no_billable_usage_dropped": dict(records=[{
        "session_id": "s4", "_usage_events": [
            event("2026-09-01T10:00:00Z", "opus", cache_read_tokens=10, usage_known=True),
            event("2026-09-01T10:01:00Z", None, turns=2)]}], start=START),
    "no_usage_fields_only_activity": dict(records=[{
        "session_id": "s5", "_usage_events": [
            event("2026-09-01T10:00:00Z", "opus", tool_calls=2, tool_name="Read")]}],
        start=START),
}


def run_case(name: str, monkeypatch: pytest.MonkeyPatch) -> list:
    monkeypatch.setattr(ur, "model_tier", lambda m: TIERS.get(m or "", "UNKNOWN"))
    spec = CASES[name]
    out = ur.window(json.loads(json.dumps(spec["records"])), start=spec["start"], end=END)
    return json.loads(json.dumps(out, sort_keys=True, default=str))


@pytest.mark.parametrize("name", sorted(CASES))
def test_window_matches_the_pre_refactor_snapshot(name, monkeypatch):
    expected = json.loads(SNAPSHOTS.read_text(encoding="utf-8"))[name]
    assert run_case(name, monkeypatch) == expected


def test_every_case_has_a_recorded_snapshot():
    assert sorted(json.loads(SNAPSHOTS.read_text(encoding="utf-8"))) == sorted(CASES)


def test_end_defaults_to_now(monkeypatch):
    """With no `end`, a record stamped in the future is excluded and one in the past kept."""
    monkeypatch.setattr(ur, "model_tier", lambda m: "STANDARD")
    past = {"session_id": "p", "timestamp": "2020-01-01T00:00:00Z"}
    future = {"session_id": "f", "timestamp": "2999-01-01T00:00:00Z"}
    assert ur.window([past, future]) == [past]


def test_input_records_are_not_mutated(monkeypatch):
    monkeypatch.setattr(ur, "model_tier", lambda m: TIERS.get(m or "", "UNKNOWN"))
    records = [mixed_session()]
    before = json.dumps(records, sort_keys=True, default=str)
    ur.window(records, start=START, end=END)
    assert json.dumps(records, sort_keys=True, default=str) == before
