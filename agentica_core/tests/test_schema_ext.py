"""Tests for the orchestration-level schema extension: optional agent-operation fields
(backward-compatible) and the autonomic_events stream."""
import json

import pytest

from agentica_core import (
    TelemetryValidationError,
    append_event,
    normalize_entry,
    validate_entry,
    validate_event,
)

BASE = {
    "schema_version": "agentica.1", "timestamp": "2026-06-01T00:00:00Z", "platform": "claude",
    "project": "OrderSamurai", "task_name": "t", "model_tier": "FAST", "latency_ms": 10.0,
    "tokens_prompt": 100, "tokens_completion": 50, "total_cost": 0.01, "status": "success",
}


def test_minimal_record_no_optional_fields_validates():
    validate_entry(dict(BASE))  # backward compat: no agent-operation fields present


def test_orchestration_fields_validate_when_present():
    entry = dict(BASE, orchestrator="Brush", chain_depth=3, subagent_spawns=2,
                 parent_task="root", knowledge_refs=5, cache_read_tokens=2048,
                 model="claude-opus", skill_tier="pipeline", mcp_or_cli="cli",
                 phase="Implementation", approved=True, outcome_ref="PR#42")
    validate_entry(entry)  # must not raise


def test_wrong_type_optional_field_rejected():
    with pytest.raises(TelemetryValidationError):
        validate_entry(dict(BASE, chain_depth="three"))


def test_int_optional_field_rejects_bool():
    with pytest.raises(TelemetryValidationError):
        validate_entry(dict(BASE, subagent_spawns=True))


def test_approved_accepts_bool():
    validate_entry(dict(BASE, approved=False))  # approved is bool by design


def test_null_optional_field_is_ignored():
    validate_entry(dict(BASE, orchestrator=None, chain_depth=None))


# --- autonomic events ---

def test_valid_event():
    validate_event({"timestamp": "2026-06-01T00:00:00Z", "event": "zombie_killed",
                    "pillar": "bow", "detail": "pid 1234", "duration_ms": 12})


def test_unknown_event_rejected():
    with pytest.raises(TelemetryValidationError):
        validate_event({"timestamp": "t", "event": "not_a_real_event"})


def test_event_missing_timestamp_rejected():
    with pytest.raises(TelemetryValidationError):
        validate_event({"event": "heal"})


def test_append_event_roundtrip(tmp_path):
    target = tmp_path / "autonomic_events.jsonl"
    append_event({"timestamp": "2026-06-01T00:00:00Z", "event": "loop_breaker_fire"}, path=target)
    event = json.loads(target.read_text(encoding="utf-8").strip())
    assert event["event"] == "loop_breaker_fire"


def test_append_event_rejects_invalid_and_writes_nothing(tmp_path):
    target = tmp_path / "ev.jsonl"
    with pytest.raises(TelemetryValidationError):
        append_event({"event": "bogus"}, path=target)
    assert not target.exists()
