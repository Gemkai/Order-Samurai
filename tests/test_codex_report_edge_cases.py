"""Malformed metadata and absent usage must not invent sessions or token measurements."""
import json
import re

import pytest

from test_harness_aware_reports import machine, _runtime, _run, _write, _codex, _tokens


@pytest.mark.parametrize("invalid_id", [[], ["bad-id"]])
def test_codex_malformed_session_id_keeps_valid_session(machine, invalid_id):
    _runtime(machine, "codex")
    _write(machine.codex / "2026" / "10" / "20" / "rollout-metadata.jsonl", [
        {"type": "session_meta", "payload": {"id": invalid_id, "cwd": "/shop"}},
        {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command"}},
        _tokens(321, 123, 45),
    ])
    result = _run(machine, "--json", first_blood=True)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["sessions_scanned"] == 1
    assert report["total_cost"] is None


def test_codex_audit_missing_token_events_does_not_render_aggregate_zero(machine):
    _runtime(machine, "codex")
    _codex(machine.codex, tokens=False)
    _run(machine, "audit")
    body = next(machine.reports.glob("audit__*.md")).read_text()
    line = next(line for line in body.splitlines() if "| Token spend |" in line)
    assert not re.search(r"\|\s*0\s*\|", line), line
    assert "unavailable" in line.lower() or "not recorded" in line.lower(), line
