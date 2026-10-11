"""Characterization of insights.populate_history() before the W4 refactor.

Plan: docs/plans/2026-10-07-order-samurai-hotspot-split/PLAN.md, W4. populate_history is CC 44;
these scenarios pin its return value AND every field it writes into the pillars envelope
(history, delta, trend, trajectory_breach_days). METRIC_RULES is pinned so the real rule table
cannot move the snapshot. Expected values live in
fixtures/insights_populate_history_snapshots.json, recorded from the pre-refactor code
(origin/work @ f6e3a6859); never regenerate them to make a refactor pass.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from agentica_core import insights

SNAPSHOTS = Path(__file__).with_name("fixtures") / "insights_populate_history_snapshots.json"
RULES = {
    "Error_Rate": {"dir": "lower", "warn": 5, "fail": 10},
    "Self_Correction_Rate": {"dir": "higher", "warn": 50, "fail": 20},
    "Slop_Density": {"dir": "lower", "warn": 2, "fail": 4, "per": "session"},
    "Flat_Metric": {"dir": "lower", "warn": 5, "fail": 10},
    "No_Fail": {"dir": "lower", "warn": 5},
    "Spike": {"dir": "lower", "warn": 5, "fail": 50},
    "Spike_High": {"dir": "higher", "warn": 50, "fail": 5},
    "Plateau_High": {"dir": "higher", "warn": 50, "fail": 5},
}


def env(val, **extra) -> dict:
    e = {"val": val}
    e.update(extra)
    return e


def pillars() -> dict:
    return {
        "bow": {
            "Activity": {
                "Session_Count": env(10),
                "Error_Rate": env(8),                      # rising toward fail -> breach days
                "Flat_Metric": env(3),                     # constant -> slope 0
                "No_Fail": env(9),                          # rule without fail
                "Unruled": env(4),                          # no rule -> no trajectory
                "Simulated": env(5, is_simulated=True),     # skipped
                "Missing": env(None),                       # skipped
                "Text": env("n/a"),                         # not numeric -> skipped
                "Brand_New": env(1),                        # no history -> 1 point
                "Spike": env(5),                            # recent trend down -> suppressed
            },
            "Remediation": {
                "Self_Correction_Rate": env(30),           # falling toward fail -> breach days
                "Spike_High": env(40),                     # recent trend up -> suppressed
                "Plateau_High": env(40),                   # recent trend flat -> suppressed
            },
        },
        "arts": {"Quality": {"Slop_Density": env(30)}},   # per-session normalized
    }


def row(values: dict, kind=None) -> dict:
    r = {"ts": "2026-08-01T00:00:00Z", "values": values}
    if kind is not None:
        r["kind"] = kind
    return r


def history_rows() -> list:
    rows = []
    for i, (err, scr, slop, sessions, spike, spike_hi) in enumerate([
        (2, 60, 20, 10, 5, 90), (3, 55, 25, 10, 10, 80), (4, 50, 99, None, 40, 70),
        (5, 45, 30, 10, 30, 60), (6, 40, 32, 10, 20, 30), (7, 35, 34, 10, 10, 35),
    ]):
        vals = {"bow/Activity/Error_Rate": err, "bow/Activity/Flat_Metric": 3,
                "bow/Activity/No_Fail": 9 - i, "bow/Activity/Unruled": i,
                "bow/Activity/Spike": spike, "bow/Remediation/Self_Correction_Rate": scr,
                "bow/Remediation/Spike_High": spike_hi, "arts/Quality/Slop_Density": slop,
                "bow/Remediation/Plateau_High": [90, 80, 70, 60, 40, 50][i]}
        if sessions is not None:
            vals["bow/Activity/Session_Count"] = sessions
        rows.append(row(vals, kind="live" if i % 2 else None))
    rows.insert(2, row({"bow/Activity/Error_Rate": 999}, kind="weekly"))   # other population
    rows.insert(3, row({"bow/Activity/Error_Rate": 999}, kind="daily"))    # unknown population
    return rows


def edge_pillars() -> dict:
    """Added after review (W4a): a metric already past fail, a non-integer breach and a
    per-session row with Session_Count 0 (a `> 0` -> `>= 0` mutant divides by zero)."""
    return {
        "bow": {
            "Activity": {
                "Session_Count": env(10),
                "Error_Rate": env(12),                     # already past fail 10 -> no breach days
                "Spike": env(9),                            # rising, fractional breach days
            },
        },
        "arts": {"Quality": {"Slop_Density": env(30)}},
    }


def edge_rows() -> list:
    rows = []
    for i, (err, spike, slop, sessions) in enumerate([
        (2, 2, 20, 10), (4, 3, 25, 0), (6, 5, 99, 10), (9, 6, 30, 10), (11, 8, 32, 10),
    ]):
        rows.append(row({"bow/Activity/Error_Rate": err, "bow/Activity/Spike": spike,
                         "arts/Quality/Slop_Density": slop,
                         "bow/Activity/Session_Count": sessions}))
    return rows


SCENARIOS = {
    "missing_store": dict(rows=None, max_points=7),
    "edge_past_fail_fractional_zero_sessions": dict(rows=edge_rows(), max_points=7,
                                                     pillars=edge_pillars),
    "rich_store_default_window": dict(rows=history_rows(), max_points=7),
    "rich_store_short_window": dict(rows=history_rows(), max_points=3),
    "rich_store_two_points": dict(rows=history_rows(), max_points=2),
}


def run_scenario(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    spec = SCENARIOS[name]
    monkeypatch.setattr(insights, "METRIC_RULES", RULES)
    store = tmp_path / "metrics_history.jsonl"
    if spec["rows"] is not None:
        store.write_text("".join(json.dumps(r) + "\n" for r in spec["rows"]) + "not-json\n",
                         encoding="utf-8")
    p = copy.deepcopy(spec.get("pillars", pillars)())
    current = insights.populate_history(p, store, max_points=spec["max_points"])
    return json.loads(json.dumps({"current": current, "pillars": p}, sort_keys=True))


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_populate_history_matches_the_pre_refactor_snapshot(name, tmp_path, monkeypatch):
    expected = json.loads(SNAPSHOTS.read_text(encoding="utf-8"))[name]
    assert run_scenario(name, tmp_path, monkeypatch) == expected


def test_every_scenario_has_a_recorded_snapshot():
    assert sorted(json.loads(SNAPSHOTS.read_text(encoding="utf-8"))) == sorted(SCENARIOS)


def test_default_store_is_read_when_none_is_given(tmp_path, monkeypatch):
    monkeypatch.setattr(insights, "METRIC_RULES", RULES)
    store = tmp_path / "default.jsonl"
    store.write_text(json.dumps(row({"bow/Activity/Error_Rate": 2})) + "\n", encoding="utf-8")
    monkeypatch.setattr(insights, "default_history_path", lambda: store)
    p = {"bow": {"Activity": {"Error_Rate": env(4)}}}
    insights.populate_history(p)
    assert p["bow"]["Activity"]["Error_Rate"]["history"] == [2, 4.0]
