"""`retire` — the human's decision on an item that already expired (coverage review R4.1).

Expiry used to be an end: an item that expired unreviewed left every surface and nothing
could be written on it afterwards, so "nobody looked" was indistinguishable from "decided".
`retire` is the one transition allowed FROM `expired`; it is chained like every other
resolution and is terminal. Each test here failed before the action existed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "Governance"))

from agentica_core import chain_ledger  # noqa: E402
from agentica_core.bushido_engine import (  # noqa: E402
    _REVIEWABLE_FROM,
    _REVIEW_ACTIONS,
    review_hitl,
    review_ledger_path,
)

_OS_BIN = Path(__file__).resolve().parents[1] / "bin"


def _item(qid: str, status: str, **over) -> dict:
    item = {
        "id": qid, "status": status, "skill": "pip-safe-upgrade", "source": "reflex",
        "pillar": "sword", "metric_id": "metric:sword:Deprecated_Deps", "backlog_id": None,
        "blast_radius": "repo", "reversible": True, "command": "/pip-safe-upgrade",
        "enqueued_at": "2026-06-21T23:59:46+00:00",
    }
    if status == "expired":
        item["expired_at"] = "2026-07-19T12:07:14+00:00"
        item["expired_reason"] = "stale"
    item.update(over)
    return item


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "hitl_queue.json").write_text(json.dumps({"items": [
        _item("hitl-exp", "expired"),
        _item("hitl-pen", "pending"),
        _item("hitl-done", "done"),
        _item("hitl-rej", "rejected"),
    ]}), encoding="utf-8")
    return tmp_path


def _row(repo: Path, qid: str) -> dict:
    data = json.loads((repo / "state" / "hitl_queue.json").read_text(encoding="utf-8"))
    return next(i for i in data["items"] if i["id"] == qid)


def test_retire_is_a_registered_action_valid_from_expired_and_pending():
    assert "retire" in _REVIEW_ACTIONS
    assert _REVIEWABLE_FROM["retire"] == {"pending", "expired"}
    # The pre-existing actions keep their pending-only contract.
    for action in ("approve", "approve_always", "reject", "expire"):
        assert _REVIEWABLE_FROM[action] == {"pending"}, action


def test_expired_item_can_be_retired_and_is_chained(repo):
    assert review_hitl("hitl-exp", repo, "retire", reason="seen 2026-09-02; CVE subset tracked elsewhere") is True
    row = _row(repo, "hitl-exp")
    assert row["status"] == "retired"
    assert row["retired_reason"].startswith("seen 2026-09-02")
    assert row["retired_at"]
    # The original expiry is preserved — retire records a decision, it does not rewrite history.
    assert row["expired_at"] == "2026-07-19T12:07:14+00:00"
    ledger = [json.loads(x) for x in review_ledger_path(repo).read_text(encoding="utf-8").splitlines() if x.strip()]
    assert [(r["action"], r["new_status"]) for r in ledger] == [("retire", "retired")]
    assert chain_ledger.verify_chain(review_ledger_path(repo))["ok"] is True


def test_pending_item_can_be_retired_without_a_push(repo, monkeypatch):
    import agentica_core.bushido_engine as be
    monkeypatch.setattr(be, "_post_manual_run", lambda *a, **k: pytest.fail("retire must never push"))
    assert review_hitl("hitl-pen", repo, "retire", reason="not worth a run") is True
    assert _row(repo, "hitl-pen")["status"] == "retired"


@pytest.mark.parametrize("qid", ["hitl-done", "hitl-rej"])
def test_settled_items_cannot_be_retired(repo, qid):
    before = _row(repo, qid)
    assert review_hitl(qid, repo, "retire", reason="x") is False
    assert _row(repo, qid) == before
    assert not review_ledger_path(repo).exists()


def test_retired_is_terminal(repo):
    assert review_hitl("hitl-exp", repo, "retire", reason="first") is True
    for action in ("retire", "approve", "reject", "expire"):
        assert review_hitl("hitl-exp", repo, action, reason="again") is False, action
    assert _row(repo, "hitl-exp")["retired_reason"] == "first"


@pytest.mark.parametrize("action", ["approve", "reject", "expire"])
def test_expired_item_still_refuses_the_pending_only_actions(repo, action):
    assert review_hitl("hitl-exp", repo, action, reason="x") is False
    assert _row(repo, "hitl-exp")["status"] == "expired"


# ── the CLI a human actually types (bin/bushido_check.py --retire) ────────────

def _cli(repo: Path, monkeypatch, argv: list[str]) -> int:
    import importlib.util
    spec = importlib.util.spec_from_file_location("bushido_check_under_test", _OS_BIN / "bushido_check.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "REPO_ROOT", repo)
    return mod.main(argv)


def test_cli_retire_records_the_decision_on_an_expired_item(repo, monkeypatch, capsys):
    assert _cli(repo, monkeypatch, ["--retire", "hitl-exp", "--reason", "seen"]) == 0
    assert _row(repo, "hitl-exp")["status"] == "retired"
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out == {"reviewed": True, "queue_id": "hitl-exp", "action": "retire"}


def test_cli_retire_works_on_a_pending_item_too(repo, monkeypatch):
    assert _cli(repo, monkeypatch, ["--retire", "hitl-pen", "--reason", "not worth a run"]) == 0
    assert _row(repo, "hitl-pen")["status"] == "retired"


def test_cli_refuses_a_retire_without_a_reason(repo, monkeypatch):
    with pytest.raises(SystemExit) as exc:
        _cli(repo, monkeypatch, ["--retire", "hitl-exp"])
    assert exc.value.code == 2
    assert _row(repo, "hitl-exp")["status"] == "expired"


def test_cli_retire_on_a_settled_item_exits_1(repo, monkeypatch, capsys):
    assert _cli(repo, monkeypatch, ["--retire", "hitl-done", "--reason", "x"]) == 1
    assert _row(repo, "hitl-done")["status"] == "done"
