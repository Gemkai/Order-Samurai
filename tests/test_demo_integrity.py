#!/usr/bin/env python3
"""Assures structural integrity and cleanliness of the synthetic demo payload."""
from pathlib import Path
from demo.validate_payload import validate_payload, PAYLOAD_PATH

def test_demo_payload_has_no_structural_errors():
    errors = validate_payload(PAYLOAD_PATH)
    assert not errors, f"Demo payload validation failed: {errors}"
