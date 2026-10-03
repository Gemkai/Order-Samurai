"""The shipped demo and its validator must agree on repair-history no-data."""

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = ROOT / "dashboard-ui" / "public" / "wid_payload.json"
VALIDATOR = ROOT / "demo" / "validate_payload.py"


def test_shipped_payload_has_explicit_repair_history_no_data():
    eff = json.loads(PAYLOAD.read_text(encoding="utf-8"))["remediation_efficacy"]
    assert eff.get("attempted", 0) == 0
    assert eff.get("completed", 0) == 0
    assert {key: eff.get(key) for key in ("applied", "improved", "regressed", "flat")} == {
        "applied": 0, "improved": 0, "regressed": 0, "flat": 0,
    }
    assert eff.get("success_rate") is None
    assert eff.get("by_skill") == {}
    assert eff.get("events") == []
    assert isinstance(eff.get("note"), str) and eff["note"].strip()


def run_validator(payload, tmp_path):
    candidate = tmp_path / "wid_payload.json"
    candidate.write_text(json.dumps(payload), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(VALIDATOR), str(candidate)],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_validator_rejects_omitted_repair_history_schema(tmp_path):
    payload = json.loads(PAYLOAD.read_text(encoding="utf-8"))
    payload["remediation_efficacy"] = {}
    result = run_validator(payload, tmp_path)
    assert result.returncode == 1
    assert "remediation_efficacy" in result.stdout + result.stderr


def test_validator_rejects_missing_repair_events(tmp_path):
    payload = json.loads(PAYLOAD.read_text(encoding="utf-8"))
    payload["remediation_efficacy"] = {
        "applied": 0, "improved": 0, "regressed": 0, "flat": 0,
        "success_rate": None, "by_skill": {}, "note": "Not evaluated.",
    }
    result = run_validator(payload, tmp_path)
    assert result.returncode == 1
    assert "remediation_efficacy.events" in result.stdout + result.stderr


def test_validator_accepts_explicit_zero_attempt_no_data(tmp_path):
    payload = json.loads(PAYLOAD.read_text(encoding="utf-8"))
    payload["remediation_efficacy"] = {
        "attempted": 0, "completed": 0, "applied": 0, "improved": 0,
        "regressed": 0, "flat": 0, "success_rate": None, "by_skill": {},
        "events": [], "note": "No repair attempts; efficacy is not evaluated.",
    }
    result = run_validator(payload, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
