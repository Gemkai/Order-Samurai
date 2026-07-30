"""Contract tests for the Python->TS payload seam (P4).

validate_payload / write_payload are the only enforcement point before the TS
reflex-engine's chokidar watcher reads wid_payload.json — an invalid envelope
must be rejected BEFORE anything reaches disk (fail fast on write).
"""
import json

import pytest
from jsonschema import ValidationError

from agentica_core import aggregate as agg

TS = "2026-07-18T00:00:00+00:00"


def _minimal_payload() -> dict:
    return {
        "schema_version": "agentica.1",
        "timestamp": TS,
        "reflexes": [
            {"id": "Error_Rate", "tier": "HIGH", "command": "/debug",
             "status": "FAIL", "source": "bow"},
        ],
    }


def test_aggregate_payload_validates_and_round_trips(tmp_path):
    payload = agg.aggregate(timestamp=TS)
    target = tmp_path / "wid_payload.json"
    assert agg.write_payload(payload, target) == target
    loaded = json.loads(target.read_text(encoding="utf-8"))
    agg.validate_payload(loaded)
    assert loaded["schema_version"] == "agentica.1"


def test_missing_required_key_rejected_before_write(tmp_path):
    payload = _minimal_payload()
    del payload["reflexes"]
    target = tmp_path / "wid_payload.json"
    with pytest.raises(ValidationError):
        agg.write_payload(payload, target)
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_wrong_schema_version_rejected(tmp_path):
    payload = _minimal_payload()
    payload["schema_version"] = "agentica.2"
    target = tmp_path / "wid_payload.json"
    with pytest.raises(ValidationError):
        agg.write_payload(payload, target)
    assert not target.exists()


def test_malformed_reflex_entry_rejected(tmp_path):
    payload = _minimal_payload()
    payload["reflexes"] = [
        {"id": "Error_Rate", "tier": "URGENT", "command": "/debug",
         "status": "FAIL", "source": "bow"},
    ]
    target = tmp_path / "wid_payload.json"
    with pytest.raises(ValidationError):
        agg.write_payload(payload, target)
    assert not target.exists()
