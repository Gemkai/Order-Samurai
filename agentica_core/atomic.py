"""Atomic JSON write — write-to-tmp + Windows-safe replace, plus the lock that makes
read-modify-write safe across processes.

Mirrors the helper used by Order Samurai's scouts (kill_chain_discovery_scout.py).
Used by aggregate.write_payload to prevent torn reads of wid_payload.json while
the TS reflex-engine is watching it (H1, governance opt-in grant hardening).

`atomic_json_write` alone is NOT enough for a file several processes update. It makes each
write indivisible, which prevents a torn READ; it does nothing about a lost UPDATE. Two
processes that both load, mutate and write back will silently keep only the second one's
change. For state/hitl_queue.json that lost change is a human's pending approval, so every
load-mutate-write of that file runs inside `file_write_lock` (see its docstring).
"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

try:  # POSIX only. Mac is the live host; see the degradation note in file_write_lock.
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]


LOCK_TIMEOUT_S = 10.0
_LOCK_POLL_S = 0.05


@contextmanager
def file_write_lock(path: Path, timeout: float = LOCK_TIMEOUT_S) -> Generator[None]:
    """Hold an exclusive advisory lock for one load-mutate-write of `path`.

    Wrap the WHOLE cycle, not just the write — locking only the write still lets two processes
    read the same bytes and clobber each other:

        with file_write_lock(queue_path):
            data = json.loads(queue_path.read_text())
            data["items"].append(item)
            atomic_json_write(queue_path, data)

    The lock lives on a `<name>.lock` SIDECAR rather than on the data file, and that is
    load-bearing: `atomic_json_write` renames a fresh inode over the destination, so a lock held
    on the data file would be attached to an inode the next process no longer opens. Every holder
    must agree on the same sidecar, which is why this helper takes the data path and derives the
    lock path itself instead of accepting one.

    Advisory, so it binds only participants. It is not a defence against a process that ignores
    it — it is the agreement between the writers that do.

    Raises TimeoutError rather than proceeding unlocked: a caller that silently continued would
    reintroduce exactly the lost update this exists to prevent. On Windows (no fcntl) it degrades
    to a no-op — parity is out of scope while Mac is the live host, and a hard failure there
    would break the callers for no safety gain.
    """
    if fcntl is None:  # pragma: no cover - Windows
        yield
        return

    lock_path = Path(str(path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # "a" never truncates, so a concurrent holder's fd stays valid.
    handle = open(lock_path, "a")
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"could not lock {lock_path} within {timeout}s — another writer is "
                        "holding it, or a stale holder needs investigating"
                    )
                time.sleep(_LOCK_POLL_S)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def atomic_json_write(path: Path, data: Any) -> None:
    # Unique per process: a fixed `<name>.tmp` is a shared mutable file, so two concurrent
    # writers race on it — the loser's replace() raises FileNotFoundError once the winner has
    # renamed it away, and the worse interleaving publishes one writer's half-written bytes under
    # the other's rename. Same directory, so replace() stays a same-filesystem atomic rename.
    tmp = Path(f"{path}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        # os.replace (Path.replace) atomically replaces an existing destination on
        # POSIX and Windows. Unlinking first would open a window where `path` does
        # not exist — the exact torn/missing read this helper prevents.
        tmp.replace(path)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise
