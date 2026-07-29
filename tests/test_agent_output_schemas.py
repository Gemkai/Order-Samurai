"""Tests for the Phase-A agent-output contracts (A1 schemas + A2 warn-only guard).

Three schemas under Governance/schema/ pin what the ronin scouts, the sensei ledger,
and the reflex engine's implementer envelope are allowed to emit. Python validates
before persisting (agentica_core.schema_guard, bin/sensei_writeback.py); the TS
reflex-engine validates the SAME files on startup — these tests pin the Python half,
exactly as test_wid_payload_schema.py does for the wid_payload seam.

DRAFT-07 IS LOAD-BEARING. The TS half uses ajv 6.15.0, which rejects a 2020-12
$schema outright, while Python jsonschema 4.26 accepts it happily. A schema
re-authored as 2020-12 would pass every test in this file and fail only in
production TS, so the draft is asserted explicitly per schema.

CORPUS NOTE. The ledger and exec_log fixtures are REAL rows copied verbatim from
state/ (which is untracked, so the fixtures are the durable copy). There is no
scout-finding corpus at all — state/ronin_results/*.json holds four remediation-item
singletons with divergent shapes, not scout findings — so scout_finding is validated
against contract-derived fixtures and must be re-validated against real scout runs.
Thresholds are therefore PER SCHEMA, not one global ratio.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
_GOVERNANCE = Path(__file__).resolve().parents[2]
if str(_GOVERNANCE) not in sys.path:
    sys.path.insert(0, str(_GOVERNANCE))

from agentica_core.schema_guard import (  # noqa: E402
    SCHEMA_DIR,
    VIOLATIONS_FILENAME,
    check_warn_only,
    load_schema,
    violations,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCHEMAS = ["scout_finding", "sensei_ledger_row", "remediation_result", "verdict_record"]


def _jsonl(name: str) -> list[dict]:
    path = FIXTURES / name
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ── Schema sanity ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", SCHEMAS)
def test_schema_file_is_valid_draft7(name):
    load_schema(name)  # load_schema itself runs Draft7Validator.check_schema


@pytest.mark.parametrize("name", SCHEMAS)
def test_schema_declares_draft7_explicitly(name):
    """ajv 6.15.0 (the TS half) rejects 2020-12. A silent draft bump would pass
    every Python test and break only production TS — so pin the declaration."""
    schema = json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))
    assert schema["$schema"] == "http://json-schema.org/draft-07/schema#"


# ── Real corpora validate clean (A1 verify, per-schema thresholds) ─────────────
#
# The plan's original "≥18/20 rows" bar assumed a corpus that does not exist.
# Actual corpora: 10 ledger rows, 164 exec_log rows (20 distinct key-set patterns
# captured verbatim as fixtures), 0 scout findings. So each schema gets the bar its
# corpus can actually support, and each states what it is measuring.

def test_every_real_ledger_row_validates():
    rows = _jsonl("sensei_ledger_rows.jsonl")
    assert len(rows) == 10, "fixture drifted from the captured corpus"
    failures = {i: violations(r, "sensei_ledger_row") for i, r in enumerate(rows)}
    assert not any(failures.values()), failures


def test_every_real_exec_log_row_validates():
    """20 rows, one per distinct key-set pattern found across all 164 real rows —
    so this covers every shape the engine has ever written, not a random sample."""
    rows = _jsonl("exec_log_rows.jsonl")
    patterns = {tuple(sorted(r)) for r in rows}
    assert len(patterns) == len(rows) == 20, "fixture no longer one row per pattern"
    failures = {i: violations(r, "remediation_result") for i, r in enumerate(rows)}
    assert not any(failures.values()), failures


def test_contract_derived_scout_findings_validate():
    findings = json.loads((FIXTURES / "scout_findings.json").read_text(encoding="utf-8"))
    failures = {i: violations(f, "scout_finding") for i, f in enumerate(findings)}
    assert not any(failures.values()), failures


# ── The seam invariants each schema exists to protect ─────────────────────────

def _ledger_row(**over):
    base = {
        "ts": "2026-07-27T00:00:00+00:00",
        "cycle_id": "11111111-2222-3333-4444-555555555555",
        "reflex_id": "metric:bow:Error_Rate",
        "pillar": "bow",
        "scout_verdict": "real:true skill",
        "rival_verdict": "CONFIRMED",
        "action_taken": "verdict_posted",
        "human_flag": False,
    }
    base.update(over)
    return base


def test_null_rival_verdict_is_valid_when_no_rival_ran():
    """Step 4 only spawns rival for real:true findings, so suppressed and escalated
    rows legitimately carry no verdict. 6 of the 10 real rows look like this."""
    assert not violations(
        _ledger_row(rival_verdict=None, action_taken="suppressed"), "sensei_ledger_row")


def test_null_scout_verdict_is_valid_on_post_audit_rows():
    """Post-audit spawns rival directly against exec_log entries — no scout runs."""
    assert not violations(
        _ledger_row(scout_verdict=None, action_taken="post_audit"), "sensei_ledger_row")


def test_verdict_posted_row_cannot_claim_a_verdict_it_never_had():
    """The one cross-field invariant: SKILL.md Step 6 posts only CONFIRMED/REFUTED,
    so verdict_posted with a null verdict means the POST and the ledger disagree."""
    assert violations(_ledger_row(rival_verdict=None), "sensei_ledger_row")


def test_verdict_posted_row_rejects_suspect():
    """SUSPECT is explicitly skipped at the POST (human_flag only)."""
    assert violations(_ledger_row(rival_verdict="SUSPECT"), "sensei_ledger_row")


@pytest.mark.parametrize("mutate,reason", [
    (lambda r: r.pop("cycle_id"), "missing-cycle-id"),
    (lambda r: r.update(pillar="fist"), "unknown-pillar"),
    (lambda r: r.update(rival_verdict="MAYBE"), "verdict-outside-vocabulary"),
    (lambda r: r.update(action_taken="posted"), "action-outside-vocabulary"),
    (lambda r: r.update(human_flag="yes"), "human-flag-not-boolean"),
])
def test_malformed_ledger_row_is_reported(mutate, reason):
    row = _ledger_row()
    mutate(row)
    assert violations(row, "sensei_ledger_row"), reason


def _exec_row(**over):
    base = {
        "timestamp": "2026-07-27T00:00:00.000Z",
        "command": "/simplify",
        "skill": "simplify",
        "status": "done",
    }
    base.update(over)
    return base


def test_manual_exec_row_without_improved_is_valid():
    """_appendExecLog OMITS `improved` on manual rows on purpose — an explicit false
    would count as a failure in skill_efficacy and lengthen autonomous cooldowns.
    Requiring the field would reclassify every healthy manual row as a violation."""
    assert not violations(_exec_row(source="dashboard_exec"), "remediation_result")


def test_worker_field_accepts_package_at_version():
    assert not violations(_exec_row(worker="critic@1"), "remediation_result")


def test_worker_field_rejects_an_unversioned_package():
    """`worker: critic` would defeat the whole point of A4 — identifying WHICH
    version ran, not just which package."""
    assert violations(_exec_row(worker="critic"), "remediation_result")


@pytest.mark.parametrize("mutate,reason", [
    (lambda r: r.pop("skill"), "missing-skill"),
    (lambda r: r.update(status="succeeded"), "status-outside-vocabulary"),
    (lambda r: r.update(kind="agent"), "kind-outside-vocabulary"),
    (lambda r: r.update(metric_before=None), "null-metric-is-not-absence"),
    (lambda r: r.update(predicted_impact={"stated": True}), "prediction-missing-arms"),
])
def test_malformed_exec_row_is_reported(mutate, reason):
    row = _exec_row()
    mutate(row)
    assert violations(row, "remediation_result"), reason


def _finding(**over):
    base = {
        "reflex_id": "metric:bow:Error_Rate",
        "real": True,
        "evidence": {"re_measured": 0.31, "source_file": "state/error_triage.json"},
        "root_cause": "still above threshold across the window",
        "proposed_fix": "/verifier-repair",
        "target_repo": "order-samurai",
        "risk_tier": "code",
        "fixability": "skill",
    }
    base.update(over)
    return base


def test_phantom_finding_may_propose_no_fix():
    assert not violations(
        _finding(real=False, fixability="phantom", proposed_fix=None), "scout_finding")


@pytest.mark.parametrize("mutate,reason", [
    (lambda f: f.pop("evidence"), "missing-evidence"),
    (lambda f: f.update(evidence={}), "evidence-without-a-measurement"),
    (lambda f: f["evidence"].pop("source_file"), "evidence-not-traceable-to-a-source"),
    (lambda f: f.update(target_repo="some-other-repo"), "repo-outside-vocabulary"),
    (lambda f: f.update(risk_tier="high"), "risk-tier-outside-vocabulary"),
    (lambda f: f.update(fixability="hard"), "fixability-outside-vocabulary"),
    (lambda f: f.update(real="yes"), "real-not-boolean"),
])
def test_malformed_scout_finding_is_reported(mutate, reason):
    finding = _finding()
    mutate(finding)
    assert violations(finding, "scout_finding"), reason


# ── A2: warn-only guard behaviour ─────────────────────────────────────────────

def test_valid_row_records_nothing(tmp_path):
    assert check_warn_only(_ledger_row(), "sensei_ledger_row", tmp_path) == []
    assert not (tmp_path / VIOLATIONS_FILENAME).exists()


def test_malformed_row_is_recorded_and_still_returns(tmp_path):
    """Warn-only: the guard reports, it does not raise. The caller still writes."""
    messages = check_warn_only(_ledger_row(pillar="fist"), "sensei_ledger_row", tmp_path,
                               context={"sink": "SENSEI_LEDGER.jsonl"})
    assert messages
    rows = [json.loads(line)
            for line in (tmp_path / VIOLATIONS_FILENAME).read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["event"] == "schema_violation"       # what A3's 7-day grep counts
    assert rows[0]["schema"] == "sensei_ledger_row"
    assert rows[0]["instance"]["pillar"] == "fist"      # diagnosable without a re-run
    assert rows[0]["sink"] == "SENSEI_LEDGER.jsonl"


def test_all_violations_are_reported_not_just_the_first():
    messages = violations(_ledger_row(pillar="fist", rival_verdict="MAYBE"),
                          "sensei_ledger_row")
    assert len(messages) >= 2


def test_unwritable_sink_never_breaks_the_caller(tmp_path):
    """A telemetry sink must not be able to break the write path it observes."""
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("i am a file")
    assert check_warn_only(_ledger_row(pillar="fist"), "sensei_ledger_row", blocked / "state")


def _verdict(**over):
    """The POST /api/reflex/verdicts wire shape — sensei_writeback is the poster,
    so the Python side owns this contract as much as the TS receiver does."""
    base = {
        "reflex_id": "metric:bow:Error_Rate",
        "verdict": "CONFIRMED",
        "reasoning": "live re-measurement reproduced the breach",
        "evidence": "tried refuting via the 30d window; the value held above threshold",
        "cycle_id": "11111111-2222-3333-4444-555555555555",
        "ts": "2026-07-27T00:00:00+00:00",
    }
    base.update(over)
    return base


def test_valid_verdict_record_passes():
    assert not violations(_verdict(), "verdict_record")


def test_verdict_record_is_not_a_ledger_row():
    """The two are different objects — conflating them is exactly what the naming
    avoids. A ledger row is not a valid POST body and vice versa."""
    assert violations(_ledger_row(), "verdict_record")
    assert violations(_verdict(), "sensei_ledger_row")


@pytest.mark.parametrize("mutate,reason", [
    (lambda v: v.pop("reasoning"), "no-reasoning-to-review"),
    (lambda v: v.update(evidence=""), "empty-evidence"),
    (lambda v: v.pop("cycle_id"), "unattributable-to-a-cycle"),
    (lambda v: v.update(verdict="MAYBE"), "verdict-outside-vocabulary"),
    (lambda v: v.update(reflex_ready="true"), "grant-flag-not-boolean"),
])
def test_malformed_verdict_record_is_reported(mutate, reason):
    verdict = _verdict()
    mutate(verdict)
    assert violations(verdict, "verdict_record"), reason


def test_server_layers_the_schema_on_top_of_its_400_gate():
    """The gate is a network boundary. A2 says 'reject nothing yet', but downgrading
    an existing hard rejection to warn-only would be a regression — so the gate must
    still be there, with the warn-only check added alongside it."""
    server_ts = (_GOVERNANCE / "api" / "src" / "server.ts").read_text(encoding="utf-8")
    assert "VALID_VERDICTS" in server_ts, "the 400 gate was removed, not layered on"
    assert "invalid verdict record" in server_ts
    assert "checkWarnOnly('verdict_record'" in server_ts


def test_sensei_writeback_validates_before_persisting():
    """The seam is only real if the producer actually calls the guard."""
    source = (_GOVERNANCE / "bin" / "sensei_writeback.py").read_text(encoding="utf-8")
    assert "check_warn_only" in source
    assert "sensei_ledger_row" in source


def test_typescript_half_reads_the_same_schema_files():
    """Cross-language seam: state.ts must point at these exact files, or the two
    halves silently enforce different contracts (the wid_payload seam's own rule)."""
    state_ts = (_GOVERNANCE / "api" / "src" / "state.ts").read_text(encoding="utf-8")
    for name in SCHEMAS:
        assert f"'{name}.schema.json'" in state_ts, name
