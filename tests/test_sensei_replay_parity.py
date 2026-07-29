"""Tests for Governance/bin/sensei_replay_parity.py (B2 REPLAY decision-parity harness,
docs/plans/2026-07-27-meta-harness-uplift.md Phase B2 / B1-SPEC.md).

Synthetic fixtures only — no real SENSEI_LEDGER.jsonl, no subprocess spawn of
sensei_replay_run.mjs (that integration is exercised by actually running the script against
real history, not by these tests). These tests pin the RECONSTRUCTION logic: parsing a
freeform scout_verdict string into (real, fixability), truncating ledger/exec_log history to
before a cycle's own timestamp, excluding post_audit rows from a cycle's eligible set,
building the synthetic wid_payload/outcomes, and tallying recorded-vs-replayed decisions per
class.
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

import sensei_replay_parity as srp  # noqa: E402

UTC = timezone.utc
CYCLE_TS = datetime(2026, 7, 18, 20, 57, 38, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ---------------------------------------------------------------------------
# parse_scout_verdict — real replay data is freeform; malformed strings must degrade
# gracefully, never raise.
# ---------------------------------------------------------------------------

def test_parse_scout_verdict_simple_real_and_fixability_word():
    assert srp.parse_scout_verdict("real:false structural") == (False, "structural")
    assert srp.parse_scout_verdict("real:true skill") == (True, "skill")


def test_parse_scout_verdict_fixability_key_form():
    raw = "real:false fixability:phantom (re-measured 5.8 vs snapshot 4.7)"
    assert srp.parse_scout_verdict(raw) == (False, "phantom")


def test_parse_scout_verdict_multi_clause_correlation_string_takes_first_real_match():
    raw = ("Total_Cost constituent real:true (19328.40 USD/30d, reproduced by rival at "
           "19651.52); Frustration_Signals constituent real:false (0.29/session)")
    real, fixability = srp.parse_scout_verdict(raw)
    assert real is True  # first "real:" occurrence wins
    assert fixability is None  # no known fixability token present


def test_parse_scout_verdict_malformed_string_has_no_real_token():
    real, fixability = srp.parse_scout_verdict("claimed from state/wiki_link.json, no verdict field at all")
    assert real is None
    assert fixability is None


def test_parse_scout_verdict_none_and_empty_input():
    assert srp.parse_scout_verdict(None) == (None, None)
    assert srp.parse_scout_verdict("") == (None, None)
    assert srp.parse_scout_verdict(123) == (None, None)  # not a string at all


def test_parse_scout_verdict_unknown_fixability_word_is_not_matched():
    # "not_a_defect" is a real fixability-shaped word in the ledger but not one of the
    # four ScoutFinding.fixability enum values — must not be mistaken for a known one.
    real, fixability = srp.parse_scout_verdict("real:false not_a_defect")
    assert real is False
    assert fixability is None


# ---------------------------------------------------------------------------
# scout_finding_for_row — defaults + warnings
# ---------------------------------------------------------------------------

def test_scout_finding_for_row_defaults_unparseable_real_to_false_with_warning():
    row = {"reflex_id": "metric:bow:X", "scout_verdict": "no real token here"}
    finding, warning = srp.scout_finding_for_row(row)
    assert finding["real"] is False
    assert "fixability" not in finding
    assert warning is not None
    assert "metric:bow:X" in warning


def test_scout_finding_for_row_no_warning_when_parseable():
    row = {"reflex_id": "metric:bow:X", "scout_verdict": "real:true skill"}
    finding, warning = srp.scout_finding_for_row(row)
    assert finding == {"reflex_id": "metric:bow:X", "real": True, "fixability": "skill",
                        "root_cause": "real:true skill"}
    assert warning is None


# ---------------------------------------------------------------------------
# truncate_before — ledger/exec_log truncation correctness (strictly before cutoff)
# ---------------------------------------------------------------------------

def test_truncate_before_keeps_only_strictly_earlier_rows():
    rows = [
        {"ts": _iso(CYCLE_TS - timedelta(hours=1))},  # kept
        {"ts": _iso(CYCLE_TS)},                        # excluded — not strictly before
        {"ts": _iso(CYCLE_TS + timedelta(hours=1))},  # excluded — after
    ]
    out = srp.truncate_before(rows, CYCLE_TS, "ts")
    assert out == [rows[0]]


def test_truncate_before_drops_unparseable_timestamps():
    rows = [{"ts": "not-a-timestamp"}, {"ts": None}, {}]
    assert srp.truncate_before(rows, CYCLE_TS, "ts") == []


def test_truncate_before_uses_the_given_ts_key_for_exec_log_rows():
    rows = [{"timestamp": _iso(CYCLE_TS - timedelta(days=1))}]
    assert srp.truncate_before(rows, CYCLE_TS, "timestamp") == rows


# ---------------------------------------------------------------------------
# eligible_rows_for_cycle — post_audit-row exclusion
# ---------------------------------------------------------------------------

def test_eligible_rows_for_cycle_excludes_post_audit_rows():
    rows = [
        {"reflex_id": "metric:arts:A", "action_taken": "verdict_posted"},
        {"reflex_id": "metric:arts:Simplify_Age", "action_taken": "post_audit"},
        {"reflex_id": "metric:brush:B", "action_taken": "suppressed"},
    ]
    out = srp.eligible_rows_for_cycle(rows)
    assert [r["reflex_id"] for r in out] == ["metric:arts:A", "metric:brush:B"]


# ---------------------------------------------------------------------------
# build_wid_payload / build_outcomes
# ---------------------------------------------------------------------------

def test_build_wid_payload_hardcodes_tier_and_status_and_dedups():
    rows = [
        {"reflex_id": "metric:bow:A", "pillar": "bow"},
        {"reflex_id": "metric:bow:A", "pillar": "bow"},  # duplicate reflex_id, dropped
        {"reflex_id": "metric:sword:B", "pillar": "sword"},
    ]
    payload = srp.build_wid_payload(rows)
    ids = [r["id"] for r in payload["reflexes"]]
    assert ids == ["metric:bow:A", "metric:sword:B"]
    for r in payload["reflexes"]:
        assert r["tier"] == "CRITICAL"
        assert r["status"] == "active"


def test_build_outcomes_groups_scouts_by_pillar_and_collects_rival_verdicts():
    rows = [
        {"reflex_id": "metric:bow:A", "pillar": "bow", "scout_verdict": "real:true skill",
         "rival_verdict": "CONFIRMED"},
        {"reflex_id": "metric:sword:B", "pillar": "sword", "scout_verdict": "real:false structural",
         "rival_verdict": None},
    ]
    outcomes, warnings = srp.build_outcomes(rows)
    assert warnings == []
    assert [f["reflex_id"] for f in outcomes["scouts"]["bow"]] == ["metric:bow:A"]
    assert [f["reflex_id"] for f in outcomes["scouts"]["sword"]] == ["metric:sword:B"]
    assert outcomes["rivals"] == {"metric:bow:A": "CONFIRMED"}  # no rival ran for B (None)


def test_build_outcomes_surfaces_parse_warnings():
    rows = [{"reflex_id": "metric:bow:A", "pillar": "bow", "scout_verdict": "garbage", "rival_verdict": None}]
    _, warnings = srp.build_outcomes(rows)
    assert len(warnings) == 1
    assert "metric:bow:A" in warnings[0]


# ---------------------------------------------------------------------------
# refuted_within_24h / has_two_consecutive_refuted
# ---------------------------------------------------------------------------

def test_refuted_within_24h_true_inside_window():
    rows = [{"reflex_id": "metric:bow:A", "rival_verdict": "REFUTED", "ts": _iso(CYCLE_TS - timedelta(hours=5))}]
    assert srp.refuted_within_24h("metric:bow:A", CYCLE_TS, rows) is True


def test_refuted_within_24h_false_outside_window():
    rows = [{"reflex_id": "metric:bow:A", "rival_verdict": "REFUTED", "ts": _iso(CYCLE_TS - timedelta(hours=25))}]
    assert srp.refuted_within_24h("metric:bow:A", CYCLE_TS, rows) is False


def test_refuted_within_24h_ignores_non_refuted_verdicts():
    rows = [{"reflex_id": "metric:bow:A", "rival_verdict": "CONFIRMED", "ts": _iso(CYCLE_TS - timedelta(hours=1))}]
    assert srp.refuted_within_24h("metric:bow:A", CYCLE_TS, rows) is False


def test_has_two_consecutive_refuted_true_when_prior_row_also_refuted():
    rows = [{"reflex_id": "metric:bow:A", "rival_verdict": "REFUTED"}]
    assert srp.has_two_consecutive_refuted("metric:bow:A", "REFUTED", rows) is True


def test_has_two_consecutive_refuted_false_when_this_verdict_is_not_refuted():
    rows = [{"reflex_id": "metric:bow:A", "rival_verdict": "REFUTED"}]
    assert srp.has_two_consecutive_refuted("metric:bow:A", "CONFIRMED", rows) is False


def test_has_two_consecutive_refuted_false_with_no_prior_rows():
    assert srp.has_two_consecutive_refuted("metric:bow:A", "REFUTED", []) is False


# ---------------------------------------------------------------------------
# compare_class — per-class tallying + a divergence being reported
# ---------------------------------------------------------------------------

def test_compare_class_perfect_agreement():
    eligible = {"metric:bow:A", "metric:sword:B"}
    result = srp.compare_class("escalation", eligible, {"metric:bow:A"}, {"metric:bow:A"})
    assert result["agreements"] == 2
    assert result["total"] == 2
    assert result["divergences"] == []


def test_compare_class_reports_a_divergence_with_both_sides_choice():
    eligible = {"metric:bow:A", "metric:sword:B"}
    recorded = {"metric:bow:A"}          # recorded: A escalated, B not
    replayed = {"metric:bow:A", "metric:sword:B"}  # replayed: both escalated
    result = srp.compare_class("escalation", eligible, recorded, replayed)
    assert result["agreements"] == 1
    assert result["total"] == 2
    assert result["divergences"] == [{"reflex_id": "metric:sword:B", "recorded": False, "replayed": True}]


# ---------------------------------------------------------------------------
# group_cycles
# ---------------------------------------------------------------------------

def test_group_cycles_sorts_chronologically():
    rows = [
        {"cycle_id": "later", "ts": _iso(CYCLE_TS + timedelta(hours=6))},
        {"cycle_id": "earlier", "ts": _iso(CYCLE_TS)},
    ]
    cycles = srp.group_cycles(rows)
    assert [c[0] for c in cycles] == ["earlier", "later"]


def test_group_cycles_skips_rows_without_cycle_id_or_parseable_ts():
    rows = [{"cycle_id": None, "ts": _iso(CYCLE_TS)}, {"cycle_id": "x", "ts": "garbage"}]
    assert srp.group_cycles(rows) == []


# ---------------------------------------------------------------------------
# explain_escalation
# ---------------------------------------------------------------------------

def test_explain_escalation_identifies_structural_fixability():
    explanation = srp.explain_escalation("metric:arts:A", "structural", "REFUTED", [], [])
    assert "structural" in explanation


def test_explain_escalation_identifies_stuck_exec_log_pattern():
    exec_log = [{"reflex_id": "metric:brush:B", "improved": False} for _ in range(2)]
    explanation = srp.explain_escalation("metric:brush:B", None, None, exec_log, [])
    assert "engine stuck" in explanation


def test_explain_escalation_falls_back_when_no_known_cause_identified():
    explanation = srp.explain_escalation("metric:bow:A", None, None, [], [])
    assert "not identified" in explanation


# ---------------------------------------------------------------------------
# resolve_state_file — worktree-first, main-tree fallback (state files live in the
# conductor-prep worktree, not the main tree — this has bitten prior steps).
# ---------------------------------------------------------------------------

def test_resolve_state_file_prefers_worktree_when_present(tmp_path, monkeypatch):
    worktree_osr = tmp_path / "worktree" / "Order Samurai"
    main_osr = tmp_path / "main" / "Order Samurai"
    (worktree_osr / "state").mkdir(parents=True)
    (worktree_osr / "state" / "SENSEI_LEDGER.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr(srp, "WORKTREE_OSR", worktree_osr)
    monkeypatch.setattr(srp, "MAIN_OSR", main_osr)
    assert srp.resolve_state_file("SENSEI_LEDGER.jsonl") == worktree_osr / "state" / "SENSEI_LEDGER.jsonl"


def test_resolve_state_file_falls_back_to_main_tree(tmp_path, monkeypatch):
    worktree_osr = tmp_path / "worktree" / "Order Samurai"  # never created
    main_osr = tmp_path / "main" / "Order Samurai"
    monkeypatch.setattr(srp, "WORKTREE_OSR", worktree_osr)
    monkeypatch.setattr(srp, "MAIN_OSR", main_osr)
    assert srp.resolve_state_file("SENSEI_LEDGER.jsonl") == main_osr / "state" / "SENSEI_LEDGER.jsonl"
