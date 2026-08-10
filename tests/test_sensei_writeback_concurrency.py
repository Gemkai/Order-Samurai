"""Concurrency guard for the sensei backlog write-back.

os.replace makes each write atomic, but read-compute-id-write is not. Before the
backlog_lock, two overlapping runs both read the same snapshot, both derived the same
next SENSEI-<n>, and the second os.replace discarded the first run's entries -- silent
loss with a zero exit code.

launchd will not start a second copy of one job, so the exposed path is a MANUAL
sensei run overlapping the scheduled one.

These tests spawn real concurrent processes; they fail if the lock is removed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from _layout import governance_bin

# Marker-based in both supported layouts: Governance/bin in the live repository,
# and the product root's bin/ in the flat public export.
WRITEBACK = governance_bin(__file__) / "sensei_writeback.py"
assert WRITEBACK.is_file(), f"writeback script not found at {WRITEBACK}"
WRITERS = 8


@pytest.fixture
def osr(tmp_path: Path) -> Path:
    """A throwaway ORDER_SAMURAI_ROOT with the object-shaped backlog this system uses."""
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "PROPOSED_BACKLOG.json").write_text(
        json.dumps({"generated_at": "2026-01-01T00:00:00Z", "note": "seed", "items": []}),
        encoding="utf-8",
    )
    return tmp_path


def _payload(tag: str) -> str:
    # No post_verdicts -> do_post() is never called, so the test needs no ReflexEngine.
    return json.dumps(
        {
            "post_verdicts": [],
            "backlog_entries": [{"title": tag, "approved": False}],
            "ledger_rows": [],
        }
    )


def _run_payload(osr: Path, payload: str, **env_overrides: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(WRITEBACK)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=60,
        env={
            **os.environ,
            "SENSEI_ARM": "1",
            "ORDER_SAMURAI_ROOT": str(osr),
            **env_overrides,
        },
    )


def _run(osr: Path, tag: str) -> subprocess.CompletedProcess:
    return _run_payload(osr, _payload(tag))


def _items(osr: Path) -> list[dict]:
    doc = json.loads((osr / "state" / "PROPOSED_BACKLOG.json").read_text(encoding="utf-8"))
    return doc["items"]


def test_concurrent_writers_lose_no_entries(osr: Path) -> None:
    with ThreadPoolExecutor(max_workers=WRITERS) as pool:
        results = list(pool.map(lambda i: _run(osr, f"entry-{i}"), range(WRITERS)))

    for i, r in enumerate(results):
        assert r.returncode == 0, f"writer {i} exited {r.returncode}: {r.stderr}"

    items = _items(osr)
    assert len(items) == WRITERS, (
        f"expected {WRITERS} entries, found {len(items)} -- entries were lost to an "
        f"interleaved read-modify-write"
    )
    assert {i["title"] for i in items} == {f"entry-{i}" for i in range(WRITERS)}


def test_concurrent_writers_assign_unique_ids(osr: Path) -> None:
    with ThreadPoolExecutor(max_workers=WRITERS) as pool:
        list(pool.map(lambda i: _run(osr, f"entry-{i}"), range(WRITERS)))

    ids = [i["id"] for i in _items(osr)]
    # Assert the count too: uniqueness alone passes vacuously when the race has
    # already discarded entries, since a single survivor is trivially unique.
    assert len(ids) == WRITERS, f"entries lost before the id check: {ids}"
    assert len(ids) == len(set(ids)), f"duplicate SENSEI ids from concurrent runs: {ids}"


def test_preexisting_container_keys_survive(osr: Path) -> None:
    """The object shape carries generated_at/note that readers depend on."""
    with ThreadPoolExecutor(max_workers=WRITERS) as pool:
        list(pool.map(lambda i: _run(osr, f"entry-{i}"), range(WRITERS)))

    doc = json.loads((osr / "state" / "PROPOSED_BACKLOG.json").read_text(encoding="utf-8"))
    assert doc["generated_at"] == "2026-01-01T00:00:00Z"
    assert doc["note"] == "seed"


def test_stale_lock_is_reclaimed(osr: Path) -> None:
    """A holder that died mid-write must not deadlock the mechanism forever."""
    lock = osr / "state" / "PROPOSED_BACKLOG.json.lock"
    lock.mkdir()
    os.utime(lock, (0, 0))  # epoch mtime -> far older than LOCK_STALE_S

    r = _run(osr, "after-stale")
    assert r.returncode == 0, r.stderr
    assert [i["title"] for i in _items(osr)] == ["after-stale"]


def test_wait_timeout_raises_rather_than_writing_unlocked(osr: Path) -> None:
    """A live lock must block the write, not be silently bypassed."""
    lock = osr / "state" / "PROPOSED_BACKLOG.json.lock"
    lock.mkdir()  # fresh mtime -> live holder, never stale

    r = subprocess.run(
        [sys.executable, str(WRITEBACK)],
        input=_payload("must-not-land"),
        capture_output=True,
        text=True,
        timeout=60,
        env={
            **os.environ,
            "SENSEI_ARM": "1",
            "ORDER_SAMURAI_ROOT": str(osr),
            "SENSEI_LOCK_WAIT_S": "0.3",
        },
    )
    assert r.returncode != 0, r.stdout
    assert "must-not-land" not in json.dumps(_items(osr))
    assert "backlog: FAILED" in r.stdout, r.stdout


def test_armed_empty_payload_exits_nonzero(osr: Path) -> None:
    """`{}` is what sensei_cycle_live.sh substitutes when the claude call fails;
    an armed run fed it used to exit 0 — a total write-back failure reported as
    a successful cycle."""
    r = _run_payload(osr, "{}")

    assert r.returncode != 0, r.stdout
    assert "empty" in r.stdout.lower()
    assert _items(osr) == []  # nothing was invented to write


def test_dry_empty_payload_stays_informational_exit_zero(osr: Path) -> None:
    """Only ARMED escalates: the dry path prints what it would do and exits 0."""
    r = _run_payload(osr, "{}", SENSEI_ARM="0")

    assert r.returncode == 0, r.stdout
    assert "DRY" in r.stdout


def test_required_post_failure_exits_nonzero(osr: Path) -> None:
    payload = json.dumps({
        "post_verdicts": [{"reflex_id": "metric:bow:Error_Rate", "verdict": "CONFIRMED"}],
        "backlog_entries": [],
        "ledger_rows": [],
    })

    r = _run_payload(osr, payload, REFLEX_API="not-a-valid-url")

    assert r.returncode != 0, r.stdout
    assert "post   : FAILED" in r.stdout


def test_required_ledger_failure_exits_nonzero(osr: Path) -> None:
    (osr / "state" / "SENSEI_LEDGER.jsonl").mkdir()
    payload = json.dumps({
        "post_verdicts": [],
        "backlog_entries": [],
        "ledger_rows": [{"cycle_id": "cycle-1"}],
    })

    r = _run_payload(osr, payload)

    assert r.returncode != 0, r.stdout
    assert "ledger : FAILED" in r.stdout


def test_unresolved_sensei_reflex_is_not_duplicated(osr: Path) -> None:
    reflex_id = "metric:arts:Retrieval_Relevance"
    backlog_path = osr / "state" / "PROPOSED_BACKLOG.json"
    backlog_path.write_text(json.dumps({
        "generated_at": "2026-01-01T00:00:00Z",
        "note": "preserve",
        "items": [{
            "id": "SENSEI-41",
            "source": "sensei",
            "approved": False,
            "reflex_id": reflex_id,
            "reason": "already awaiting human review",
        }],
    }), encoding="utf-8")
    payload = json.dumps({
        "post_verdicts": [],
        "backlog_entries": [{
            "source": "sensei", "approved": False, "reflex_id": reflex_id,
            "reason": "same unresolved reflex, later cycle",
        }],
        "ledger_rows": [],
    })

    r = _run_payload(osr, payload)

    assert r.returncode == 0, r.stdout
    doc = json.loads(backlog_path.read_text(encoding="utf-8"))
    assert len([i for i in doc["items"] if i.get("reflex_id") == reflex_id]) == 1
    assert doc["generated_at"] == "2026-01-01T00:00:00Z"
    assert doc["note"] == "preserve"


def test_resolved_sensei_reflex_can_be_escalated_again(osr: Path) -> None:
    reflex_id = "metric:arts:Retrieval_Relevance"
    backlog_path = osr / "state" / "PROPOSED_BACKLOG.json"
    backlog_path.write_text(json.dumps({"items": [{
        "id": "SENSEI-41",
        "source": "sensei",
        "approved": False,
        "reflex_id": reflex_id,
        "reason": "prior incident",
        "status": "resolved",
    }]}), encoding="utf-8")
    payload = json.dumps({
        "post_verdicts": [],
        "backlog_entries": [{
            "source": "sensei", "approved": False, "reflex_id": reflex_id,
            "reason": "new incident",
        }],
        "ledger_rows": [],
    })

    r = _run_payload(osr, payload)

    assert r.returncode == 0, r.stdout
    entries = [i for i in _items(osr) if i.get("reflex_id") == reflex_id]
    assert len(entries) == 2
    assert entries[-1]["id"] == "SENSEI-42"
