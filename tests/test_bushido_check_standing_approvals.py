"""CLI tests for --approve-always / --list-standing / --revoke-standing (ADOPT-003)."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_BUSHIDO_CHECK = _REPO / "bin" / "bushido_check.py"
_spec = importlib.util.spec_from_file_location("bushido_check_std", _BUSHIDO_CHECK)
bushido_check = importlib.util.module_from_spec(_spec)
sys.modules["bushido_check_std"] = bushido_check
_spec.loader.exec_module(bushido_check)


@pytest.fixture(autouse=True)
def _pull_only_semantics(monkeypatch):
    monkeypatch.setenv("BUSHIDO_PUSH_ON_APPROVE", "false")


@pytest.fixture()
def tmp_repo(tmp_path, monkeypatch):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "hitl_queue.json").write_text(
        json.dumps({"schema_version": 1, "items": [], "created_at": "x", "updated_at": "x"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(bushido_check, "REPO_ROOT", tmp_path)
    return tmp_path


def _seed_pending(tmp_repo, queue_id="hitl-abc123", tier_assigned="queue", **overrides):
    from agentica_core.bushido_engine import BlastRadius, Tier, WorkItem, enqueue_hitl
    wi = WorkItem(
        skill=overrides.pop("skill", "simplify"), source="reflex", pillar="arts",
        blast_radius=overrides.pop("blast_radius", BlastRadius.REPO),
        reversible=overrides.pop("reversible", True),
        metric_id=overrides.pop("metric_id", "metric:arts:Simplify_Age"),
    )
    tier = Tier.QUEUE if tier_assigned == "queue" else Tier.HITL
    real_id = enqueue_hitl(wi, tier, tmp_repo)
    return real_id


def test_approve_always_cli_grants_standing_approval(tmp_repo, capsys):
    qid = _seed_pending(tmp_repo)
    rc = bushido_check.main(["--approve-always", qid])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out == {"reviewed": True, "queue_id": qid, "action": "approve_always"}

    rc2 = bushido_check.main(["--list-standing"])
    listed = json.loads(capsys.readouterr().out)
    assert rc2 == 0
    assert len(listed["standing_approvals"]) == 1
    assert listed["standing_approvals"][0]["granted_via_queue_id"] == qid


def test_approve_always_and_reject_are_mutually_exclusive(tmp_repo, capsys):
    qid = _seed_pending(tmp_repo)
    with pytest.raises(SystemExit):
        bushido_check.main(["--approve-always", qid, "--reject", qid, "--reason", "x"])


def test_revoke_standing_cli_removes_the_grant(tmp_repo, capsys):
    qid = _seed_pending(tmp_repo)
    bushido_check.main(["--approve-always", qid])
    capsys.readouterr()

    rc = bushido_check.main(["--revoke-standing", qid])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out == {"revoked": True, "queue_id": qid}

    rc2 = bushido_check.main(["--list-standing"])
    listed = json.loads(capsys.readouterr().out)
    assert listed["standing_approvals"] == []


def test_revoke_standing_unknown_queue_id_fails_cleanly(tmp_repo, capsys):
    rc = bushido_check.main(["--revoke-standing", "hitl-does-not-exist"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["revoked"] is False


def test_list_standing_empty_returns_empty_list(tmp_repo, capsys):
    rc = bushido_check.main(["--list-standing"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out == {"standing_approvals": []}


def test_approve_always_on_hitl_tier_item_grants_nothing(tmp_repo, capsys):
    qid = _seed_pending(
        tmp_repo, tier_assigned="hitl", reversible=False,
    )
    # tier_assigned is derived from the WorkItem by enqueue_hitl's caller (decide()/tests
    # call it directly here); make sure this item is truly hitl-tier by construction.
    data = json.loads((tmp_repo / "state" / "hitl_queue.json").read_text())
    assert data["items"][0]["tier_assigned"] == "hitl"

    rc = bushido_check.main(["--approve-always", qid])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["reviewed"] is True  # the one-off approve still lands

    rc2 = bushido_check.main(["--list-standing"])
    listed = json.loads(capsys.readouterr().out)
    assert listed["standing_approvals"] == []
