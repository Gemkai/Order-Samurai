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
    "state/MEDITATION_STATE.json", "~/.claude/data/security_scorecard.json",
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
# Payload and registry are INJECTED (M6.1) rather than monkeypatched over module
# imports; the expected rows below predate the M6 restructure, so these double as
# the frozen-fixture equivalence check: same payload in, same rows out.

def _checks(payload, registry, repo_root):
    return vls.run_checks(repo_root=repo_root,
                          payload_loader=lambda: payload,
                          registry=registry)


def test_run_checks_fails_on_live_metric_with_missing_source(tmp_path):
    payload = {"pillars": {"sword": {"Security": {
        "Dead_Metric": {"val": 3, "is_simulated": False},
    }}}}
    registry = [{"metric": "Dead_Metric", "source": "state/gone.json"}]
    results = _checks(payload, registry, tmp_path)
    counts, exit_code = vls.summarize(results)
    assert counts["FAIL"] == 1
    assert exit_code == 1
    assert "Dead_Metric" in results[0]["detail"]


def test_run_checks_passes_when_source_resolves(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "live.json").write_text("{}", encoding="utf-8")
    payload = {"pillars": {"sword": {"Security": {
        "Good_Metric": {"val": 3, "is_simulated": False},
    }}}}
    registry = [{"metric": "Good_Metric", "source": "state/live.json"}]
    results = _checks(payload, registry, tmp_path)
    counts, exit_code = vls.summarize(results)
    assert counts["FAIL"] == 0
    assert exit_code == 0


def test_run_checks_ignores_simulated_metric_with_missing_source(tmp_path):
    # A SIMULATED metric whose source is absent is NOT a violation.
    payload = {"pillars": {"sword": {"Security": {
        "Dead_Metric": {"val": None, "is_simulated": True},
    }}}}
    registry = [{"metric": "Dead_Metric", "source": "state/gone.json"}]
    _, exit_code = vls.summarize(_checks(payload, registry, tmp_path))
    assert exit_code == 0


def test_run_checks_skips_logical_sources(tmp_path):
    payload = {"pillars": {"bow": {"Activity": {
        "Telemetry_Metric": {"val": 1, "is_simulated": False},
    }}}}
    registry = [{"metric": "Telemetry_Metric", "source": "telemetry.model_tier"}]
    _, exit_code = vls.summarize(_checks(payload, registry, tmp_path))
    assert exit_code == 0


# ── payload acquisition: fast path, fallback, and the budget ──────────────────

_EMPTY_REGISTRY: list = []


def test_fresh_canonical_payload_preempts_the_builder(tmp_path):
    """When the loader yields a payload, the expensive builder must never run."""
    def _forbidden():
        raise AssertionError("builder ran despite a fresh canonical payload")

    results = vls.run_checks(repo_root=tmp_path,
                             payload_loader=lambda: {"pillars": {}},
                             payload_builder=_forbidden,
                             registry=_EMPTY_REGISTRY)
    _, exit_code = vls.summarize(results)
    assert exit_code == 0


def test_stale_canonical_payload_falls_back_to_the_builder(tmp_path):
    built = {"pillars": {}}
    results = vls.run_checks(repo_root=tmp_path,
                             payload_loader=lambda: None,
                             payload_builder=lambda: built,
                             registry=_EMPTY_REGISTRY)
    _, exit_code = vls.summarize(results)
    assert exit_code == 0


def test_build_exceeding_the_budget_is_warn_never_ok(tmp_path):
    import time

    def _slow():
        time.sleep(5)
        return {"pillars": {}}

    results = vls.run_checks(repo_root=tmp_path,
                             payload_loader=lambda: None,
                             payload_builder=_slow,
                             registry=_EMPTY_REGISTRY,
                             budget_s=0.05)
    assert len(results) == 1
    assert results[0]["status"] == "WARN"
    assert "UNVERIFIED" in results[0]["detail"]
    assert "budget" in results[0]["detail"]


def test_builder_exception_is_still_fail(tmp_path):
    def _broken():
        raise RuntimeError("aggregation pipeline is broken")

    results = vls.run_checks(repo_root=tmp_path,
                             payload_loader=lambda: None,
                             payload_builder=_broken,
                             registry=_EMPTY_REGISTRY)
    assert results[0]["status"] == "FAIL"
    assert "aggregation pipeline is broken" in results[0]["detail"]


