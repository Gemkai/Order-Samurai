"""The write lock: what it guarantees, and the loss it exists to prevent.

atomic_json_write makes each write indivisible — that stops a TORN READ. It does nothing about a
LOST UPDATE, and for state/hitl_queue.json a lost update is a human's pending approval quietly
disappearing. These tests hold the difference in place.
"""
import json
import multiprocessing
import os
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agentica_core.atomic import atomic_json_write, file_write_lock  # noqa: E402


def _append_item(args):
    """One load-mutate-write cycle, with a gap wide enough to lose the other writer's change."""
    path, name, locked = Path(args[0]), args[1], args[2]

    def body():
        data = json.loads(path.read_text(encoding="utf-8"))
        time.sleep(0.2)  # the window an unlocked writer leaves open
        data["items"].append(name)
        atomic_json_write(path, data)

    if locked:
        with file_write_lock(path, timeout=30):
            body()
    else:
        body()


def _run_concurrently(path, locked):
    # "spawn", not "fork". Python 3.14 warns that forking a multi-threaded parent may
    # deadlock the child (DeprecationWarning, emitted by this very test), and pytest's
    # own machinery makes the parent multi-threaded. spawn re-imports the module in the
    # child instead of copying a threaded address space; both targets here are
    # module-level with picklable args, which is all spawn requires.
    # (2026-08-16 audit, Phase 5.4.)
    ctx = multiprocessing.get_context("spawn")
    args = [(str(path), "a", locked), (str(path), "b", locked)]
    with ctx.Pool(2) as pool:
        pool.map(_append_item, args)
    return json.loads(path.read_text(encoding="utf-8"))["items"]


@pytest.fixture
def queue(tmp_path):
    p = tmp_path / "hitl_queue.json"
    atomic_json_write(p, {"items": []})
    return p


def test_unlocked_concurrent_writers_lose_an_update(queue):
    """The bug being fixed — asserted, so the lock's value is demonstrated, not assumed."""
    assert len(_run_concurrently(queue, locked=False)) == 1


def test_locked_concurrent_writers_keep_both_updates(queue):
    assert sorted(_run_concurrently(queue, locked=True)) == ["a", "b"]


def test_lock_uses_a_sidecar_not_the_data_file(queue):
    """Load-bearing: atomic_json_write renames a new inode over the destination, so a lock held
    on the data file would sit on an inode the next writer never opens."""
    with file_write_lock(queue):
        assert Path(str(queue) + ".lock").exists()


def test_lock_is_released_when_the_body_raises(queue):
    with pytest.raises(ValueError):
        with file_write_lock(queue):
            raise ValueError("boom")
    with file_write_lock(queue, timeout=1):  # would raise TimeoutError if still held
        pass


def test_lock_times_out_rather_than_proceeding_unlocked(queue):
    """Proceeding unlocked would silently reintroduce the lost update."""
    ctx = multiprocessing.get_context("spawn")
    holder = ctx.Process(target=_hold, args=(str(queue), 3))
    holder.start()
    time.sleep(0.5)
    try:
        with pytest.raises(TimeoutError):
            with file_write_lock(queue, timeout=0.5):
                pass
    finally:
        holder.join()


def _hold(path, seconds):
    with file_write_lock(Path(path), timeout=30):
        time.sleep(seconds)


def test_lock_works_on_a_path_that_does_not_exist_yet(tmp_path):
    """First run: the queue is created inside the lock, so bootstrap is serialised too."""
    missing = tmp_path / "nested" / "hitl_queue.json"
    with file_write_lock(missing):
        atomic_json_write(missing, {"items": []})
    assert missing.exists()


def test_atomic_write_tmp_path_is_unique_per_process(tmp_path):
    """A fixed `<name>.tmp` is a shared mutable file: concurrent writers race on it, and the
    loser either raises FileNotFoundError or publishes the winner's half-written bytes."""
    target = tmp_path / "x.json"
    atomic_json_write(target, {"a": 1})
    assert not list(tmp_path.glob("*.tmp")), "tmp must not survive a successful write"
    assert str(os.getpid()) in f"{target}.{os.getpid()}.tmp"
