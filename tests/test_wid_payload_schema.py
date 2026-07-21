"""Tests for the P4 typed wid_payload envelope (write side).

agentica_core.aggregate.validate_payload validates the envelope against
schema/wid_payload.schema.json before it is persisted, so a malformed payload
never reaches disk. The TS reflex-engine validates the SAME schema on startup —
these tests pin the Python half of that cross-language seam.
"""
from __future__ import annotations

import sys
from pathlib import Path

import jsonschema
import pytest

# agentica_core lives in the canonical Governance kernel (parents[2]).
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
_GOVERNANCE = Path(__file__).resolve().parents[2]
if str(_GOVERNANCE) not in sys.path:
    sys.path.insert(0, str(_GOVERNANCE))

from agentica_core.aggregate import (  # noqa: E402
    _WID_PAYLOAD_SCHEMA_PATH,
    validate_payload,
)


def _reflex(**over: object) -> dict:
    base = {
        "id": "metric:bow:Agent_Autonomy_Ratio",
        "tier": "CRITICAL",
        "command": "/sensei-cycle",
        "status": "active",
        "source": "metric",
    }
    base.update(over)
    return base


def _payload(**over: object) -> dict:
    base = {
        "schema_version": "agentica.1",
        "timestamp": "2026-06-28T00:00:00+00:00",
        "reflexes": [_reflex()],
    }
    base.update(over)
    return base


# ── Schema sanity ─────────────────────────────────────────────────────────────

def test_schema_file_is_valid_draft7():
    import json
    schema = json.loads(_WID_PAYLOAD_SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator.check_schema(schema)


# ── Well-formed envelopes validate clean ──────────────────────────────────────

def test_minimal_valid_payload_passes():
    validate_payload(_payload())  # must not raise


def test_extra_top_level_keys_are_allowed():
    # The real payload carries ~20 dashboard keys; the seam stays open.
    validate_payload(_payload(pillars={}, architecture={}, needs_attention={}))


def test_empty_reflexes_is_valid():
    validate_payload(_payload(reflexes=[]))


def test_extra_reflex_keys_are_allowed():
    validate_payload(_payload(reflexes=[_reflex(maturity="APPLY", reflex_ready=True,
                                                mechanism_status="no_mechanism")]))


# ── Malformed envelopes fail fast ─────────────────────────────────────────────

@pytest.mark.parametrize("mutate", [
    pytest.param(lambda p: p.pop("reflexes"), id="missing-reflexes"),
    pytest.param(lambda p: p.pop("schema_version"), id="missing-schema-version"),
    pytest.param(lambda p: p.pop("timestamp"), id="missing-timestamp"),
    pytest.param(lambda p: p.__setitem__("schema_version", "agentica.2"), id="wrong-version"),
    pytest.param(lambda p: p.__setitem__("reflexes", [_reflex(tier="URGENT")]), id="bad-tier"),
    pytest.param(lambda p: p.__setitem__("reflexes", [_reflex(command="")]), id="empty-command"),
    pytest.param(lambda p: p["reflexes"][0].pop("source"), id="reflex-missing-source"),
    pytest.param(lambda p: p.__setitem__("reflexes", "not-a-list"), id="reflexes-not-array"),
])
def test_malformed_payload_raises(mutate):
    payload = _payload()
    mutate(payload)
    with pytest.raises(jsonschema.ValidationError):
        validate_payload(payload)
