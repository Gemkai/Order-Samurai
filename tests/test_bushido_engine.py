"""Tests for the Bushido Engine — unified tier decision module.

Covers compute_tier matrix, ronin-mode collapsing, hard-stop precedence,
enqueue_hitl idempotency, consume-on-check approval flow, and
skill_to_work_item metadata lookup.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Make agentica_core importable when pytest runs from repo root.
# bushido_engine now lives in the canonical Governance kernel (parents[2]).
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
_GOVERNANCE = Path(__file__).resolve().parents[2]
if str(_GOVERNANCE) not in sys.path:
    sys.path.insert(0, str(_GOVERNANCE))

from agentica_core.bushido_engine import (  # noqa: E402
    BlastRadius,
    ROLE_REQUESTED_CEILING,
    Tier,
    WorkItem,
    _consume_approval,
    compute_tier,
    decide,
    enqueue_hitl,
    load_skill_metadata,
    mark_complete,
    reconcile_stale_executing,
    resolve_ceiling,
    resolve_role_binding,
    resolve_ronin_mode,
    review_hitl,
    skill_to_work_item,
)


# ── compute_tier matrix ───────────────────────────────────────────────────────

def test_hard_stop_irreversible_and_high_blast():
    wi = WorkItem(blast_radius=BlastRadius.IRREVERSIBLE, reversible=False)
    assert compute_tier(wi) == Tier.HARD_STOP


def test_hard_stop_any_blast_if_irreversible_and_high():
    wi = WorkItem(blast_radius=BlastRadius.REPO, reversible=False)
    assert compute_tier(wi) == Tier.HARD_STOP

    wi_sys = WorkItem(blast_radius=BlastRadius.SYSTEM, reversible=False)
    assert compute_tier(wi_sys) == Tier.HARD_STOP


def test_irreversible_blast_hard_stop_even_when_marked_reversible():
    # blast=IRREVERSIBLE always wins, regardless of the reversible flag.
    wi = WorkItem(blast_radius=BlastRadius.IRREVERSIBLE, reversible=True)
    assert compute_tier(wi) == Tier.HARD_STOP


def test_auto_confined_reversible():
    wi = WorkItem(blast_radius=BlastRadius.CONFINED, reversible=True)
    assert compute_tier(wi) == Tier.AUTO


def test_queue_repo_reversible():
    wi = WorkItem(blast_radius=BlastRadius.REPO, reversible=True)
    assert compute_tier(wi) == Tier.QUEUE


def test_queue_system_reversible():
    wi = WorkItem(blast_radius=BlastRadius.SYSTEM, reversible=True)
    assert compute_tier(wi) == Tier.QUEUE


def test_hitl_confined_irreversible():
    wi = WorkItem(blast_radius=BlastRadius.CONFINED, reversible=False)
    assert compute_tier(wi) == Tier.HITL


def test_ronin_mode_collapses_queue_to_auto():
    wi = WorkItem(blast_radius=BlastRadius.REPO, reversible=True)
    assert compute_tier(wi, ronin_mode=True) == Tier.AUTO


def test_ronin_mode_collapses_hitl_to_auto():
    wi = WorkItem(blast_radius=BlastRadius.CONFINED, reversible=False)
    assert compute_tier(wi, ronin_mode=True) == Tier.AUTO


def test_ronin_mode_does_not_lift_hard_stop():
    wi = WorkItem(blast_radius=BlastRadius.IRREVERSIBLE, reversible=False)
    assert compute_tier(wi, ronin_mode=True) == Tier.HARD_STOP

    wi_repo = WorkItem(blast_radius=BlastRadius.REPO, reversible=False)
    assert compute_tier(wi_repo, ronin_mode=True) == Tier.HARD_STOP


# ── enqueue_hitl idempotency ──────────────────────────────────────────────────

@pytest.fixture()
def tmp_repo(tmp_path):
    """Build a repo-shaped tmp_path with empty state/."""
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "hitl_queue.json").write_text(
        json.dumps({"schema_version": 1, "items": [], "created_at": "x", "updated_at": "x"}),
        encoding="utf-8",
    )
    return tmp_path


def test_enqueue_hitl_idempotent(tmp_repo):
    wi = WorkItem(
        skill="simplify", source="reflex", pillar="arts",
        blast_radius=BlastRadius.REPO, reversible=True,
        metric_id="metric:arts:Simplify_Age",
    )
    id1 = enqueue_hitl(wi, Tier.QUEUE, tmp_repo)
    id2 = enqueue_hitl(wi, Tier.QUEUE, tmp_repo)
    assert id1 == id2

    data = json.loads((tmp_repo / "state" / "hitl_queue.json").read_text())
    pending = [i for i in data["items"] if i.get("status") == "pending"]
    assert len(pending) == 1


def test_enqueue_distinguishes_by_pillar(tmp_repo):
    """R4: pillar is part of the approval key."""
    wi_arts = WorkItem(skill="simplify", source="meditation", pillar="arts",
                       blast_radius=BlastRadius.REPO, reversible=True)
    wi_brush = WorkItem(skill="simplify", source="meditation", pillar="brush",
                        blast_radius=BlastRadius.REPO, reversible=True)
    id_a = enqueue_hitl(wi_arts, Tier.QUEUE, tmp_repo)
    id_b = enqueue_hitl(wi_brush, Tier.QUEUE, tmp_repo)
    assert id_a != id_b


# ── consume-on-check (R2 + R4) ────────────────────────────────────────────────

def test_consume_approval_returns_auto(tmp_repo):
    """Manually inject an `approved` item; decide() consumes it and returns AUTO."""
    wi = WorkItem(
        skill="simplify", source="reflex", pillar="arts",
        blast_radius=BlastRadius.REPO, reversible=True,
        metric_id="metric:arts:Simplify_Age",
    )
    # Pre-seed an approved item with the same key
    queue_path = tmp_repo / "state" / "hitl_queue.json"
    data = json.loads(queue_path.read_text())
    data["items"].append({
        "id": "hitl-pre1",
        "source": "reflex",
        "skill": "simplify",
        "pillar": "arts",
        "metric_id": "metric:arts:Simplify_Age",
        "backlog_id": None,
        "status": "approved",
        "tier_assigned": "queue",
        "enqueued_at": "x", "approved_at": "y",
        "rejected_at": None, "rejected_reason": None,
        "executing_at": None, "completed_at": None,
        "command": "/simplify",
        "blast_radius": "repo", "reversible": True,
        "consecutive_no_improvement": 0, "stuck": False, "context": "",
    })
    queue_path.write_text(json.dumps(data))

    tier, qid = decide(wi, tmp_repo)
    assert tier == Tier.AUTO
    assert qid == "hitl-pre1"  # consumed id returned for downstream --complete

    # And the queued item is now marked executing
    data2 = json.loads(queue_path.read_text())
    matched = [i for i in data2["items"] if i["id"] == "hitl-pre1"][0]
    assert matched["status"] == "executing"
    assert matched["executing_at"] is not None


def test_consume_approval_not_triggered_for_pending(tmp_repo):
    """A pending (not approved) item must NOT short-circuit the tier check."""
    wi = WorkItem(
        skill="simplify", source="reflex", pillar="arts",
        blast_radius=BlastRadius.REPO, reversible=True,
        metric_id="metric:arts:Simplify_Age",
    )
    queue_path = tmp_repo / "state" / "hitl_queue.json"
    data = json.loads(queue_path.read_text())
    data["items"].append({
        "id": "hitl-pending1",
        "source": "reflex",
        "skill": "simplify",
        "pillar": "arts",
        "metric_id": "metric:arts:Simplify_Age",
        "backlog_id": None,
        "status": "pending",   # NOT approved
        "tier_assigned": "queue",
        "enqueued_at": "x", "approved_at": None,
        "rejected_at": None, "rejected_reason": None,
        "executing_at": None, "completed_at": None,
        "command": "/simplify",
        "blast_radius": "repo", "reversible": True,
        "consecutive_no_improvement": 0, "stuck": False, "context": "",
    })
    queue_path.write_text(json.dumps(data))

    assert _consume_approval(wi, tmp_repo) is None
    # Still pending after the call
    data2 = json.loads(queue_path.read_text())
    matched = [i for i in data2["items"] if i["id"] == "hitl-pending1"][0]
    assert matched["status"] == "pending"


def test_decide_returns_existing_queue_id_on_second_call(tmp_repo):
    """Calling decide() twice for the same QUEUE work item → same queue_id."""
    wi = WorkItem(
        skill="simplify", source="reflex", pillar="arts",
        blast_radius=BlastRadius.REPO, reversible=True,
        metric_id="metric:arts:Simplify_Age",
    )
    tier1, id1 = decide(wi, tmp_repo)
    tier2, id2 = decide(wi, tmp_repo)
    assert tier1 == tier2 == Tier.QUEUE
    assert id1 == id2 and id1 is not None


# ── decision audit log ────────────────────────────────────────────────────────

def test_queue_decision_emits_audit_line(tmp_repo):
    """A QUEUE decision appends one bushido_decision event to autonomic_events.jsonl."""
    wi = WorkItem(
        skill="simplify", source="reflex", pillar="arts",
        blast_radius=BlastRadius.REPO, reversible=True,
        metric_id="metric:arts:Simplify_Age",
    )
    tier, qid = decide(wi, tmp_repo, global_ronin_override=False)
    assert tier == Tier.QUEUE

    log_path = tmp_repo / "state" / "autonomic_events.jsonl"
    assert log_path.exists()
    events = [
        json.loads(ln) for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()
    ]
    decisions = [e for e in events if e.get("event") == "bushido_decision"]
    assert len(decisions) == 1
    assert decisions[0]["tier"] == "queue"
    assert decisions[0]["skill"] == "simplify"
    assert decisions[0]["queue_id"] == qid
    assert decisions[0]["pillar"] == "arts"


def test_plain_auto_decision_not_logged(tmp_repo):
    """A plain AUTO decision (confined/reversible) must NOT write an audit line."""
    wi = WorkItem(
        skill="canary-fault-diagnosis", source="reflex", pillar="sword",
        blast_radius=BlastRadius.CONFINED, reversible=True,
    )
    tier, _ = decide(wi, tmp_repo, global_ronin_override=False)
    assert tier == Tier.AUTO

    log_path = tmp_repo / "state" / "autonomic_events.jsonl"
    if log_path.exists():
        events = [
            json.loads(ln) for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()
        ]
        assert not [e for e in events if e.get("event") == "bushido_decision"]


# ── skill_to_work_item metadata lookup ────────────────────────────────────────

@pytest.fixture()
def tmp_repo_with_tiers(tmp_repo):
    tiers = {
        "schema_version": 1,
        "skills": {
            "simplify": {"blast_radius": "repo", "reversible": True, "approval_tier": "queue"},
            "canary-fault-diagnosis": {"blast_radius": "confined", "reversible": True, "approval_tier": "auto"},
            "sensei-cycle": {"blast_radius": "repo", "reversible": True, "approval_tier": "queue"},
        },
    }
    (tmp_repo / "state" / "skill_tiers.json").write_text(json.dumps(tiers), encoding="utf-8")
    return tmp_repo


def test_skill_to_work_item_uses_metadata(tmp_repo_with_tiers):
    wi = skill_to_work_item("simplify", source="reflex", repo_root=tmp_repo_with_tiers)
    assert wi.blast_radius == BlastRadius.REPO
    assert wi.reversible is True


def test_skill_to_work_item_unknown_defaults_to_queue(tmp_repo_with_tiers):
    wi = skill_to_work_item("does-not-exist-anywhere", source="reflex", repo_root=tmp_repo_with_tiers)
    assert wi.blast_radius == BlastRadius.REPO
    assert wi.reversible is True
    # Maps to QUEUE
    assert compute_tier(wi) == Tier.QUEUE


def test_load_skill_metadata_missing_returns_empty(tmp_path):
    # No state/skill_tiers.json — should return {}
    assert load_skill_metadata(tmp_path) == {}


# ── resolve_ronin_mode priority ───────────────────────────────────────────────

def test_resolve_ronin_env_override(tmp_repo, monkeypatch):
    monkeypatch.setenv("BUSHIDO_RONIN_GLOBAL", "true")
    assert resolve_ronin_mode("arts", tmp_repo) is True

    monkeypatch.setenv("BUSHIDO_RONIN_GLOBAL", "false")
    assert resolve_ronin_mode("arts", tmp_repo) is False


def test_resolve_ronin_meditation_state_top_level(tmp_repo, monkeypatch):
    monkeypatch.delenv("BUSHIDO_RONIN_GLOBAL", raising=False)
    (tmp_repo / "state" / "MEDITATION_STATE.json").write_text(json.dumps({
        "ronin_mode": "ronin",
        "pillars": {"arts": {"ronin_mode": "dormant"}},
    }))
    # Top-level wins over per-pillar
    assert resolve_ronin_mode("arts", tmp_repo) is True


def test_top_level_dormant_falls_through_to_per_pillar(tmp_repo, monkeypatch):
    """Per Phase 2.3 spec: 'When absent or "dormant", per-pillar settings apply.'"""
    monkeypatch.delenv("BUSHIDO_RONIN_GLOBAL", raising=False)
    (tmp_repo / "state" / "MEDITATION_STATE.json").write_text(json.dumps({
        "ronin_mode": "dormant",   # explicit dormant
        "pillars": {"arts": {"ronin_mode": "ronin"}},   # arts IS ronin
    }))
    assert resolve_ronin_mode("arts", tmp_repo) is True
    # And a pillar not in ronin_mode stays False
    (tmp_repo / "state" / "MEDITATION_STATE.json").write_text(json.dumps({
        "ronin_mode": "dormant",
        "pillars": {"bow": {"ronin_mode": "dormant"}},
    }))
    assert resolve_ronin_mode("bow", tmp_repo) is False


def test_resolve_ronin_per_pillar_fallback(tmp_repo, monkeypatch):
    monkeypatch.delenv("BUSHIDO_RONIN_GLOBAL", raising=False)
    (tmp_repo / "state" / "MEDITATION_STATE.json").write_text(json.dumps({
        # No top-level ronin_mode
        "pillars": {"arts": {"ronin_mode": "ronin"}, "bow": {"ronin_mode": "dormant"}},
    }))
    assert resolve_ronin_mode("arts", tmp_repo) is True
    assert resolve_ronin_mode("bow", tmp_repo) is False


def test_resolve_ronin_default_false(tmp_repo, monkeypatch):
    monkeypatch.delenv("BUSHIDO_RONIN_GLOBAL", raising=False)
    # No MEDITATION_STATE.json
    assert resolve_ronin_mode("arts", tmp_repo) is False


def test_resolve_ronin_global_override_param_wins(tmp_repo, monkeypatch):
    monkeypatch.setenv("BUSHIDO_RONIN_GLOBAL", "true")
    # global_override parameter beats env
    assert resolve_ronin_mode("arts", tmp_repo, global_override=False) is False


# ── Budget hard-limit (R7) ────────────────────────────────────────────────────

def test_budget_overrun_hard_stops_decide(tmp_repo):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    (tmp_repo / "state" / "budget_ledger.json").write_text(json.dumps({
        "date": today,
        "spent_usd": 6.50,
        "daily_limit_usd": 5.00,
        "cycles": 30,
    }))
    wi = WorkItem(
        skill="canary-fault-diagnosis", source="reflex", pillar="sword",
        blast_radius=BlastRadius.CONFINED, reversible=True,
    )
    tier, qid = decide(wi, tmp_repo)
    assert tier == Tier.HARD_STOP
    assert qid is None


def test_zero_daily_limit_freezes_spending(tmp_repo):
    """An explicit $0 limit is a budget freeze, not a fall-through to the $5 default."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    (tmp_repo / "state" / "budget_ledger.json").write_text(json.dumps({
        "date": today,
        "spent_usd": 0.10,
        "daily_limit_usd": 0,
    }))
    wi = WorkItem(
        skill="canary-fault-diagnosis", source="reflex", pillar="sword",
        blast_radius=BlastRadius.CONFINED, reversible=True,
    )
    tier, qid = decide(wi, tmp_repo)
    assert tier == Tier.HARD_STOP
    assert qid is None


