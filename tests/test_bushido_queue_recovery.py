"""The HITL queue is the only on-disk record that a human approved something.

enqueue_hitl() rewrites that file in full. These tests pin that a queue which is
present-but-unreadable is never silently replaced: an unreadable file means the
read failed, not that there were no approvals.
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
    Tier,
    WorkItem,
    enqueue_hitl,
)

# A queue holding one human-approved item, truncated mid-write — exactly what the
# non-atomic Windows fallback in _atomic_write_json leaves behind if it is cut short.
TRUNCATED_QUEUE = (
    '{"schema_version": 1, "items": [{"id": "hitl-7f3a91c2", "status": "approved", '
    '"skill": "wiki", "source": "reflex", "pillar": "arts", '
    '"metric_id": "metric:arts:Wiki_Health_Score", "approved_at": "2026-07-20T09:00:00+00:00"'
)


@pytest.fixture
def repo_with_unreadable_queue(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "hitl_queue.json").write_text(TRUNCATED_QUEUE, encoding="utf-8")
    return tmp_path


def _new_item() -> WorkItem:
    return WorkItem(skill="simplify", source="reflex", pillar="arts",
                    metric_id="metric:arts:Simplify_Age")


def test_unreadable_queue_is_preserved_not_destroyed(repo_with_unreadable_queue):
    """The approval record must survive somewhere on disk after an enqueue."""
    state_dir = repo_with_unreadable_queue / "state"

    enqueue_hitl(_new_item(), Tier.QUEUE, repo_with_unreadable_queue)

    survivors = [
        p for p in state_dir.glob("hitl_queue*")
        if TRUNCATED_QUEUE in p.read_text(encoding="utf-8", errors="ignore")
    ]
    assert survivors, (
        "the unreadable queue was overwritten in place — the approved item "
        "hitl-7f3a91c2 is now unrecoverable"
    )


def test_enqueue_still_succeeds_on_an_unreadable_queue(repo_with_unreadable_queue):
    """Preserving the old file must not block the gate: the new item still enqueues."""
    qid = enqueue_hitl(_new_item(), Tier.QUEUE, repo_with_unreadable_queue)
    assert qid.startswith("hitl-")

    data = json.loads(
        (repo_with_unreadable_queue / "state" / "hitl_queue.json").read_text(encoding="utf-8")
    )
    assert [i["id"] for i in data["items"]] == [qid]


def test_absent_queue_still_bootstraps_without_a_quarantine_file(tmp_path):
    """A missing queue is not a failed read — first-run bootstrap is unchanged."""
    (tmp_path / "state").mkdir()

    qid = enqueue_hitl(_new_item(), Tier.QUEUE, tmp_path)

    data = json.loads((tmp_path / "state" / "hitl_queue.json").read_text(encoding="utf-8"))
    assert [i["id"] for i in data["items"]] == [qid]
    # The guarded artifact is the QUARANTINE copy — a bootstrap that moved a "corrupt" queue
    # aside would mean the absent-file path had been misread as a failed read. Asserted by name
    # rather than by "nothing else exists": the write lock's .lock sidecar is a legitimate
    # neighbour, and a glob broad enough to catch it fails for a reason unrelated to recovery.
    assert not list((tmp_path / "state").glob("hitl_queue.corrupt*")), \
        "bootstrap must not leave a quarantine artifact behind"
    assert sorted(p.name for p in (tmp_path / "state").glob("hitl_queue*")) == [
        "hitl_queue.json", "hitl_queue.json.lock",
    ]