# ── load_fresh_payload: the freshness contract ────────────────────────────────

def _canonical(tmp_path, ts: str) -> Path:
    """A minimal schema-valid payload file stamped with `ts`.

    Validated here with the REAL validator so the fixture can never drift into a
    shape load_fresh_payload would reject for schema reasons while these tests
    keep asserting freshness semantics against it.
    """
    import json

    from agentica_core.aggregate import validate_payload

    payload = {"schema_version": "agentica.1", "timestamp": ts,
               "reflexes": [], "pillars": {}}
    validate_payload(payload)
    p = tmp_path / "wid_payload.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_fresh_payload_is_loaded(tmp_path):
    from datetime import datetime, timezone
    path = _canonical(tmp_path, datetime.now(timezone.utc).isoformat())
    assert vls.load_fresh_payload(path=path) is not None


def test_stale_payload_is_rejected(tmp_path):
    from datetime import datetime, timedelta, timezone
    old = (datetime.now(timezone.utc)
           - timedelta(seconds=vls.FRESH_PAYLOAD_MAX_AGE_S + 60)).isoformat()
    path = _canonical(tmp_path, old)
    assert vls.load_fresh_payload(path=path) is None


def test_future_stamped_payload_is_rejected(tmp_path):
    """A timestamp ahead of the clock is a clock lie, not extra freshness."""
    from datetime import datetime, timedelta, timezone
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    path = _canonical(tmp_path, future)
    assert vls.load_fresh_payload(path=path) is None


def test_missing_or_malformed_payload_is_rejected(tmp_path):
    assert vls.load_fresh_payload(path=tmp_path / "absent.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert vls.load_fresh_payload(path=bad) is None


@pytest.mark.live_machine
def test_real_canonical_path_answers_inside_the_doctor_budget():
    """The M6 timing claim on the real corpus: with the canonical payload
    present and fresh (the refresh cycle's normal state), the whole check —
    load, validate, stat every declared source — fits inside the budget that
    used to be blown 5x over by a single rebuild. live_machine: reads the real
    Data/wid_payload.json, which only this host maintains."""
    import time
    t0 = time.perf_counter()
    results = vls.run_checks()
    elapsed = time.perf_counter() - t0
    assert elapsed < vls.BUILD_BUDGET_S, f"{elapsed:.1f}s"
    assert results[0]["status"] in ("OK", "FAIL", "WARN")


# ── real REGISTRY declaration parity (Estimated_Human_Time_Saved) ─────────────
# The 2026-07-12 doctor FAIL was a declaration drift: tokens that don't exist
# (vibe_alignment.json, doc_parity.json) on a metric reporting LIVE. These pin
# the REAL registry entry to the reducer's actual read paths so a revert or
# future drift fails in CI instead of only at doctor-runtime on one machine.

def _real_ehts_entry():
    from agentica_core.ronin_metrics import REGISTRY
    return next(e for e in REGISTRY if e.get("metric") == "Estimated_Human_Time_Saved")


def _ehts_fixture_repo(tmp_path):
    # Two levels below tmp_path, mirroring AgenticaOS/Governance/"Order Samurai"
    # so the declaration's ../../Data token lands at tmp_path/Data.
    root = tmp_path / "Governance" / "os"
    (root / "state" / "charters").mkdir(parents=True)
    (root / "state" / "MEDITATION_STATE.json").write_text("{}", encoding="utf-8")
    (root / "state" / "vibe_alignment.json").write_text("{}", encoding="utf-8")
    (root / "state" / "charters" / "bow.md").write_text("# charter", encoding="utf-8")
    hist = tmp_path / "Data" / "telemetry"
    hist.mkdir(parents=True)
    (hist / "metrics_history.jsonl").write_text("", encoding="utf-8")
    return root


def test_real_ehts_declaration_resolves_against_reducer_read_paths(tmp_path):
    root = _ehts_fixture_repo(tmp_path)
    assert vls._source_missing_tokens(_real_ehts_entry()["source"], root) == []


def test_pre_fix_ehts_declaration_does_not_resolve(tmp_path):
    root = _ehts_fixture_repo(tmp_path)
    broken = "state/MEDITATION_STATE.json+vibe_alignment.json+doc_parity.json"
    assert vls._source_missing_tokens(broken, root) == ["vibe_alignment.json", "doc_parity.json"]
