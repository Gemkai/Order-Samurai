"""Characterization of remediation.efficacy() before the W1 refactor.

Plan: docs/plans/2026-10-07-order-samurai-hotspot-split/PLAN.md, W1. efficacy() is a
single CC-93 function; this file pins its full return dict for a table of scenarios so
an extract-function refactor can be proven behavior-preserving. The expected dicts in
fixtures/remediation_efficacy_snapshots.json were recorded from the pre-refactor code
(origin/work @ f6e3a6859) and must not be regenerated to make a refactor pass.

Every input is local and fixed: a frozen `now`, tmp files for the history, exec log and
re-judgment sidecar, and pinned METRIC_RULES / REMEDIATION tables (so a later change to
insights' real tables cannot move these snapshots). The scenarios were chosen to execute
every branch of efficacy() (checked with `coverage run --branch`).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agentica_core import insights
from agentica_core import remediation as rem

SNAPSHOTS = Path(__file__).with_name("fixtures") / "remediation_efficacy_snapshots.json"
NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

RULES = {
    "Error_Rate": {"dir": "lower", "warn": 5, "fail": 10},
    "Self_Correction_Rate": {"dir": "higher", "warn": 50, "fail": 20},
    "Slop_Density": {"dir": "lower", "warn": 2, "fail": 4, "per": "session"},
    "Cost": {"dir": "lower", "warn": 1, "fail": 2},
    "Latency": {"dir": "lower", "warn": 1, "fail": 2},
}
REMEDIATION = {
    "Error_Rate": {"skill": "debug", "command": "/debug"},
    "Self_Correction_Rate": {"skill": "verify", "command": "/verify"},
    "Slop_Density": {"skill": "simplify", "command": "/simplify"},
    "Cost": {"skill": "cost-skill", "command": "/cost"},
    "Latency": {"skill": "never-used", "command": "/never"},
    "No_Rule_Metric": {"skill": "debug", "command": "/norule"},
}


def ts(days_ago: float, hours: float = 0.0) -> str:
    return (NOW - timedelta(days=days_ago) + timedelta(hours=hours)).isoformat()


def engine_row(skill: str, days_ago: float, **extra) -> dict:
    row = {"skill": skill, "timestamp": ts(days_ago), "source": "reflex_engine",
           "status": "done", "command": f"/{skill}"}
    row.update(extra)
    return row


def snap(days_ago: float, values: dict) -> dict:
    return {"ts": ts(days_ago), "values": values}


def telemetry(days_ago: float, skills: list[str], session_id: str | None = None) -> dict:
    rec = {"timestamp": ts(days_ago), "skills_used": skills}
    if session_id is not None:
        rec["session_id"] = session_id
    return rec


# --------------------------------------------------------------------------- inputs

def exec_log_all_tiers() -> list[dict]:
    """Rows exercising the attempt counter and the tier-1 / tier-2 event paths."""
    return [
        # Attempt counter: non-autonomous rows are skipped, autonomous ones counted.
        {"skill": "debug", "timestamp": ts(1), "source": "dashboard", "status": "done"},
        {"skill": "debug", "source": "reflex_engine", "status": "done"},  # no timestamp
        engine_row("debug", 40, status="error"),                           # out of 7d window
        engine_row("verify", 2, status="timeout", improved=True),          # unsettled verdict
        # Tier 1 (fire-time before/after), every skip and outcome branch.
        engine_row("debug", 3, reflex_id="manual:debug", metric_before=1, metric_after=2),
        engine_row("debug", 3, reflex_id="metric:bow:Unknown_Metric",
                   metric_before=1, metric_after=2),
        {"skill": "", "timestamp": ts(3), "source": "reflex_engine", "status": "done",
         "reflex_id": "metric:bow:Error_Rate", "metric_before": 20, "metric_after": 3},
        {"skill": "debug", "source": "dashboard", "status": "done",
         "reflex_id": "metric:bow:Error_Rate", "metric_before": 20, "metric_after": 3},
        engine_row("debug", 2, reflex_id="metric:bow:Error_Rate",
                   metric_before=20, metric_after=3),
        engine_row("verify", 2, reflex_id="metric:bow:Self_Correction_Rate",
                   metric_before=40, metric_after=30),
        engine_row("debug", 50, reflex_id="metric:bow:Error_Rate",
                   metric_before=8, metric_after=8),
        engine_row("debug", 2, reflex_id="metric:bow:Error_Rate", propose_only=True,
                   metric_before=20, metric_after=4),
        {"skill": "debug", "timestamp": ts(2), "source": "dashboard", "status": "done",
         "reflex_id": "metric:bow:Error_Rate", "metric_before": 12, "metric_after": 6},
        engine_row("debug", 2, reflex_id="metric:bow:Error_Rate", kind="mechanism",
                   read_only=True, metric_before=12, metric_after=6),
        engine_row("debug", 2, reflex_id="metric:bow:Error_Rate",
                   metric_before=True, metric_after=False),  # bools are not readings
        # Tier 2 (engine verdict on a settled run).
        engine_row("simplify", 1, reflex_id="metric:arts:Slop_Density", improved=True),
        engine_row("verify", 1, status="no_change", reflex_id="correlation:cost_and_quality",
                   improved=False, entry_hash="h-late"),
        engine_row("cost-skill", 45, reflex_id="", improved=False, entry_hash="h-not-late"),
        engine_row("simplify", 1, reflex_id="metric:arts:Slop_Density", improved=True,
                   propose_only=True),
        # Uses for snapshot correlation (done, no evidence): one reflex, one human.
        engine_row("debug", 25),
        {"skill": "verify", "timestamp": ts(19), "source": "dashboard", "status": "done"},
        {"skill": "verify", "timestamp": ts(19), "source": "dashboard", "status": "error"},
    ]


def history_all_paths() -> list[dict]:
    return [
        # Error_Rate: flagged -> healthy (improved), flagged -> flat, healthy stretch.
        snap(30, {"bow/Activity/Error_Rate": 30}),
        snap(24, {"bow/Activity/Error_Rate": 3}),
        snap(22, {"bow/Activity/Error_Rate": 25}),
        snap(15, {"bow/Activity/Error_Rate": 25}),
        snap(10, {"bow/Activity/Error_Rate": 2}),
        snap(9, {"bow/Activity/Error_Rate": "n/a"}),
        # Self_Correction_Rate (higher is better): flagged -> worse (regressed).
        snap(20, {"bow/Remediation/Self_Correction_Rate": 10}),
        snap(18, {"bow/Remediation/Self_Correction_Rate": 5}),
        # Slop_Density per session: one snapshot lacks Session_Count and is skipped.
        snap(14, {"arts/Quality/Slop_Density": 60, "bow/Activity/Session_Count": 10}),
        snap(13, {"arts/Quality/Slop_Density": 99}),
        snap(12, {"arts/Quality/Slop_Density": 10, "bow/Activity/Session_Count": 10}),
        snap(11, {"arts/Quality/Slop_Density": 70, "bow/Activity/Session_Count": 0}),
        # Cost: a single snapshot cannot bracket a use.
        snap(8, {"brush/Spend/Cost": 9}),
    ]


def records_all_paths() -> list[dict]:
    return [
        telemetry(27, ["debug"], "sess-a"),            # human use in the 30->24 window
        telemetry(26, ["debug"], "sess-a"),            # re-emit: deduped per session
        telemetry(26.5, ["debug"], "local-session"),   # no real session: kept
        telemetry(20, ["debug"]),                      # 22->15 window, human first ...
        telemetry(17, ["debug"]),                      # ... duplicate window, human stays
        telemetry(28, ["debug"], "sess-b"),            # reflex (exec_log, 25d) upgrades
        telemetry(29, ["verify"]),                     # Self_Correction not yet flagged
        telemetry(19.5, ["verify"]),                   # 20->18 window (regressed)
        telemetry(13.5, ["simplify"]),                 # Slop 14->12 window
        telemetry(5, ["debug"]),                       # after the last Error snapshot
        telemetry(12, ["debug"]),                      # healthy before -> no flag
        telemetry(7, ["cost-skill"]),
        {"skills_used": ["debug"]},                    # no timestamp: ignored
    ]


def many_events_log() -> list[dict]:
    return [engine_row("debug", 1 + i / 24, reflex_id="metric:bow:Error_Rate",
                       metric_before=20, metric_after=3 if i % 2 else 25)
            for i in range(23)]


SCENARIOS = {
    "lifetime_all_tiers": dict(window_days=None),
    "windowed_7d": dict(window_days=7),
    "windowed_kill_switch": dict(window_days=7, env={"REMEDIATION_WINDOW": "false"}),
    "engine_verdict_kill_switch": dict(window_days=None,
                                       env={"REMEDIATION_ENGINE_VERDICT": "false"}),
    "rejudge_sidecar_disabled": dict(window_days=None, env={"REJUDGE_SIDECAR_ENABLED": "false"}),
    "windowed_zero_attempts": dict(window_days=7, exec_rows=[engine_row("debug", 30)]),
    "no_inputs": dict(window_days=7, exec_rows=[], history=[], records=[], rejudge=[]),
    "more_than_twenty_events": dict(window_days=None, exec_rows=many_events_log(),
                                    history=[], records=[]),
    "defaults_from_module_paths": dict(window_days=30, use_defaults=True),
    # Naive `now` and naive row timestamps are both read as UTC.
    "windowed_naive_clock": dict(window_days=7, now=NOW.replace(tzinfo=None),
                                 exec_rows=exec_log_all_tiers() + [
                                     dict(engine_row("debug", 1, reflex_id="metric:bow:Error_Rate",
                                                     metric_before=20, metric_after=3),
                                          timestamp=(NOW - timedelta(days=1)).replace(
                                              tzinfo=None).isoformat())]),
}


def run_scenario(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    spec = SCENARIOS[name]
    for var in ("REMEDIATION_WINDOW", "REMEDIATION_ENGINE_VERDICT", "REJUDGE_SIDECAR_ENABLED"):
        monkeypatch.delenv(var, raising=False)
    for var, value in spec.get("env", {}).items():
        monkeypatch.setenv(var, value)
    monkeypatch.setattr(insights, "METRIC_RULES", RULES)
    monkeypatch.setattr(insights, "REMEDIATION", REMEDIATION)

    def write_jsonl(path: Path, rows: list[dict]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in rows) + "not-json\n\n",
                        encoding="utf-8")
        return path

    history = write_jsonl(tmp_path / "hist" / "metrics_history.jsonl",
                          spec.get("history", history_all_paths()))
    exec_log = write_jsonl(tmp_path / "state" / "exec_log.jsonl",
                           spec.get("exec_rows", exec_log_all_tiers()))
    write_jsonl(tmp_path / "state" / "exec_log_rejudge.jsonl", spec.get("rejudge", [
        {"entry_hash": "h-late", "improved": True},
        {"entry_hash": "h-not-late", "improved": False},
    ]))
    records = spec.get("records", records_all_paths())

    if spec.get("use_defaults"):
        monkeypatch.setattr(insights, "default_history_path", lambda: history)
        monkeypatch.setattr(rem, "_EXEC_LOG", exec_log)
        monkeypatch.setattr(rem, "list_platforms", lambda: ["alpha", "beta"])
        from agentica_core import aggregate
        monkeypatch.setattr(aggregate, "load_records",
                            lambda platform: records if platform == "alpha" else [])
        return rem.efficacy(window_days=spec["window_days"], now=spec.get("now", NOW))
    return rem.efficacy(history_path=history, records=records, exec_log_path=exec_log,
                        window_days=spec["window_days"], now=spec.get("now", NOW))


def _canonical(result: dict) -> dict:
    return json.loads(json.dumps(result, sort_keys=True))


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_efficacy_return_dict_matches_the_pre_refactor_snapshot(name, tmp_path, monkeypatch):
    expected = json.loads(SNAPSHOTS.read_text(encoding="utf-8"))[name]
    assert _canonical(run_scenario(name, tmp_path, monkeypatch)) == expected


def test_every_scenario_has_a_recorded_snapshot():
    assert sorted(json.loads(SNAPSHOTS.read_text(encoding="utf-8"))) == sorted(SCENARIOS)


def test_explicit_rejudge_path_overrides_the_sidecar_beside_the_exec_log(tmp_path, monkeypatch):
    """`rejudge_path` wins over the default location next to the exec log."""
    baseline = run_scenario("lifetime_all_tiers", tmp_path, monkeypatch)
    empty = tmp_path / "elsewhere.jsonl"
    empty.write_text("", encoding="utf-8")
    overridden = rem.efficacy(
        history_path=tmp_path / "hist" / "metrics_history.jsonl",
        records=records_all_paths(), exec_log_path=tmp_path / "state" / "exec_log.jsonl",
        now=NOW, rejudge_path=empty)
    late = [e for e in baseline["events"] if e.get("late_rejudge")]
    assert late and not [e for e in overridden["events"] if e.get("late_rejudge")]
