"""Tests for the chained HITL review ledger (2026-08-18).

Traced from a live incident: 10 queue resolutions landed correctly through `review_hitl`,
but the only record of them was `state/autonomic_events.jsonl` — a plain append with no
seq/prev/entry hash, shared with five other writers. Raises were tamper-evident (the
factory's chained ledger); resolutions were not, and *approve* is the direction that grants
authority. These tests pin the two guarantees added in response:

  1. every resolution appends a verifiable chained row, and
  2. a resolution that cannot be chained does not happen at all (fail-closed), with the
     queue left untouched — i.e. the audit write now precedes the state write.

Each test was verified to fail against the pre-fix code (state written first, audit
best-effort through a swallow-everything emitter).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "Governance"))

from agentica_core.bushido_engine import (  # noqa: E402
    review_hitl,
    review_ledger_path,
)

# The chain verifier is a sibling module since plan M2.2, not a by-path load of
# Execution/factory/ledger.py. That path does not exist in the public export, so this
# helper was itself part of what made the exported suite red — the tests could not
# check the chain they had just written.
from agentica_core import chain_ledger  # noqa: E402


def _queue_item(qid: str = "hitl-t1", **over) -> dict:
    item = {
        "id": qid,
        "status": "pending",
        "skill": "dispatcher",
        "source": "factory",
        "pillar": "bow",
        "metric_id": None,
        "backlog_id": "demo:t1",
        "blast_radius": "repo",
        "reversible": True,
        "command": "dispatcher.py --once (task demo:t1)",
    }
    item.update(over)
    return item


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "state").mkdir(parents=True)
    (tmp_path / "state" / "hitl_queue.json").write_text(
        json.dumps({"items": [_queue_item()]}), encoding="utf-8",
    )
    return tmp_path


def _status(repo: Path, qid: str = "hitl-t1") -> str:
    data = json.loads((repo / "state" / "hitl_queue.json").read_text(encoding="utf-8"))
    return next(i["status"] for i in data["items"] if i["id"] == qid)


# ── 1. every resolution is chained ────────────────────────────────────────────

@pytest.mark.parametrize("action,expected", [
    ("approve", "approved"),
    ("reject", "rejected"),
    ("expire", "expired"),
])
def test_every_resolution_appends_a_verifiable_chained_row(repo, action, expected):
    assert review_hitl("hitl-t1", repo, action, reason="because") is True

    rows = [json.loads(x) for x in
            review_ledger_path(repo).read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(rows) == 1
    row = rows[0]
    assert row["event"] == "hitl_review"
    assert row["action"] == action
    assert row["queue_id"] == "hitl-t1"
    assert row["new_status"] == expected
    assert row["reason"] == "because"
    # The point of the row: it is chained, not merely written.
    assert {"seq", "prev_hash", "entry_hash"} <= row.keys()
    assert chain_ledger.verify_chain(review_ledger_path(repo))["ok"] is True


def test_successive_resolutions_extend_one_verifiable_chain(repo):
    data = json.loads((repo / "state" / "hitl_queue.json").read_text(encoding="utf-8"))
    data["items"].append(_queue_item("hitl-t2"))
    data["items"].append(_queue_item("hitl-t3"))
    (repo / "state" / "hitl_queue.json").write_text(json.dumps(data), encoding="utf-8")

    assert review_hitl("hitl-t1", repo, "reject", reason="stale") is True
    assert review_hitl("hitl-t2", repo, "approve") is True
    assert review_hitl("hitl-t3", repo, "expire", reason="self-resolved") is True

    result = chain_ledger.verify_chain(review_ledger_path(repo))
    assert result["ok"] is True
    assert result["chained"] == 3
    assert result["unchained"] == 0

    rows = [json.loads(x) for x in
            review_ledger_path(repo).read_text(encoding="utf-8").splitlines() if x.strip()]
    assert [r["seq"] for r in rows] == [1, 2, 3]
    assert rows[1]["prev_hash"] == rows[0]["entry_hash"]
    assert rows[2]["prev_hash"] == rows[1]["entry_hash"]


def test_tampering_with_a_chained_row_is_detectable(repo):
    assert review_hitl("hitl-t1", repo, "reject", reason="stale") is True
    path = review_ledger_path(repo)

    row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    row["action"] = "approve"          # rewrite history: a rejection becomes an approval
    row["new_status"] = "approved"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    assert chain_ledger.verify_chain(path)["ok"] is False


# ── 2. fail-closed: audit before state ────────────────────────────────────────

def test_unchainable_resolution_does_not_change_queue_state(repo, monkeypatch):
    """The write-order guarantee. Pre-fix this returned True and settled the item with no
    durable record; the audit ran afterwards and swallowed its own failure."""
    import agentica_core.bushido_engine as be

    monkeypatch.setattr(be, "_chain_review", lambda *_a, **_k: False)

    assert review_hitl("hitl-t1", repo, "approve") is False
    assert _status(repo) == "pending"
    assert not review_ledger_path(repo).exists()


def test_ledger_write_failure_fails_closed_rather_than_raising(repo, monkeypatch):
    """A broken ledger must block the resolution, not crash the caller: `bushido_check.py`
    and `review_pending_patch.py` both treat a False return as 'not reviewed'."""
    import agentica_core.bushido_engine as be

    class _Boom:
        def append(self, *_a, **_k):
            raise OSError("read-only file system")

    # The chain module is a sibling import since plan M2.2, not a by-path load, so the
    # failure is injected on its append rather than on a loader function.
    monkeypatch.setattr(be.chain_ledger, "append", _Boom().append)

    assert review_hitl("hitl-t1", repo, "reject", reason="stale") is False
    assert _status(repo) == "pending"


def test_already_settled_item_is_not_rechained(repo):
    """`review_hitl` refuses to re-decide a settled item; that refusal must not leave a
    misleading second attestation behind."""
    assert review_hitl("hitl-t1", repo, "reject", reason="stale") is True
    assert review_hitl("hitl-t1", repo, "approve") is False

    rows = [x for x in review_ledger_path(repo).read_text(encoding="utf-8").splitlines()
            if x.strip()]
    assert len(rows) == 1


# ── 3. isolation from the factory ledger ──────────────────────────────────────

def test_review_ledger_is_scoped_to_its_repo(repo):
    assert review_hitl("hitl-t1", repo, "reject", reason="stale") is True

    assert review_ledger_path(repo) == repo / "state" / "review_ledger.jsonl"
    assert (repo / "state" / ".review_ledger_head.json").exists()


def test_review_ledger_never_touches_the_factory_ledgers_own_head_cache(repo):
    """A review must never write the FACTORY ledger's head cache.

    History: append() used to default head_cache_path to
    `Execution/factory/state/.ledger_head.json` — a fixed path OUTSIDE repo_root — so an
    omitted kwarg sent every review's head into the factory's own artifact. Proven by
    hand at the time: dropping the explicit kwarg and running this test against the LIVE
    machine overwrote the real file (mtime and content both changed), recoverable only
    because append() never trusts the cache for correctness.

    Since plan M2.2 the guarantee is structural rather than argued. The chain primitive
    in agentica_core.chain_ledger takes log_path and head_cache_path as REQUIRED
    positional parameters — it has no defaults to fall back to and no knowledge of the
    factory's layout — so there is no longer a default for a caller to omit into. This
    test now pins that shape, which is the thing that makes the old hazard unreachable."""
    import inspect

    from agentica_core import chain_ledger

    params = inspect.signature(chain_ledger.append).parameters
    for name in ("log_path", "head_cache_path"):
        self_param = params[name]
        assert self_param.default is inspect.Parameter.empty, (
            f"chain_ledger.append.{name} has a default again — a caller that omits it "
            f"now writes to someone else's ledger, which is the exact regression this "
            f"test exists for"
        )

    # And the review still chains into its OWN head cache, next to its own log.
    assert review_hitl("hitl-t1", repo, "reject", reason="stale") is True
    assert (repo / "state" / ".review_ledger_head.json").exists()
    assert not (repo / "state" / ".ledger_head.json").exists()
