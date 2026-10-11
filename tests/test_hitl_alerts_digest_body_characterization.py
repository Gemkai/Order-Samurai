"""Characterization of hitl_alerts._digest_body() before the W4b refactor.

Plan: docs/plans/2026-10-07-order-samurai-hotspot-split/PLAN.md, W4b. _digest_body is CC 45 and
builds the daily digest text from already-loaded inputs, so its whole observable behavior is the
returned string plus how often it calls the automation-health monitor-status function. Every
section is driven through each of its branches. Expected values live in
tests/fixtures/hitl_digest_body_snapshots.json, recorded from the frozen post-RECON1 baseline
(work @ b559e7d00, hitl_alerts.py blob d27d3b67db1d); never regenerate them to make a refactor pass.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ORDER_SAMURAI = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("hitl_alerts", ORDER_SAMURAI / "bin" / "hitl_alerts.py")
assert _SPEC and _SPEC.loader
hitl_alerts = importlib.util.module_from_spec(_SPEC)
sys.modules["hitl_alerts"] = hitl_alerts
_SPEC.loader.exec_module(hitl_alerts)

SNAPSHOTS = Path(__file__).with_name("fixtures") / "hitl_digest_body_snapshots.json"
FIXED_NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


def _pending() -> list[dict]:
    return [
        {"command": "/debug", "pillar": "bow", "metric_id": "metric:bow:Error_Rate",
         "enqueued_at": "2026-07-30T12:00:00Z", "context": "  short context  ",
         "blast_radius": "low"},
        {"command": "/fix", "pillar": "sword", "metric_id": None,
         "enqueued_at": "2026-06-01T00:00:00", "context": "x" * 260},       # naive ts, 200-char cut
        {"enqueued_at": "not-a-date"},                                      # every field missing
    ]


def _expired() -> list[dict]:
    return [
        {"id": "h1", "skill": "verify", "source": "reflex", "pillar": "arts",
         "expired_at": "2026-07-01T09:00:00Z"},
        {"id": "h2", "command": "/scan", "expired_at": "2026-08-01T00:00:00Z"},   # command fallback
        {},                                                                       # all "?"
    ]


def _patches() -> list[dict]:
    return [{"name": "pending_remediation_a.patch", "mtime": datetime(2026, 7, 31, tzinfo=timezone.utc)},
            {"name": "pending_remediation_b.patch", "mtime": None}]


def _status_fn(value: str, calls: list):
    def fn(path, interval):
        calls.append((Path(path).name, interval))
        return value
    return fn


DOCTOR_FRESH = "2026-08-02T06:45:00Z"

SCENARIOS = {
    "empty": dict(),
    "queue_error_with_pending": dict(pending=_pending(), queue_error=RuntimeError("bad json")),
    "patches_and_expired": dict(patches=_patches(), expired=_expired()),
    "backlog_variants": [
        dict(backlog={"error": "permission denied"}),
        dict(backlog={"pending": 4, "oldest_days": None}),
        dict(backlog={"pending": 2, "oldest_days": 3}),
        dict(backlog={"pending": 9, "oldest_days": 30}),
    ],
    "fleet_variants": [
        dict(fleet={"error": "probe missing"}),
        dict(fleet={"failing_jobs": [], "unreachable_services": None}),
        dict(fleet={"failing_jobs": ["job.a", "job.b", "job.c", "job.d"],
                    "unreachable_services": ["qdrant"]},
             fleet_tiers_by_job={"job.a": {"tier": "banner", "days": 1},
                                 "job.b": {"tier": "digest", "days": 4},
                                 "job.c": {"tier": "hitl", "days": 8}}),
        dict(fleet={"failing_jobs": ["job.z"]}),                            # tiers None
    ],
    "retirements_and_unreconciled": [
        dict(retirements=[{"label": "a"}, {"error": "ledger unreadable"}]),
        dict(retirements=[{"label": "com.x", "disabled_at": "2026-07-30T10:00:00Z",
                           "reason": "failing 7d", "revert": "launchctl load x"}, {}],
             unreconciled=[{"label": "com.y", "detail": "bootout failed", "revert": "load y"}, {}]),
    ],
    "doctor_variants": [
        dict(doctor={"missing": True}),
        dict(doctor={"error": "bad json"}),
        dict(doctor={"generated_at": None, "fails": [{"label": "x"}]}),
        dict(doctor={"generated_at": "2026-07-30T00:00:00Z", "fails": [{}, {}]}),        # stale
        dict(doctor={"generated_at": DOCTOR_FRESH,
                     "fails": [{"label": "hooks", "detail": "2 broken"}, {}]}),
        dict(doctor={"generated_at": DOCTOR_FRESH, "fails": [], "counts": {"OK": 20, "WARN": 3}}),
        dict(doctor={"generated_at": DOCTOR_FRESH}),                                      # no counts
    ],
    "automation_variants": [
        dict(automation_health={"missing": True}),
        dict(automation_health={"error": "malformed"}),
        dict(automation_health={"jobs": {}}),                                            # loader -> None
        dict(automation_health={"jobs": {"j1": {"result_kind": "ok"}},
                                "coverage": {"numerator": 5, "denominator": 6},
                                "artifact_verified": 4}, status="OK"),
        dict(automation_health={"jobs": {"j1": {"result_kind": "failed"},
                                         "j2": {"result_kind": "retired-unverified"},
                                         "j3": "not-a-dict", "j4": {"result_kind": "ok"}},
                                "coverage": {"numerator": 5, "denominator": 6},
                                "artifact_verified": 4}, status="OK"),
        dict(automation_health={"jobs": {"j1": {"result_kind": "stale"}},
                                "coverage": {"numerator": 5, "denominator": 6},
                                "artifact_verified": 4}, status="DEAD"),
        dict(automation_health={"jobs": {}, "coverage": {"numerator": True, "denominator": 6},
                                "artifact_verified": 4}, status="OK"),                   # bool is not int
        dict(automation_health={"jobs": {}, "coverage": {"numerator": -1, "denominator": 6},
                                "artifact_verified": 4}, status="OK"),
        dict(automation_health={"jobs": {}, "coverage": "n/a", "artifact_verified": 4},
             status="OK"),
        dict(automation_health={"jobs": None, "coverage": {"numerator": 1, "denominator": 1},
                                "artifact_verified": None}, status="OK"),
    ],
    # Added after the W4b review: zero is a valid count (">= 0", not "> 0").
    "automation_zero_counts": dict(automation_health={"jobs": {}, "coverage": {"numerator": 0,
                                                                         "denominator": 0},
                                                      "artifact_verified": 0}, status="OK"),
    "everything": dict(pending=_pending()[:1], expired=_expired()[:1], patches=_patches()[:1],
                       backlog={"pending": 1, "oldest_days": 15},
                       fleet={"failing_jobs": ["job.c"], "unreachable_services": []},
                       fleet_tiers_by_job={"job.c": {"tier": "hitl", "days": 9}},
                       retirements=[{"label": "r", "disabled_at": "2026-08-01", "reason": "x",
                                     "revert": "y"}],
                       unreconciled=[{"label": "u", "detail": "d", "revert": "v"}],
                       doctor={"generated_at": DOCTOR_FRESH, "fails": [], "counts": {"OK": 1}},
                       automation_health={"jobs": {"j": {"result_kind": "push-failure"}}},
                       status="OK"),
}


def _render(spec: dict) -> dict:
    spec = dict(spec)
    calls: list = []
    status = spec.pop("status", None)
    kwargs = {"pending": spec.pop("pending", []), "expired": spec.pop("expired", []), **spec}
    if status is not None:
        kwargs["automation_health_monitor_status_fn"] = _status_fn(status, calls)
    body = hitl_alerts._digest_body(**kwargs)
    return {"body": body.split("\n"), "monitor_status_calls": calls}


def run_scenario(name: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(hitl_alerts, "_now", lambda: FIXED_NOW)
    monkeypatch.setattr(hitl_alerts, "DOCTOR_STATE_PATH", tmp_path / "doctor_last.json")
    monkeypatch.setattr(hitl_alerts, "AUTOMATION_HEALTH_PATH", tmp_path / "automation-health.json")
    monkeypatch.setattr(hitl_alerts, "_load_monitor_status_fn", lambda: None)
    spec = SCENARIOS[name]
    out = [_render(s) for s in spec] if isinstance(spec, list) else _render(spec)
    return json.loads(json.dumps(out))


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_digest_body_matches_the_pre_refactor_snapshot(name, monkeypatch, tmp_path):
    expected = json.loads(SNAPSHOTS.read_text(encoding="utf-8"))[name]
    assert run_scenario(name, monkeypatch, tmp_path) == expected


def test_every_scenario_has_a_recorded_snapshot():
    assert sorted(json.loads(SNAPSHOTS.read_text(encoding="utf-8"))) == sorted(SCENARIOS)


def test_monitor_status_is_read_once_and_reused_for_alert_lines(monkeypatch, tmp_path):
    """The digest captures the one-shot monitor verdict and hands that same verdict to
    automation_health_alert_lines, so the section never mixes counts with a different verdict."""
    out = run_scenario("everything", monkeypatch, tmp_path)
    assert out["monitor_status_calls"] == [["automation-health.json", 1800]]
