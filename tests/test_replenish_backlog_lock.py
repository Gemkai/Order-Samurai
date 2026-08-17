"""PROPOSED_BACKLOG.json must survive two concurrent writers (2026-08-16 audit, P2).

replenish_backlog did an UNLOCKED read-modify-write (load_proposed -> append ->
write_text) of the same file Governance/bin/sensei_writeback.py writes under a
`PROPOSED_BACKLOG.json.lock` mkdir lock with an atomic replace. When the daily sensei
cycle escalated a stuck reflex while replenish was running its intake, whichever plain
write landed last silently discarded the other's items — an escalated stuck remediation
could vanish from the backlog with no error anywhere. The non-atomic write also let a
reader (bin/ronin status, hitl_alerts, the /goal night sweep, which treats this file as
its work queue) observe truncated JSON.

Run: python3 -m pytest tests/test_replenish_backlog_lock.py -q
"""
import importlib.util
import json
import multiprocessing
import os
import sys
import time
from pathlib import Path

import pytest

_MOD = Path(__file__).resolve().parents[1] / "bin" / "replenish_backlog.py"


def _load(state_dir):
    """Import replenish_backlog with its state dir redirected at a temp path."""
    os.environ["MEDITATION_STATE_DIR"] = str(state_dir)
    spec = importlib.util.spec_from_file_location("replenish_backlog_under_test", _MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def rb(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDITATION_STATE_DIR", str(tmp_path))
    mod = _load(tmp_path)
    (tmp_path / "PROPOSED_BACKLOG.json").write_text(
        json.dumps({"generated_at": "", "items": []}), encoding="utf-8")
    return mod


# ── the lock itself ──────────────────────────────────────────────────────────

def test_lock_sidecar_matches_the_other_writers_convention(rb):
    """Both holders must derive the SAME sidecar or neither excludes the other."""
    assert rb.BACKLOG_LOCK.name == rb.PROPOSED_BACKLOG.name + ".lock"
    assert rb.BACKLOG_LOCK.parent == rb.PROPOSED_BACKLOG.parent


def test_lock_is_a_directory_not_a_file(rb):
    """sensei_writeback uses os.mkdir. A flock-on-a-file would not exclude it."""
    with rb.backlog_lock():
        assert rb.BACKLOG_LOCK.is_dir()
    assert not rb.BACKLOG_LOCK.exists(), "lock must be released on exit"


def test_lock_is_released_even_when_the_body_raises(rb):
    with pytest.raises(ValueError):
        with rb.backlog_lock():
            raise ValueError("boom")
    assert not rb.BACKLOG_LOCK.exists()


def test_a_held_lock_blocks_a_second_holder_then_times_out(rb, monkeypatch):
    """Waiting past the deadline must RAISE, never proceed unlocked."""
    monkeypatch.setattr(rb, "LOCK_WAIT_S", 0.2)
    monkeypatch.setattr(rb, "LOCK_STALE_S", 300)
    with rb.backlog_lock():
        with pytest.raises(TimeoutError, match="rather than risk losing entries"):
            with rb.backlog_lock():
                pytest.fail("acquired a lock another holder was holding")


def test_a_stale_lock_is_reclaimed_rather_than_deadlocking(rb, monkeypatch):
    monkeypatch.setattr(rb, "LOCK_STALE_S", 0.05)
    monkeypatch.setattr(rb, "LOCK_WAIT_S", 5)
    os.mkdir(rb.BACKLOG_LOCK)               # simulate a holder that died mid-write
    time.sleep(0.1)
    with rb.backlog_lock():
        assert rb.BACKLOG_LOCK.is_dir()


# ── the write ────────────────────────────────────────────────────────────────

def test_write_is_atomic_and_leaves_no_temp_behind(rb):
    rb.write_proposed_atomic({"generated_at": "now", "items": [{"id": "A"}]})
    assert json.loads(rb.PROPOSED_BACKLOG.read_text())["items"] == [{"id": "A"}]
    leftovers = list(rb.PROPOSED_BACKLOG.parent.glob("PROPOSED_BACKLOG.json.*.tmp"))
    assert leftovers == [], f"temp files left behind: {leftovers}"


def test_temp_name_is_per_process_not_a_shared_fixed_name(rb):
    """A fixed `<name>.tmp` is a shared mutable file two writers race on."""
    import inspect
    src = inspect.getsource(rb.write_proposed_atomic)
    assert "getpid" in src, "temp name must be unique per process"


# ── the actual concurrency defect ────────────────────────────────────────────

def _writer(state_dir, tag, count, barrier):
    """Simulate the other holder: lock, read, append, atomic-write."""
    os.environ["MEDITATION_STATE_DIR"] = str(state_dir)
    spec = importlib.util.spec_from_file_location(f"rb_{tag}", _MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    barrier.wait()
    for i in range(count):
        with mod.backlog_lock():
            doc = mod.load_proposed()
            items = doc.get("items", [])
            items.append({"id": f"{tag}-{i}"})
            doc["items"] = items
            time.sleep(0.002)          # widen the read->write window
            mod.write_proposed_atomic(doc)


@pytest.mark.skipif(sys.platform == "win32", reason="fork-based test")
def test_two_concurrent_writers_lose_no_items(rb, tmp_path):
    """The real defect: an escalated item must not vanish because another writer
    read the same snapshot and wrote last."""
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    n = 12
    procs = [ctx.Process(target=_writer, args=(tmp_path, tag, n, barrier))
             for tag in ("sensei", "replenish")]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=90)
        assert p.exitcode == 0, f"writer failed: exitcode={p.exitcode}"

    items = json.loads(rb.PROPOSED_BACKLOG.read_text())["items"]
    ids = {i["id"] for i in items}
    expected = {f"{tag}-{i}" for tag in ("sensei", "replenish") for i in range(n)}
    assert ids == expected, (
        f"lost {len(expected - ids)} item(s) to a clobbering write: "
        f"{sorted(expected - ids)[:5]}"
    )
