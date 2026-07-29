"""Tests for Governance/bin/sensei_parity.py (B2 decision-parity differ,
docs/plans/2026-07-27-meta-harness-uplift.md Phase B2 / B1-SPEC.md).

Synthetic fixtures only — no real shadow run, no real SENSEI_LEDGER.jsonl. The
differ's job is to pair a TS-orchestrator shadow decision against the prose
cycle's actual ledger rows and score agreement over three decision classes
(skip / dedup / escalation); these tests pin that scoring logic directly against
constructed inputs so they don't depend on what's currently in either worktree.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
_GOVERNANCE = Path(__file__).resolve().parents[2]
if str(_GOVERNANCE) not in sys.path:
    sys.path.insert(0, str(_GOVERNANCE))
_GOVERNANCE_BIN = _GOVERNANCE / "bin"
if str(_GOVERNANCE_BIN) not in sys.path:
    sys.path.insert(0, str(_GOVERNANCE_BIN))

import sensei_parity as sp  # noqa: E402

UTC = timezone.utc
CYCLE_TS = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
ELIGIBLE_IDS = {"metric:bow:A", "metric:sword:B", "metric:brush:C"}


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _prose_rows(cycle_id: str = "prose-cycle-1") -> list[dict]:
    """A processed, B escalated, C absent (skipped). A REFUTED row for C an hour
    before the cycle makes C's skip a dedup, not just an ordinary drop."""
    return [
        {"ts": _iso(CYCLE_TS - timedelta(hours=1)), "cycle_id": "prior-cycle",
         "reflex_id": "metric:brush:C", "pillar": "brush", "scout_verdict": "real:true skill",
         "rival_verdict": "REFUTED", "action_taken": "verdict_posted", "human_flag": False},
        {"ts": _iso(CYCLE_TS), "cycle_id": cycle_id, "reflex_id": "metric:bow:A", "pillar": "bow",
         "scout_verdict": "real:true skill", "rival_verdict": "CONFIRMED",
         "action_taken": "verdict_posted", "human_flag": False},
        {"ts": _iso(CYCLE_TS), "cycle_id": cycle_id, "reflex_id": "metric:sword:B", "pillar": "sword",
         "scout_verdict": "real:true structural", "rival_verdict": None,
         "action_taken": "escalated_backlog", "human_flag": False},
    ]


def _shadow_record(cycle_id: str = "shadow-1", ts: datetime = CYCLE_TS, **overrides) -> dict:
    base = {
        "ts": _iso(ts),
        "cycle_id": cycle_id,
        "status": "done",
        "counts": {"reflexes": 3, "findings": 2, "confirmed": 1, "refuted": 0, "suspect": 0,
                   "posted": 1, "escalated": 1, "postAudited": 0},
        "checkpoints": [],
        "reflex_ids_skipped": ["metric:brush:C"],
        "reflex_ids_deduped": ["metric:brush:C"],
        "reflex_ids_escalated": ["metric:sword:B"],
        "source_osr_root": "../AgenticaOS-worktrees/conductor-prep/Governance/Order Samurai",
    }
    base.update(overrides)
    return base


def _prose_decisions(cycle_id: str = "prose-cycle-1") -> dict:
    rows = _prose_rows(cycle_id)
    all_rows = rows
    cycle_rows = [r for r in rows if r["cycle_id"] == cycle_id]
    return sp.prose_decisions_for_cycle(CYCLE_TS, cycle_rows, all_rows, ELIGIBLE_IDS)


# ---------------------------------------------------------------------------
# compute_eligible_ids
# ---------------------------------------------------------------------------

def test_compute_eligible_ids_filters_tier_status_prefix_and_pillar():
    payload = {
        "reflexes": [
            {"id": "metric:bow:X", "tier": "CRITICAL", "status": "active"},
            {"id": "metric:sword:Y", "tier": "HIGH", "status": "active"},
            {"id": "metric:brush:Z", "tier": "MEDIUM", "status": "active"},  # tier excluded
            {"id": "metric:arts:W", "tier": "CRITICAL", "status": "resolved"},  # status excluded
            {"id": "nudge:brush:V", "tier": "CRITICAL", "status": "active"},  # prefix excluded
            {"id": "correlation:weird_label", "tier": "CRITICAL", "status": "active"},  # unroutable
            {"id": "correlation:ops_issue", "tier": "CRITICAL", "status": "active", "pillar": "sword"},
        ]
    }
    assert sp.compute_eligible_ids(payload) == {
        "metric:bow:X", "metric:sword:Y", "correlation:ops_issue",
    }


def test_compute_eligible_ids_empty_payload():
    assert sp.compute_eligible_ids(None) == set()
    assert sp.compute_eligible_ids({}) == set()


