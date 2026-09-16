"""Doctor's claude-hook-health family (execution/doctor.py::_run_claude_hook_health_checks).

Pins the R0.1 contract from the 2026-09-02 incident coverage review: a
quarantined ~/.claude hook must show up in doctor as a WARN (advisory) or FAIL
(blocking, or fleet-wide silence), and an unreadable input must never print as
OK. All paths and the registry are injected — no dependency on the live home.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from execution.doctor import _run_claude_hook_health_checks  # noqa: E402

NOW = datetime(2026, 9, 2, 20, 0, tzinfo=timezone.utc)
REGISTRY = {
    "mechanism-audit": {"criticality": "advisory"},
    "protected-asset-gate": {"criticality": "blocking"},
}


def _timings(path: Path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _row(hook, status, minutes_ago):
    return {"ts": (NOW - timedelta(minutes=minutes_ago)).isoformat(), "hook": hook,
            "event": "SessionStart", "status": status, "duration_ms": 0,
            "exit_code": 0, "platform": "darwin"}


def _run(tmp_path, quarantine=None, rows=None, **kw):
    q = tmp_path / "hook_quarantine.json"
    t = tmp_path / "hook_timings.jsonl"
    if quarantine is not None:
        q.write_text(json.dumps(quarantine), encoding="utf-8")
    if rows is not None:
        _timings(t, rows)
    return _run_claude_hook_health_checks(quarantine_path=q, timings_path=t,
                                          registry=REGISTRY, now=NOW, **kw)


def test_no_telemetry_is_a_warn_not_an_ok(tmp_path):
    rows = _run(tmp_path)
    assert [r["status"] for r in rows] == ["WARN"]
    assert "cannot tell" in rows[0]["detail"]


def test_clean_state_is_ok_with_the_dispatch_count(tmp_path):
    rows = _run(tmp_path, quarantine={"quarantined": {}},
                rows=[_row("mechanism-audit", "ok", 5), _row("guardrails", "blocked", 3)])
    assert [r["status"] for r in rows] == ["OK"]
    assert "0 hooks quarantined; 2 dispatches" in rows[0]["detail"]


def test_missing_quarantine_file_with_telemetry_is_ok(tmp_path):
    rows = _run(tmp_path, rows=[_row("mechanism-audit", "ok", 5)])
    assert rows[0]["status"] == "OK"


def test_advisory_hook_quarantined_warns_and_names_it(tmp_path):
    since = int((NOW - timedelta(days=8)).timestamp())
    quarantine = {"quarantined": {"mechanism-audit": {"quarantined_at": since,
                                                      "consecutive_failures": 3}}}
    timeline = [_row("mechanism-audit", "quarantined", 10)] + \
               [_row("guardrails", "ok", m) for m in range(1, 60)]
    rows = _run(tmp_path, quarantine=quarantine, rows=timeline)
    by_label = {r["label"]: r for r in rows}
    hook = by_label["claude-hook-health.mechanism-audit"]
    assert hook["status"] == "WARN"
    assert "2026-08-25" in hook["detail"] and "advisory" in hook["detail"]
    assert "no-oped 1 dispatches" in hook["detail"]
    assert "--release mechanism-audit" in hook["detail"]
    assert by_label["claude-hook-health.share"]["status"] == "WARN"   # 1/60 < 5%
    assert not any(r["status"] == "FAIL" for r in rows)


def test_blocking_hook_quarantined_fails(tmp_path):
    quarantine = {"quarantined": {"protected-asset-gate": {"quarantined_at": 1, "consecutive_failures": 3}}}
    rows = _run(tmp_path, quarantine=quarantine, rows=[_row("guardrails", "ok", 1)])
    hook = next(r for r in rows if r["label"].endswith("protected-asset-gate"))
    assert hook["status"] == "FAIL"
    assert "blocking" in hook["detail"]


def test_fleet_wide_silence_fails_on_share(tmp_path):
    """The 2026-08-24..31 shape: many advisory hooks, 100% of their dispatches no-ops."""
    quarantine = {"quarantined": {f"h{i}": {"quarantined_at": 1, "consecutive_failures": 3}
                                  for i in range(17)}}
    timeline = [_row(f"h{i}", "quarantined", m) for i in range(17) for m in range(1, 10)]
    timeline += [_row("guardrails", "ok", m) for m in range(1, 20)]
    rows = _run(tmp_path, quarantine=quarantine, rows=timeline)
    share = next(r for r in rows if r["label"] == "claude-hook-health.share")
    assert share["status"] == "FAIL"
    assert "153 of 172 dispatches" in share["detail"]
    assert sum(1 for r in rows if r["label"].startswith("claude-hook-health.h")) == 17


def test_unreadable_quarantine_file_is_a_warn(tmp_path):
    (tmp_path / "hook_quarantine.json").write_text("{not json", encoding="utf-8")
    _timings(tmp_path / "hook_timings.jsonl", [_row("guardrails", "ok", 1)])
    rows = _run_claude_hook_health_checks(quarantine_path=tmp_path / "hook_quarantine.json",
                                          timings_path=tmp_path / "hook_timings.jsonl",
                                          registry=REGISTRY, now=NOW)
    assert [r["status"] for r in rows] == ["WARN"]
    assert "unreadable" in rows[0]["detail"]


def test_rows_outside_the_window_are_ignored(tmp_path):
    quarantine = {"quarantined": {"mechanism-audit": {"quarantined_at": 1, "consecutive_failures": 3}}}
    timeline = [_row("mechanism-audit", "quarantined", 60 * 30)] + [_row("guardrails", "ok", 1)]
    rows = _run(tmp_path, quarantine=quarantine, rows=timeline)
    hook = next(r for r in rows if r["label"].endswith("mechanism-audit"))
    assert "no-oped 0 dispatches" in hook["detail"]
