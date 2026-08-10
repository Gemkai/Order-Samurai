"""Tests for standing HITL approvals — "approve and don't ask again" (ADOPT-003).

Pins the safety scoping that makes this feature safe to have at all: grant is
refused outside `queue` tier, decide() only ever consults standing approvals
for `queue` tier, the match key is the full 5-tuple (no partial matches), and
the grant/revoke/list surface is legible (nothing silent, nothing black-box).
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

from agentica_core.bushido_engine import (  # noqa: E402
    BlastRadius,
    Tier,
    WorkItem,
    _has_standing_approval,
    decide,
    enqueue_hitl,
    grant_standing_approval,
    list_standing_approvals,
    review_hitl,
    revoke_standing_approval,
)


@pytest.fixture(autouse=True)
def _pull_only_semantics(monkeypatch):
    """Same pin as test_bushido_engine.py — approve_always must not POST to a
    real localhost API in this suite."""
    monkeypatch.setenv("BUSHIDO_PUSH_ON_APPROVE", "false")


@pytest.fixture()
def tmp_repo(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "hitl_queue.json").write_text(
        json.dumps({"schema_version": 1, "items": [], "created_at": "x", "updated_at": "x"}),
        encoding="utf-8",
    )
    return tmp_path


def _enqueue_pending(tmp_repo, tier=Tier.QUEUE, **overrides):
    wi = WorkItem(
        skill=overrides.pop("skill", "simplify"), source=overrides.pop("source", "reflex"),
        pillar=overrides.pop("pillar", "arts"),
        blast_radius=overrides.pop("blast_radius", BlastRadius.REPO),
        reversible=overrides.pop("reversible", True),
        **overrides,
    )
    return enqueue_hitl(wi, tier, tmp_repo), wi


_QKEY = ("reflex", "simplify", "arts", "metric:arts:Simplify_Age", "")


# ── grant / revoke / list — pure store behaviour ─────────────────────────────


def test_grant_succeeds_for_queue_tier_and_is_listed(tmp_repo):
    ok = grant_standing_approval(_QKEY, tmp_repo, "hitl-src1", tier_at_grant="queue")
    assert ok is True
    entries = list_standing_approvals(tmp_repo)
    assert len(entries) == 1
    assert tuple(entries[0]["key"]) == _QKEY
    assert entries[0]["granted_via_queue_id"] == "hitl-src1"


def test_grant_refused_for_hitl_tier(tmp_repo):
    ok = grant_standing_approval(_QKEY, tmp_repo, "hitl-src1", tier_at_grant="hitl")
    assert ok is False
    assert list_standing_approvals(tmp_repo) == []


def test_grant_refused_for_hard_stop_tier(tmp_repo):
    ok = grant_standing_approval(_QKEY, tmp_repo, "hitl-src1", tier_at_grant="hard_stop")
    assert ok is False
    assert list_standing_approvals(tmp_repo) == []


def test_grant_is_idempotent_not_duplicated(tmp_repo):
    grant_standing_approval(_QKEY, tmp_repo, "hitl-src1", tier_at_grant="queue")
    grant_standing_approval(_QKEY, tmp_repo, "hitl-src2", tier_at_grant="queue", reason="re-granted")
    entries = list_standing_approvals(tmp_repo)
    assert len(entries) == 1
    assert entries[0]["granted_via_queue_id"] == "hitl-src2"
    assert entries[0]["reason"] == "re-granted"


def test_revoke_removes_matching_entry(tmp_repo):
    grant_standing_approval(_QKEY, tmp_repo, "hitl-src1", tier_at_grant="queue")
    assert revoke_standing_approval(_QKEY, tmp_repo) is True
    assert list_standing_approvals(tmp_repo) == []


def test_revoke_unknown_key_returns_false(tmp_repo):
    assert revoke_standing_approval(_QKEY, tmp_repo) is False


def test_has_standing_approval_reflects_grant_and_revoke(tmp_repo):
    assert _has_standing_approval(_QKEY, tmp_repo) is False
    grant_standing_approval(_QKEY, tmp_repo, "hitl-src1", tier_at_grant="queue")
    assert _has_standing_approval(_QKEY, tmp_repo) is True
    revoke_standing_approval(_QKEY, tmp_repo)
    assert _has_standing_approval(_QKEY, tmp_repo) is False


def test_corrupt_standing_file_is_quarantined_not_fatal(tmp_repo):
    (tmp_repo / "state" / "hitl_standing_approvals.json").write_text("{not json", encoding="utf-8")
    ok = grant_standing_approval(_QKEY, tmp_repo, "hitl-src1", tier_at_grant="queue")
    assert ok is True
    assert (tmp_repo / "state" / "hitl_standing_approvals.corrupt.json").exists()
    assert list_standing_approvals(tmp_repo)[0]["granted_via_queue_id"] == "hitl-src1"


# ── review_hitl(action="approve_always") ─────────────────────────────────────


def test_approve_always_approves_and_grants_standing_for_queue_tier(tmp_repo):
    qid, wi = _enqueue_pending(tmp_repo, tier=Tier.QUEUE, metric_id="metric:arts:Simplify_Age")
    assert review_hitl(qid, tmp_repo, "approve_always") is True

    data = json.loads((tmp_repo / "state" / "hitl_queue.json").read_text())
    matched = [i for i in data["items"] if i["id"] == qid][0]
    assert matched["status"] == "approved"

    entries = list_standing_approvals(tmp_repo)
    assert len(entries) == 1
    assert tuple(entries[0]["key"]) == _QKEY
    assert entries[0]["granted_via_queue_id"] == qid


def test_approve_always_approves_but_does_not_grant_for_hitl_tier(tmp_repo):
    qid, wi = _enqueue_pending(
        tmp_repo, tier=Tier.HITL, metric_id="metric:arts:Simplify_Age",
        reversible=False, blast_radius=BlastRadius.CONFINED,
    )
    assert review_hitl(qid, tmp_repo, "approve_always") is True

    data = json.loads((tmp_repo / "state" / "hitl_queue.json").read_text())
    matched = [i for i in data["items"] if i["id"] == qid][0]
    assert matched["status"] == "approved"  # the one-off approve still lands

    assert list_standing_approvals(tmp_repo) == []  # but no standing grant


def test_approve_always_unknown_id_returns_false_no_grant(tmp_repo):
    assert review_hitl("hitl-does-not-exist", tmp_repo, "approve_always") is False
    assert list_standing_approvals(tmp_repo) == []


def test_approve_always_on_already_settled_item_returns_false(tmp_repo):
    qid, wi = _enqueue_pending(tmp_repo)
    review_hitl(qid, tmp_repo, "reject", reason="not needed")
    assert review_hitl(qid, tmp_repo, "approve_always") is False
    assert list_standing_approvals(tmp_repo) == []


def test_approve_always_records_reason(tmp_repo):
    qid, wi = _enqueue_pending(tmp_repo, metric_id="metric:arts:Simplify_Age")
    review_hitl(qid, tmp_repo, "approve_always", reason="low-risk, seen 5 times")
    assert list_standing_approvals(tmp_repo)[0]["reason"] == "low-risk, seen 5 times"


# ── decide() — the actual "stop re-prompting" behaviour ──────────────────────


def test_decide_skips_queue_entirely_once_standing_approval_matches(tmp_repo):
    wi = WorkItem(
        skill="simplify", source="reflex", pillar="arts",
        blast_radius=BlastRadius.REPO, reversible=True,
        metric_id="metric:arts:Simplify_Age",
    )
    grant_standing_approval(_QKEY, tmp_repo, "hitl-prior", tier_at_grant="queue")

    tier, queue_id = decide(wi, tmp_repo)

    assert tier == Tier.AUTO
    assert queue_id is None
    data = json.loads((tmp_repo / "state" / "hitl_queue.json").read_text())
    assert data["items"] == []  # no pending item was ever created


def test_decide_still_enqueues_without_a_standing_approval(tmp_repo):
    wi = WorkItem(
        skill="simplify", source="reflex", pillar="arts",
        blast_radius=BlastRadius.REPO, reversible=True,
        metric_id="metric:arts:Simplify_Age",
    )
    tier, queue_id = decide(wi, tmp_repo)
    assert tier == Tier.QUEUE
    assert queue_id is not None


def test_decide_never_consults_standing_approval_for_hitl_tier(tmp_repo):
    """Defense in depth: even if a standing entry somehow exists for a key whose
    CURRENT work item computes to HITL tier, decide() must not use it — the
    check is gated on tier == QUEUE, not on "a standing entry exists"."""
    hitl_key = ("reflex", "delete-stale-state", "arts", "", "")
    # Bypass grant_standing_approval's own tier gate to simulate an inconsistent
    # store (e.g. hand-edited file, or a future bug elsewhere) and prove decide()
    # is the second, independent gate — not the only one.
    path = tmp_repo / "state" / "hitl_standing_approvals.json"
    path.write_text(json.dumps({
        "schema_version": 1, "entries": [{
            "key": list(hitl_key), "granted_at": "x",
            "granted_via_queue_id": "hitl-x", "tier_at_grant": "hitl", "reason": "",
        }],
    }), encoding="utf-8")

    wi = WorkItem(
        skill="delete-stale-state", source="reflex", pillar="arts",
        blast_radius=BlastRadius.CONFINED, reversible=False,  # -> HITL tier
    )
    tier, queue_id = decide(wi, tmp_repo)

    assert tier == Tier.HITL
    assert queue_id is not None  # it was enqueued for real human review, not skipped


def test_decide_standing_approval_does_not_match_a_different_metric(tmp_repo):
    grant_standing_approval(_QKEY, tmp_repo, "hitl-prior", tier_at_grant="queue")
    wi_other = WorkItem(
        skill="simplify", source="reflex", pillar="arts",
        blast_radius=BlastRadius.REPO, reversible=True,
        metric_id="metric:arts:SomethingElse",  # different key component
    )
    tier, queue_id = decide(wi_other, tmp_repo)
    assert tier == Tier.QUEUE
    assert queue_id is not None


def test_decide_revoked_standing_approval_no_longer_skips_the_queue(tmp_repo):
    wi = WorkItem(
        skill="simplify", source="reflex", pillar="arts",
        blast_radius=BlastRadius.REPO, reversible=True,
        metric_id="metric:arts:Simplify_Age",
    )
    grant_standing_approval(_QKEY, tmp_repo, "hitl-prior", tier_at_grant="queue")
    assert decide(wi, tmp_repo)[0] == Tier.AUTO

    revoke_standing_approval(_QKEY, tmp_repo)
    tier, queue_id = decide(wi, tmp_repo)
    assert tier == Tier.QUEUE
    assert queue_id is not None