# ---------------------------------------------------------------------------
# group_prose_cycles / prose_decisions_for_cycle
# ---------------------------------------------------------------------------

def test_group_prose_cycles_groups_by_cycle_id_and_takes_earliest_ts():
    rows = _prose_rows("prose-cycle-1")
    cycles = sp.group_prose_cycles(rows)
    assert set(cycles.keys()) == {"prior-cycle", "prose-cycle-1"}
    assert cycles["prose-cycle-1"]["ts"] == CYCLE_TS
    assert len(cycles["prose-cycle-1"]["rows"]) == 2


def test_prose_decisions_identifies_skip_dedup_escalation():
    decisions = _prose_decisions()
    assert decisions["processed"] == {"metric:bow:A", "metric:sword:B"}
    assert decisions["escalated"] == {"metric:sword:B"}
    assert decisions["skipped"] == {"metric:brush:C"}
    assert decisions["deduped"] == {"metric:brush:C"}


# ---------------------------------------------------------------------------
# pairing
# ---------------------------------------------------------------------------

def test_pair_shadow_to_prose_pairs_within_window():
    shadow = [_shadow_record(ts=CYCLE_TS + timedelta(minutes=5))]
    cycles = sp.group_prose_cycles(_prose_rows("prose-cycle-1"))
    pairs = sp.pair_shadow_to_prose(shadow, cycles, window_seconds=3 * 3600)
    assert len(pairs) == 1
    assert pairs[0]["cycle_id"] == "prose-cycle-1"
    assert pairs[0]["delta_seconds"] == 300


def test_pair_shadow_to_prose_unpairable_outside_window():
    """A shadow run 10 days from the nearest prose cycle must not be paired —
    comparing decisions made against completely different world states would be
    meaningless, not merely imprecise."""
    shadow = [_shadow_record(ts=CYCLE_TS + timedelta(days=10))]
    cycles = sp.group_prose_cycles(_prose_rows("prose-cycle-1"))
    pairs = sp.pair_shadow_to_prose(shadow, cycles, window_seconds=3 * 3600)
    assert len(pairs) == 1
    assert pairs[0]["cycle_id"] is None
    assert pairs[0]["delta_seconds"] == timedelta(days=10).total_seconds()


def test_pair_shadow_to_prose_no_cycles_at_all_is_unpairable():
    shadow = [_shadow_record()]
    pairs = sp.pair_shadow_to_prose(shadow, {}, window_seconds=3 * 3600)
    assert len(pairs) == 1
    assert pairs[0]["cycle_id"] is None
    assert pairs[0]["delta_seconds"] is None


# ---------------------------------------------------------------------------
# compare_pair — the six named scenarios
# ---------------------------------------------------------------------------

def test_perfect_agreement():
    shadow = _shadow_record()
    comparison = sp.compare_pair(shadow, _prose_decisions(), ELIGIBLE_IDS)
    for cls in sp.DECISION_CLASSES:
        assert comparison[cls]["agreements"] == comparison[cls]["total"] == len(ELIGIBLE_IDS)
        assert comparison[cls]["divergences"] == []


def test_one_skip_divergence():
    # Shadow wrongly believes A was skipped; prose says A was processed.
    shadow = _shadow_record(reflex_ids_skipped=["metric:brush:C", "metric:bow:A"])
    comparison = sp.compare_pair(shadow, _prose_decisions(), ELIGIBLE_IDS)

    assert comparison["skip"]["agreements"] == len(ELIGIBLE_IDS) - 1
    assert comparison["skip"]["divergences"] == [
        {"reflex_id": "metric:bow:A", "shadow": True, "prose": False}
    ]
    # The other two classes are untouched by this divergence.
    assert comparison["dedup"]["divergences"] == []
    assert comparison["escalation"]["divergences"] == []


def test_one_dedup_divergence():
    # Shadow saw C as skipped (correct) but not attributed to a REFUTED dedup.
    shadow = _shadow_record(reflex_ids_deduped=[])
    comparison = sp.compare_pair(shadow, _prose_decisions(), ELIGIBLE_IDS)

    assert comparison["dedup"]["agreements"] == len(ELIGIBLE_IDS) - 1
    assert comparison["dedup"]["divergences"] == [
        {"reflex_id": "metric:brush:C", "shadow": False, "prose": True}
    ]
    assert comparison["skip"]["divergences"] == []
    assert comparison["escalation"]["divergences"] == []


def test_one_escalation_divergence():
    # Shadow never escalated B; prose did.
    shadow = _shadow_record(reflex_ids_escalated=[])
    comparison = sp.compare_pair(shadow, _prose_decisions(), ELIGIBLE_IDS)

    assert comparison["escalation"]["agreements"] == len(ELIGIBLE_IDS) - 1
    assert comparison["escalation"]["divergences"] == [
        {"reflex_id": "metric:sword:B", "shadow": False, "prose": True}
    ]
    assert comparison["skip"]["divergences"] == []
    assert comparison["dedup"]["divergences"] == []


