import json

import pytest

from agentica_core import (
    TelemetryValidationError,
    append_entry,
    normalize_entry,
    validate_entry,
    validate_metric,
)

# A real record from Jarvis telemetry.jsonl (schema 2026-04-07.1) — note: no `platform` field.
JARVIS_SAMPLE = {
    "schema_version": "2026-04-07.1",
    "timestamp": "2026-06-01T13:03:04.045727Z",
    "session_id": "local-session",
    "project": "HUD",
    "task_name": "wid_narrative_insights",
    "model_tier": "FAST",
    "latency_ms": 19118.16,
    "tokens_prompt": 47910,
    "tokens_completion": 2287,
    "total_cost": 0.052484,
    "status": "success",
    "error": None,
    "tool_calls": 0,
    "tool_calls_list": [],
    "tool_latencies": [],
    "mod_type": "READ",
    "skill_hits": 0,
}

VALID_METRIC = {
    "val": "98.5", "delta": "+0.3", "trend": "up", "history": [97, 98, 98, 98, 99, 98, 98],
    "is_percent": True, "is_count": False, "is_simulated": False, "tier": "AUTO",
    "timestamp": "2026-06-01 13:03",
}


def test_real_jarvis_sample_validates_after_normalize():
    entry = normalize_entry(JARVIS_SAMPLE, platform="antigravity")
    assert entry["platform"] == "antigravity"
    validate_entry(entry)  # must not raise


def test_missing_total_cost_rejected():
    entry = normalize_entry(JARVIS_SAMPLE, platform="antigravity")
    del entry["total_cost"]
    with pytest.raises(TelemetryValidationError):
        validate_entry(entry)


def test_missing_platform_rejected():
    entry = normalize_entry(JARVIS_SAMPLE)  # no platform passed
    with pytest.raises(TelemetryValidationError):
        validate_entry(entry)


def test_bad_status_rejected():
    entry = normalize_entry(JARVIS_SAMPLE, platform="claude")
    entry["status"] = "weird"
    with pytest.raises(TelemetryValidationError):
        validate_entry(entry)


def test_bool_rejected_for_numeric_field():
    entry = normalize_entry(JARVIS_SAMPLE, platform="claude")
    entry["tokens_prompt"] = True  # bool is an int subclass — must be rejected
    with pytest.raises(TelemetryValidationError):
        validate_entry(entry)


def test_model_tier_coerced_to_string():
    raw = dict(JARVIS_SAMPLE)
    raw["model_tier"] = 3  # a platform might emit an int tier
    entry = normalize_entry(raw, platform="claude")
    assert entry["model_tier"] == "3"
    validate_entry(entry)


def test_metric_valid():
    validate_metric(VALID_METRIC)  # must not raise


def test_metric_missing_tier_rejected():
    node = dict(VALID_METRIC)
    del node["tier"]
    with pytest.raises(TelemetryValidationError):
        validate_metric(node)


def test_metric_bad_tier_rejected():
    node = dict(VALID_METRIC, tier="FAKE")
    with pytest.raises(TelemetryValidationError):
        validate_metric(node)


def test_append_entry_roundtrip_and_creates_dirs(tmp_path):
    entry = normalize_entry(JARVIS_SAMPLE, platform="antigravity")
    target = tmp_path / "nested" / "telemetry.jsonl"
    written = append_entry(entry, path=target)
    assert written == target
    lines = target.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["platform"] == "antigravity"


def test_append_entry_rejects_invalid_and_writes_nothing(tmp_path):
    target = tmp_path / "telemetry.jsonl"
    with pytest.raises(TelemetryValidationError):
        append_entry({"bogus": 1}, path=target)
    assert not target.exists()
