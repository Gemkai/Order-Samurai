"""Characterization of scouts.security_signals() before the W3 refactor.

Plan: docs/plans/2026-10-07-order-samurai-hotspot-split/PLAN.md, W3. security_signals is
CC 62; these scenarios pin its full return dict so an extract-function refactor can be
proven behavior-preserving. Expected dicts live in
fixtures/scouts_security_signals_snapshots.json, recorded from the pre-refactor code
(origin/work @ f6e3a6859); never regenerate them to make a refactor pass.

Pinned: the clock (`scouts.datetime`), the deprecated-deps allowlist path, the autonomic
events path, governance_findings(), and the three live sub-scouts (doc_parity.run,
vulnerability_window.update_and_measure, kill_chain_discovery.run). A refactor must keep
reading these through the same module attributes at call time.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import agentica_core.scouts as scouts
import agentica_core.scouts.doc_parity as doc_parity
import agentica_core.scouts.kill_chain_discovery as kill_chain_discovery
import agentica_core.scouts.vulnerability_window as vulnerability_window
from agentica_core import telemetry

SNAPSHOTS = Path(__file__).with_name("fixtures") / "scouts_security_signals_snapshots.json"
NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)  # ISO week 2026-W36


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is None else NOW.astimezone(tz)


def jsonl(*rows) -> str:
    return "".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows)


FULL_FILES = {
    "security_gate_log.jsonl": jsonl(
        {"findings": ["x"]}, {"finding_count": 2}, {"exit_code": 1}, {"exit_code": 0},
        {"findings": []}, "", "not-json", "[1, 2]"),
    "dependency_audit.json": {
        "scanner_ok": {"pip": True, "pip_audit": True, "npm": True},
        "pip_cves": [{"vuln_count": 3}, {"vuln_count": True}, {}, "legacy"],
        "npm_audits": [{"total": 2}, {"total": -1}, {"total": "x"}, "legacy"],
        "pip_outdated": [{"name": "Allowed_Pkg"}, {"name": "other"}, "legacy-row"],
    },
    "security_gate_canary.json": {"gate_working": True, "last_run": "2026-08-01T00:00:00Z",
                                  "max_age_days": 7},
    "mechanism_audit.json": {"counts": {"orphan": 2, "critical": 1},
                             "findings": [{"category": "launchd_failing"},
                                          {"category": "other"}, "junk"]},
    "orphan_mechanisms.json": {"counts": {"producer_orphans": 3,
                                          "consumer_orphans_dead_end": "4"}},
    "doc_parity.json": {"broken_refs": [1, 2], "undocumented_hooks": [3], "unwired_hooks": None},
    "skill_conflicts.json": {"groups": [1, 2, 3]},
    "mcp_smoke_test.json": {"fail_count": "2"},
    "config_integrity_drift.jsonl": jsonl(
        {"ts": "2026-09-01T10:00:00Z"}, {"ts": "2026-08-20T10:00:00Z"}, {"ts": ""},
        {"ts": "garbage"}, "", "not-json"),
}

SCENARIOS = {
    "empty_data_dir": dict(files={}, events=None, gov={}, live=dict(dp=None, vw=None, kcd=None)),
    "full_with_live_scouts": dict(files=FULL_FILES, events=[
        {"event": "memory_recall"}, {"event": "memory_recall"}, {"event": "other"}, "x", "", [1]],
        gov={"governance_findings_critical": 1, "governance_findings_high": 2,
             "governance_findings_total_ch": 3},
        live=dict(dp=4, vw=12, kcd=0)),
    "live_scouts_raise": dict(files=FULL_FILES, events=[], gov={},
                              live=dict(dp="raise", vw="raise", kcd="raise")),
    "live_scouts_report_none": dict(files=FULL_FILES, events=[], gov={},
                                    live=dict(dp=None, vw=None, kcd=None)),
    "scanners_partial": dict(files={
        "dependency_audit.json": {"scanner_ok": {"pip": False, "pip_audit": True},
                                  "pip_cves": [{"vuln_count": 1}], "pip_outdated": ["a"]},
        "security_gate_canary.json": {"gate_working": False},
        "mechanism_audit.json": {"counts": None},
        "orphan_mechanisms.json": {"counts": {"producer_orphans": 1}},
        "mcp_smoke_test.json": {"no_fail_count": 1},
    }, events=None, gov={}, live=dict(dp=None, vw=None, kcd=None)),
    "legacy_health_and_bad_shapes": dict(files={
        "dependency_audit.json": {"scanner_ok": "legacy", "pip_outdated": "nope"},
        "security_gate_canary.json": {"gate_working": True, "last_run": "not-a-date",
                                      "max_age_days": 1},
        "orphan_mechanisms.json": {"counts": {"producer_orphans": None,
                                              "consumer_orphans_dead_end": 1}},
        "doc_parity.json": [],
        "skill_conflicts.json": {"groups": None},
    }, events=None, gov={}, live=dict(dp=None, vw=None, kcd=None)),
    "npm_skipped_and_fresh_naive_canary": dict(files={
        "dependency_audit.json": {"scanner_ok": {"pip": True, "pip_audit": True},
                                  "pip_cves": "not-a-list", "npm_audits": [{"total": 5}],
                                  "pip_outdated": []},
        "security_gate_canary.json": {"gate_working": True, "last_run": "2026-09-01T00:00:00",
                                      "max_age_days": 7},
    }, events=None, gov={}, live=dict(dp=None, vw=None, kcd=None)),
    "pip_ok_outdated_not_a_list": dict(files={
        "dependency_audit.json": {"scanner_ok": {"pip": True}, "pip_outdated": {"a": 1}},
    }, events=None, gov={}, live=dict(dp=None, vw=None, kcd=None)),
    "canary_without_age_budget": dict(files={
        "security_gate_canary.json": {"gate_working": True, "last_run": "2020-01-01T00:00:00Z"},
    }, events=None, gov={}, live=dict(dp=None, vw=None, kcd=None)),
}


def run_scenario(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    spec = SCENARIOS[name]
    runtime_root = tmp_path / "runtime"
    data = runtime_root / "data"
    data.mkdir(parents=True)
    for fname, content in spec["files"].items():
        text = content if isinstance(content, str) else json.dumps(content)
        (data / fname).write_text(text, encoding="utf-8")

    allow = tmp_path / "deprecated_deps_allowlist.json"
    allow.write_text(json.dumps({"packages": ["allowed-pkg", " "]}), encoding="utf-8")
    monkeypatch.setattr(scouts, "_DEPRECATED_DEPS_ALLOWLIST_PATH", allow)
    monkeypatch.setattr(scouts, "datetime", _FrozenDatetime)
    monkeypatch.setattr(scouts, "governance_findings", lambda: dict(spec["gov"]))

    events_path = tmp_path / "autonomic_events.jsonl"
    if spec["events"] is not None:
        events_path.write_text(jsonl(*[e if isinstance(e, str) else e for e in spec["events"]]),
                               encoding="utf-8")
    monkeypatch.setattr(telemetry, "default_events_path", lambda: events_path)

    def live(value, key):
        def fn(*_args, **_kwargs):
            if value == "raise":
                raise RuntimeError("live scout failed")
            return {key: value}
        return fn

    lv = spec["live"]
    monkeypatch.setattr(doc_parity, "run", live(lv["dp"], "doc_parity_issues"))
    monkeypatch.setattr(vulnerability_window, "update_and_measure",
                        live(lv["vw"], "vulnerability_window_days"))
    monkeypatch.setattr(kill_chain_discovery, "run", live(lv["kcd"], "kill_chain_candidates"))
    return scouts.security_signals(runtime_root)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_security_signals_matches_the_pre_refactor_snapshot(name, tmp_path, monkeypatch):
    expected = json.loads(SNAPSHOTS.read_text(encoding="utf-8"))[name]
    assert run_scenario(name, tmp_path, monkeypatch) == expected


def test_every_scenario_has_a_recorded_snapshot():
    assert sorted(json.loads(SNAPSHOTS.read_text(encoding="utf-8"))) == sorted(SCENARIOS)


def test_missing_runtime_data_dir_yields_only_live_signals(tmp_path, monkeypatch):
    """No <runtime_root>/data at all: every file-backed signal is absent, not zero."""
    monkeypatch.setattr(scouts, "governance_findings", lambda: {})
    monkeypatch.setattr(telemetry, "default_events_path", lambda: tmp_path / "absent.jsonl")
    monkeypatch.setattr(doc_parity, "run", lambda: {"doc_parity_issues": None})
    monkeypatch.setattr(vulnerability_window, "update_and_measure",
                        lambda root: {"vulnerability_window_days": None})
    monkeypatch.setattr(kill_chain_discovery, "run", lambda root: {"kill_chain_candidates": None})
    assert scouts.security_signals(tmp_path / "nowhere") == {}
