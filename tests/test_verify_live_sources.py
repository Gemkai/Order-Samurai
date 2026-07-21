"""Tests for the P5 live-source honesty gate (execution/verify_live_sources.py).

Covers the source mini-language parser, the LIVE-metric extraction, and the
end-to-end FAIL path: a metric the payload marks LIVE whose declared source is
missing makes the check (and therefore doctor) FAIL.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
_GOVERNANCE = Path(__file__).resolve().parents[2]
if str(_GOVERNANCE) not in sys.path:
    sys.path.insert(0, str(_GOVERNANCE))

from execution import verify_live_sources as vls  # noqa: E402


# ── _is_logical_source ────────────────────────────────────────────────────────

@pytest.mark.parametrize("source", [
    "telemetry.model_tier", "verifier.root_hygiene", "len(REGISTRY)/TOTAL_PLANNED",
])
def test_logical_sources_skipped(source):
    assert vls._is_logical_source(source) is True


@pytest.mark.parametrize("source", [
    "state/DOJO_STATE.json", "~/.claude/data/security_scorecard.json",
    "file.mtime(state/charters/*.md, execution/**/*.py)",
])
def test_concrete_sources_not_logical(source):
    assert vls._is_logical_source(source) is False


# ── _source_missing_tokens (the mini-language) ────────────────────────────────

def test_existing_single_file_resolves(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "a.json").write_text("{}", encoding="utf-8")
    assert vls._source_missing_tokens("state/a.json", tmp_path) == []


def test_missing_single_file_reported(tmp_path):
    assert vls._source_missing_tokens("state/nope.json", tmp_path) == ["state/nope.json"]


def test_conjunction_all_required(tmp_path):
    (tmp_path / "a.json").write_text("{}", encoding="utf-8")
    # b.json missing -> the '+' conjunction is unsatisfied on b.
    assert vls._source_missing_tokens("a.json+b.json", tmp_path) == ["b.json"]


def test_alternation_any_suffices(tmp_path):
    (tmp_path / "b.json").write_text("{}", encoding="utf-8")
    # a.json missing but b.json present -> alternation satisfied.
    assert vls._source_missing_tokens("a.json|b.json", tmp_path) == []


def test_alternation_all_missing_reported(tmp_path):
    assert vls._source_missing_tokens("a.json|b.json", tmp_path) == ["a.json|b.json"]


def test_glob_match_resolves(tmp_path):
    logs = tmp_path / "state" / "logs"
    logs.mkdir(parents=True)
    (logs / "cycle_1.json").write_text("{}", encoding="utf-8")
    assert vls._source_missing_tokens("state/logs/cycle_*.json", tmp_path) == []


def test_glob_no_match_reported(tmp_path):
    assert vls._source_missing_tokens("state/logs/cycle_*.json", tmp_path) == [
        "state/logs/cycle_*.json"
    ]


def test_file_mtime_wrapper_unwrapped(tmp_path):
    (tmp_path / "x.py").write_text("", encoding="utf-8")
    # comma-separated globs inside file.mtime() are each required; both must match.
    missing = vls._source_missing_tokens("file.mtime(*.py, *.md)", tmp_path)
    assert missing == ["*.md"]


# ── _live_metric_names ────────────────────────────────────────────────────────

def test_live_extraction_skips_simulated():
    payload = {"pillars": {"bow": {"Activity": {
        "Live_One": {"val": 1, "is_simulated": False},
        "Sim_One": {"val": None, "is_simulated": True},
    }}}}
    assert vls._live_metric_names(payload) == {"Live_One"}


# ── run_checks end-to-end ─────────────────────────────────────────────────────

def _patch(monkeypatch, payload, registry):
    import agentica_core.aggregate as agg_mod
    import agentica_core.ronin_metrics as rm_mod
    monkeypatch.setattr(agg_mod, "aggregate", lambda **_: payload)
    monkeypatch.setattr(rm_mod, "REGISTRY", registry)


def test_run_checks_fails_on_live_metric_with_missing_source(monkeypatch, tmp_path):
    payload = {"pillars": {"sword": {"Security": {
        "Dead_Metric": {"val": 3, "is_simulated": False},
    }}}}
    registry = [{"metric": "Dead_Metric", "source": "state/gone.json"}]
    _patch(monkeypatch, payload, registry)
    results = vls.run_checks(repo_root=tmp_path)
    counts, exit_code = vls.summarize(results)
    assert counts["FAIL"] == 1
    assert exit_code == 1
    assert "Dead_Metric" in results[0]["detail"]


def test_run_checks_passes_when_source_resolves(monkeypatch, tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "live.json").write_text("{}", encoding="utf-8")
    payload = {"pillars": {"sword": {"Security": {
        "Good_Metric": {"val": 3, "is_simulated": False},
    }}}}
    registry = [{"metric": "Good_Metric", "source": "state/live.json"}]
    _patch(monkeypatch, payload, registry)
    results = vls.run_checks(repo_root=tmp_path)
    counts, exit_code = vls.summarize(results)
    assert counts["FAIL"] == 0
    assert exit_code == 0


def test_run_checks_ignores_simulated_metric_with_missing_source(monkeypatch, tmp_path):
    # A SIMULATED metric whose source is absent is NOT a violation.
    payload = {"pillars": {"sword": {"Security": {
        "Dead_Metric": {"val": None, "is_simulated": True},
    }}}}
    registry = [{"metric": "Dead_Metric", "source": "state/gone.json"}]
    _patch(monkeypatch, payload, registry)
    results = vls.run_checks(repo_root=tmp_path)
    _, exit_code = vls.summarize(results)
    assert exit_code == 0


def test_run_checks_skips_logical_sources(monkeypatch, tmp_path):
    payload = {"pillars": {"bow": {"Activity": {
        "Telemetry_Metric": {"val": 1, "is_simulated": False},
    }}}}
    registry = [{"metric": "Telemetry_Metric", "source": "telemetry.model_tier"}]
    _patch(monkeypatch, payload, registry)
    results = vls.run_checks(repo_root=tmp_path)
    _, exit_code = vls.summarize(results)
    assert exit_code == 0