def test_budget_on_different_date_is_not_over(tmp_repo):
    # ledger date is yesterday — treat as fresh day, not over
    (tmp_repo / "state" / "budget_ledger.json").write_text(json.dumps({
        "date": "2020-01-01",
        "spent_usd": 100.0,
        "daily_limit_usd": 5.00,
    }))
    wi = WorkItem(
        skill="canary-fault-diagnosis", source="reflex",
        blast_radius=BlastRadius.CONFINED, reversible=True,
    )
    tier, _ = decide(wi, tmp_repo)
    assert tier == Tier.AUTO


@pytest.mark.parametrize("payload", [
    "{not json",
    "{}",
    json.dumps({"date": "not-a-date", "spent_usd": 0, "daily_limit_usd": 5}),
    json.dumps({"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "spent_usd": "NaN", "daily_limit_usd": 5}),
])
def test_invalid_budget_ledger_fails_closed(tmp_repo, payload):
    """A broken hard-limit ledger must not silently re-enable autonomous spend."""
    (tmp_repo / "state" / "budget_ledger.json").write_text(payload, encoding="utf-8")
    wi = WorkItem(
        skill="canary-fault-diagnosis", source="reflex",
        blast_radius=BlastRadius.CONFINED, reversible=True,
    )
    tier, qid = decide(wi, tmp_repo)
    assert tier == Tier.HARD_STOP
    assert qid is None


# ── R2: approval must NOT bypass newly-added hard limit ───────────────────────

def test_approval_does_not_bypass_hard_limit(tmp_repo):
    """Pre-existing `approved` entry plus budget overrun -> still HARD_STOP."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    (tmp_repo / "state" / "budget_ledger.json").write_text(json.dumps({
        "date": today, "spent_usd": 100.0, "daily_limit_usd": 5.00,
    }))
    queue_path = tmp_repo / "state" / "hitl_queue.json"
    data = json.loads(queue_path.read_text())
    data["items"].append({
        "id": "hitl-old",
        "source": "meditation", "skill": "simplify", "pillar": "arts",
        "metric_id": None, "backlog_id": None,
        "status": "approved", "tier_assigned": "queue",
        "enqueued_at": "x", "approved_at": "y",
        "rejected_at": None, "rejected_reason": None,
        "executing_at": None, "completed_at": None,
        "command": "/simplify", "blast_radius": "repo", "reversible": True,
        "consecutive_no_improvement": 0, "stuck": False, "context": "",
    })
    queue_path.write_text(json.dumps(data))

    wi = WorkItem(
        skill="simplify", source="meditation", pillar="arts",
        blast_radius=BlastRadius.REPO, reversible=True,
    )
    tier, _ = decide(wi, tmp_repo)
    assert tier == Tier.HARD_STOP

    # The approved item must remain approved (not consumed by a hard-stop call)
    data2 = json.loads(queue_path.read_text())
    matched = [i for i in data2["items"] if i["id"] == "hitl-old"][0]
    assert matched["status"] == "approved"


# ── mark_complete ─────────────────────────────────────────────────────────────

def test_mark_complete_done(tmp_repo):
    wi = WorkItem(
        skill="simplify", source="reflex", pillar="arts",
        blast_radius=BlastRadius.REPO, reversible=True,
    )
    qid = enqueue_hitl(wi, Tier.QUEUE, tmp_repo)
    assert mark_complete(qid, tmp_repo) is True

    data = json.loads((tmp_repo / "state" / "hitl_queue.json").read_text())
    matched = [i for i in data["items"] if i["id"] == qid][0]
    assert matched["status"] == "done"
    assert matched["completed_at"] is not None


def test_mark_complete_failed(tmp_repo):
    wi = WorkItem(
        skill="simplify", source="reflex", pillar="arts",
        blast_radius=BlastRadius.REPO, reversible=True,
    )
    qid = enqueue_hitl(wi, Tier.QUEUE, tmp_repo)
    assert mark_complete(qid, tmp_repo, failed=True) is True

    data = json.loads((tmp_repo / "state" / "hitl_queue.json").read_text())
    matched = [i for i in data["items"] if i["id"] == qid][0]
    assert matched["status"] == "failed"


def test_mark_complete_unknown_returns_false(tmp_repo):
    assert mark_complete("hitl-does-not-exist", tmp_repo) is False


def test_reconcile_stale_executing_marks_failed(tmp_repo):
    now = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
    queue = {
        "schema_version": 1,
        "items": [
            {"id": "old", "status": "executing",
             "executing_at": (now - timedelta(hours=3)).isoformat(),
             "completed_at": None},
            {"id": "fresh", "status": "executing",
             "executing_at": (now - timedelta(minutes=15)).isoformat(),
             "completed_at": None},
        ],
    }
    path = tmp_repo / "state" / "hitl_queue.json"
    path.write_text(json.dumps(queue), encoding="utf-8")

    assert reconcile_stale_executing(tmp_repo, max_age_hours=2, now=now) == 1
    items = {i["id"]: i for i in json.loads(path.read_text())["items"]}
    assert items["old"]["status"] == "failed"
    assert items["old"]["failure_reason"] == "execution_lease_expired"
    assert items["old"]["completed_at"] == now.isoformat()
    assert items["fresh"]["status"] == "executing"


def test_reconcile_missing_or_invalid_timestamp_fails_closed(tmp_repo):
    now = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
    queue = {"schema_version": 1, "items": [
        {"id": "missing", "status": "executing", "executing_at": None},
        {"id": "bad", "status": "executing", "executing_at": "not-a-date"},
    ]}
    path = tmp_repo / "state" / "hitl_queue.json"
    path.write_text(json.dumps(queue), encoding="utf-8")

    assert reconcile_stale_executing(tmp_repo, now=now) == 2
    assert all(i["status"] == "failed" for i in json.loads(path.read_text())["items"])


# ── review_hitl ────────────────────────────────────────────────────────────────

def _enqueue_pending(tmp_repo, **overrides):
    wi = WorkItem(
        skill=overrides.pop("skill", "simplify"), source="reflex", pillar="arts",
        blast_radius=BlastRadius.REPO, reversible=True, **overrides,
    )
    return enqueue_hitl(wi, Tier.QUEUE, tmp_repo)


def test_review_hitl_approve_sets_status_and_timestamp(tmp_repo):
    qid = _enqueue_pending(tmp_repo)
    assert review_hitl(qid, tmp_repo, "approve") is True

    data = json.loads((tmp_repo / "state" / "hitl_queue.json").read_text())
    matched = [i for i in data["items"] if i["id"] == qid][0]
    assert matched["status"] == "approved"
    assert matched["approved_at"] is not None


def test_review_hitl_reject_records_reason(tmp_repo):
    qid = _enqueue_pending(tmp_repo)
    assert review_hitl(qid, tmp_repo, "reject", reason="stale, superseded") is True

    data = json.loads((tmp_repo / "state" / "hitl_queue.json").read_text())
    matched = [i for i in data["items"] if i["id"] == qid][0]
    assert matched["status"] == "rejected"
    assert matched["rejected_at"] is not None
    assert matched["rejected_reason"] == "stale, superseded"


def test_review_hitl_expire_records_reason(tmp_repo):
    qid = _enqueue_pending(tmp_repo)
    assert review_hitl(qid, tmp_repo, "expire", reason="content-free probe artifact") is True

    data = json.loads((tmp_repo / "state" / "hitl_queue.json").read_text())
    matched = [i for i in data["items"] if i["id"] == qid][0]
    assert matched["status"] == "expired"
    assert matched["expired_at"] is not None
    assert matched["expired_reason"] == "content-free probe artifact"


def test_review_hitl_unknown_id_returns_false(tmp_repo):
    assert review_hitl("hitl-does-not-exist", tmp_repo, "approve") is False


def test_review_hitl_does_not_redecide_already_approved(tmp_repo):
    """A settled item must not be re-reviewed — this is the guard that stops
    the queue from silently rubber-stamping a decision that already happened."""
    qid = _enqueue_pending(tmp_repo)
    assert review_hitl(qid, tmp_repo, "approve") is True
    assert review_hitl(qid, tmp_repo, "reject", reason="changed my mind") is False

    data = json.loads((tmp_repo / "state" / "hitl_queue.json").read_text())
    matched = [i for i in data["items"] if i["id"] == qid][0]
    assert matched["status"] == "approved"  # untouched by the second call
    assert matched["rejected_reason"] is None


def test_review_hitl_invalid_action_raises(tmp_repo):
    qid = _enqueue_pending(tmp_repo)
    with pytest.raises(ValueError):
        review_hitl(qid, tmp_repo, "frobnicate")


def test_review_hitl_writes_audit_event(tmp_repo):
    qid = _enqueue_pending(tmp_repo, skill="wiki")
    review_hitl(qid, tmp_repo, "expire", reason="gating metric now reads 0")

    events_path = tmp_repo / "state" / "autonomic_events.jsonl"
    lines = [json.loads(ln) for ln in events_path.read_text().splitlines() if ln.strip()]
    review_events = [e for e in lines if e.get("event") == "hitl_review"]
    assert len(review_events) == 1
    assert review_events[0]["action"] == "expire"
    assert review_events[0]["queue_id"] == qid
    assert review_events[0]["skill"] == "wiki"


# ── approval_tier override (allowlist) ────────────────────────────────────────

# Skills whose shipped approval_tier deliberately differs from the 2-axis matrix
# cell. Everything else in state/skill_tiers.json must agree with the matrix, so
# that a future entry cannot quietly buy itself a softer tier — adding one here
# is the deliberate act that records the decision.
TIER_OVERRIDE_EXCEPTIONS = {
    # User decision 2026-07-28: "classify it as auto because that is ronin
    # mode's entire point." Repo blast is real and stated; the exception is to
    # the tier, not to the blast radius.
    "ronin-pillar": Tier.AUTO,
}


def _shipped_tier_table() -> dict[str, dict]:
    path = _REPO / "state" / "skill_tiers.json"
    return json.loads(path.read_text(encoding="utf-8"))["skills"]


def test_shipped_table_matches_matrix_except_declared_exceptions():
    """Guard: no skill drifts away from its matrix cell without being declared."""
    diverging = {}
    for name, meta in _shipped_tier_table().items():
        try:
            blast = BlastRadius(meta.get("blast_radius", "repo"))
        except ValueError:
            blast = BlastRadius.REPO
        matrix_only = compute_tier(
            WorkItem(skill=name, blast_radius=blast,
                     reversible=bool(meta.get("reversible", True)))
        )
        declared = Tier(str(meta["approval_tier"]).lower())
        if declared != matrix_only:
            diverging[name] = declared
    assert diverging == TIER_OVERRIDE_EXCEPTIONS


def test_ronin_pillar_resolves_to_auto():
    """The item-1 decision, asserted against the real shipped table."""
    wi = skill_to_work_item("ronin-pillar", source="reflex", repo_root=_REPO)
    # Blast radius stays honest — ronin-pillar commits to the live tree.
    assert wi.blast_radius == BlastRadius.REPO
    assert wi.approval_tier == "auto"
    assert compute_tier(wi) == Tier.AUTO


def test_override_does_not_widen_untabled_skills():
    """No override present → matrix default (QUEUE), unchanged."""
    wi = skill_to_work_item("no-such-skill-anywhere", source="reflex", repo_root=_REPO)
    assert wi.approval_tier is None
    assert compute_tier(wi) == Tier.QUEUE


@pytest.mark.parametrize("blast,reversible", [
    (BlastRadius.IRREVERSIBLE, True),
    (BlastRadius.IRREVERSIBLE, False),
    (BlastRadius.REPO, False),
    (BlastRadius.SYSTEM, False),
])
def test_override_cannot_escape_hard_stop(blast, reversible):
    """An `auto` override must never buy a hard-stopped op an auto-fire."""
    wi = WorkItem(skill="hostile", blast_radius=blast,
                  reversible=reversible, approval_tier="auto")
    assert compute_tier(wi) == Tier.HARD_STOP
    assert compute_tier(wi, ronin_mode=True) == Tier.HARD_STOP


def test_override_can_also_tighten():
    """The override is not auto-only: it can pin a confined skill to queue."""
    wi = WorkItem(skill="cautious", blast_radius=BlastRadius.CONFINED,
                  reversible=True, approval_tier="queue")
    assert compute_tier(wi) == Tier.QUEUE
    # ...and ronin mode still collapses it, as with any QUEUE.
    assert compute_tier(wi, ronin_mode=True) == Tier.AUTO


def test_unparseable_override_falls_back_to_matrix():
    wi = WorkItem(skill="typo", blast_radius=BlastRadius.REPO,
                  reversible=True, approval_tier="atuo")
    assert compute_tier(wi) == Tier.QUEUE


# ── Phase D1: role_binding ceiling resolution ─────────────────────────────────
# docs/plans/2026-07-27-meta-harness-uplift.md:162 — a role_binding's
# authority_ceiling narrows a package's requested BlastRadius, never widens it.

def test_resolve_ceiling_narrows_repo_request_under_confined_ceiling():
    """The literal plan verify: a package requesting `repo` under a `confined`
    ceiling resolves to `confined`."""
    assert resolve_ceiling(BlastRadius.REPO, BlastRadius.CONFINED) == BlastRadius.CONFINED


@pytest.mark.parametrize("requested,ceiling,expected", [
    (BlastRadius.REPO, BlastRadius.CONFINED, BlastRadius.CONFINED),
    (BlastRadius.SYSTEM, BlastRadius.REPO, BlastRadius.REPO),
    (BlastRadius.IRREVERSIBLE, BlastRadius.CONFINED, BlastRadius.CONFINED),
    # Already at or under the ceiling -> untouched (never WIDENED to the ceiling).
    (BlastRadius.CONFINED, BlastRadius.REPO, BlastRadius.CONFINED),
    (BlastRadius.CONFINED, BlastRadius.IRREVERSIBLE, BlastRadius.CONFINED),
    (BlastRadius.REPO, BlastRadius.REPO, BlastRadius.REPO),
])
def test_resolve_ceiling_never_widens(requested, ceiling, expected):
    assert resolve_ceiling(requested, ceiling) == expected


def test_resolve_ceiling_is_the_tighter_of_the_two_in_both_directions():
    """Symmetry check: whichever argument is tighter wins, regardless of position."""
    assert resolve_ceiling(BlastRadius.CONFINED, BlastRadius.SYSTEM) == BlastRadius.CONFINED
    assert resolve_ceiling(BlastRadius.SYSTEM, BlastRadius.CONFINED) == BlastRadius.CONFINED


def test_resolve_role_binding_writable_implementer_under_confined_ceiling():
    """A writable-implementer role (requests REPO) bound to a confined-ceiling
    workflow resolves to CONFINED — the read-only-reviewer verify scenario for
    a package that would otherwise want to write."""
    assert resolve_role_binding("writable-implementer", BlastRadius.CONFINED) == BlastRadius.CONFINED


def test_resolve_role_binding_read_only_reviewer_untouched_by_wide_ceiling():
    """A read-only-reviewer role's own CONFINED request is never widened by a
    generous ceiling."""
    assert resolve_role_binding("read-only-reviewer", BlastRadius.SYSTEM) == BlastRadius.CONFINED


def test_resolve_role_binding_same_package_two_workflows_two_ceilings():
    """Plan text: 'Same package runs writable-implementer in one workflow,
    read-only-reviewer in another, no frontmatter edit needed.' — the role
    alone (not the package) determines the requested ceiling; a critic@1-shaped
    package bound as writable-implementer in a repo-ceiling workflow gets REPO,
    the identical package bound as read-only-reviewer elsewhere gets CONFINED."""
    as_implementer = resolve_role_binding("writable-implementer", BlastRadius.REPO)
    as_reviewer = resolve_role_binding("read-only-reviewer", BlastRadius.REPO)
    assert as_implementer == BlastRadius.REPO
    assert as_reviewer == BlastRadius.CONFINED


def test_resolve_role_binding_unknown_role_defaults_to_repo_request():
    """Unknown role -> requests REPO (mirrors skill_to_work_item's unknown-skill
    default), still bounded by the ceiling like any other request."""
    assert "totally-unheard-of-role" not in ROLE_REQUESTED_CEILING
    assert resolve_role_binding("totally-unheard-of-role", BlastRadius.CONFINED) == BlastRadius.CONFINED
    assert resolve_role_binding("totally-unheard-of-role", BlastRadius.SYSTEM) == BlastRadius.REPO