# ---------------------------------------------------------------------------
# empty inputs / end-to-end report
# ---------------------------------------------------------------------------

def test_load_jsonl_missing_file_returns_empty_list(tmp_path):
    assert sp.load_jsonl(tmp_path / "does_not_exist.jsonl") == []


def test_load_jsonl_skips_torn_lines(tmp_path):
    f = tmp_path / "ledger.jsonl"
    f.write_text('{"a":1}\nnot json\n{"b":2}\n\n', encoding="utf-8")
    assert sp.load_jsonl(f) == [{"a": 1}, {"b": 2}]


def test_format_report_empty_inputs_never_crashes_and_reports_no_data():
    report = sp.format_report(pairs=[], prose_cycles={}, all_ledger_rows=[], eligible_ids=set(), prep_output=None)
    assert "No shadow runs found" in report
    assert "PASS/FAIL: N/A" in report


def test_format_report_unpairable_run_excluded_from_totals_but_listed():
    shadow = _shadow_record(ts=CYCLE_TS + timedelta(days=10))
    cycles = sp.group_prose_cycles(_prose_rows("prose-cycle-1"))
    pairs = sp.pair_shadow_to_prose([shadow], cycles, window_seconds=3 * 3600)
    report = sp.format_report(pairs, cycles, _prose_rows("prose-cycle-1"), ELIGIBLE_IDS, prep_output=None)
    assert "UNPAIRED" in report
    assert "Overall decision-parity: 0/0" in report
    assert "PASS/FAIL: FAIL (insufficient data" in report


def test_format_report_full_pass_and_fail_paths():
    cycles = sp.group_prose_cycles(_prose_rows("prose-cycle-1"))

    perfect_pairs = sp.pair_shadow_to_prose([_shadow_record()], cycles, window_seconds=3 * 3600)
    pass_report = sp.format_report(perfect_pairs, cycles, _prose_rows("prose-cycle-1"), ELIGIBLE_IDS, prep_output=None)
    assert "Overall decision-parity: 9/9 = 100.0%" in pass_report
    assert "PASS/FAIL: PASS" in pass_report

    diverging_shadow = _shadow_record(reflex_ids_escalated=[])
    fail_pairs = sp.pair_shadow_to_prose([diverging_shadow], cycles, window_seconds=3 * 3600)
    fail_report = sp.format_report(fail_pairs, cycles, _prose_rows("prose-cycle-1"), ELIGIBLE_IDS, prep_output=None)
    assert "DIVERGE[escalation] metric:sword:B: shadow=False prose=True" in fail_report
    assert "PASS/FAIL: FAIL" in fail_report


# ---------------------------------------------------------------------------
# path resolution
# ---------------------------------------------------------------------------

def test_resolve_ledger_path_prefers_worktree_when_present(tmp_path, monkeypatch):
    worktree_osr = tmp_path / "worktree" / "Order Samurai"
    main_osr = tmp_path / "main" / "Order Samurai"
    (worktree_osr / "state").mkdir(parents=True)
    (worktree_osr / "state" / "SENSEI_LEDGER.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr(sp, "WORKTREE_OSR", worktree_osr)
    monkeypatch.setattr(sp, "MAIN_OSR", main_osr)
    assert sp.resolve_ledger_path() == worktree_osr / "state" / "SENSEI_LEDGER.jsonl"


def test_resolve_ledger_path_falls_back_to_main_tree(tmp_path, monkeypatch):
    worktree_osr = tmp_path / "worktree" / "Order Samurai"  # never created
    main_osr = tmp_path / "main" / "Order Samurai"
    monkeypatch.setattr(sp, "WORKTREE_OSR", worktree_osr)
    monkeypatch.setattr(sp, "MAIN_OSR", main_osr)
    assert sp.resolve_ledger_path() == main_osr / "state" / "SENSEI_LEDGER.jsonl"


# ---------------------------------------------------------------------------
# sensei_prep.py as a third data point
# ---------------------------------------------------------------------------

def test_run_sensei_prep_returns_parseable_dict_from_the_real_script():
    # sensei_prep.py is read-only and fast; this exercises the real script (not a
    # fixture) since it's exactly the artifact the differ reports on as a third
    # data point — not a full parity input.
    result = sp.run_sensei_prep(timeout_s=30.0)
    assert result is not None
    assert "cycle_id" in result
    assert "pillars" in result


def test_run_sensei_prep_missing_script_returns_none(monkeypatch):
    monkeypatch.setattr(sp, "_GOVERNANCE_BIN", Path("/nonexistent/dir"))
    assert sp.run_sensei_prep(timeout_s=5.0) is None
