"""Hash-chained append-only log: the chain format itself, and nothing else.

Extracted from `Execution/factory/ledger.py` (2026-09-02, plan M2.2, decision D2) so
that ONE implementation of the chain format serves both writers. Two copies of a hash
chain is two chains that can drift into mutual unverifiability, which is the failure
this module exists to prevent — not a style preference.

Who writes through here:

  * `Execution/factory/ledger.py` — the factory's own dispatch/spend/eligibility log.
    It keeps its default paths and its event-vocabulary writers and delegates the
    chaining to this module.
  * `agentica_core/bushido_engine.py` — the HITL review ledger
    (`state/review_ledger.jsonl`), the attestation that a human settled a queue item.

Why this module exists at all: `bushido_engine` used to load the factory ledger by
FILE PATH (`parents[2] / "Execution" / "factory" / "ledger.py"`), which is absent in
the public export. Every exported approve/reject/expire therefore refused with
"review ledger append failed" — 52 failing tests, unnoticed for eight weeks because
the export gate was not in CI (audit 2026-09-01, finding B3(a)). A normal sibling
import cannot go missing that way.

Deliberately NOT here: the factory's event vocabulary (`log_dispatch`, `log_spawn`,
`spend_since`, `live_runs`, `attempt_pairs`, ...). That is orchestration IP with
nothing to do with the chain format, and decision D2 is that it does not ship.

`log_path` and `head_cache_path` are REQUIRED here. The factory's module-level
defaults are the factory's business; a shared primitive that silently defaults to one
caller's log is how a review row ends up in the dispatch chain.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Iterator

GENESIS_HASH = "0" * 64
_CHAIN_KEYS = ("seq", "prev_hash", "entry_hash")

_LOCK_STALE_AFTER_S = 900.0  # matches with_repo_lock.sh's stale-lock reclaim window
_LOCK_POLL_S = 0.05


class LockTimeoutError(RuntimeError):
    """Raised when a ledger lock cannot be acquired within the caller's timeout."""


# --------------------------------------------------------------------------------------
# Canonical hashing (row shape ported from Governance/api/src/hash-chain.ts)
# --------------------------------------------------------------------------------------


def canonicalize(value: Any) -> str:
    """Deterministic JSON text: object keys sorted, no whitespace. Two writers hashing
    the same logical payload must produce the same bytes regardless of dict insertion
    order."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return json.dumps(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonicalize(v) for v in value) + "]"
    if isinstance(value, dict):
        parts = [
            json.dumps(str(k)) + ":" + canonicalize(v)
            for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))
        ]
        return "{" + ",".join(parts) + "}"
    raise TypeError(f"canonicalize: unsupported type {type(value).__name__}")


def compute_entry_hash(seq: int, prev_hash: str, payload: dict) -> str:
    """sha256(seq \\n prev_hash \\n canonical(payload without chain keys))."""
    stripped = {k: v for k, v in payload.items() if k not in _CHAIN_KEYS}
    material = f"{seq}\n{prev_hash}\n{canonicalize(stripped)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------------------
# Locking — in-process mutex, same stale-reclaim shape as with_repo_lock.sh
# --------------------------------------------------------------------------------------


def _lock_path(log_path: Path) -> Path:
    return log_path.with_name(log_path.name + ".lock")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not ours to signal
    return True


def _try_reclaim_stale_lock(lock_path: Path) -> bool:
    """True when the lock is gone as a result — i.e. it is worth trying the open again."""
    try:
        age = time.time() - lock_path.stat().st_mtime
    except FileNotFoundError:
        return True
    if age <= _LOCK_STALE_AFTER_S:
        return False
    try:
        pid_txt = lock_path.read_text(encoding="utf-8").strip()
        pid = int(pid_txt) if pid_txt else None
    except (OSError, ValueError):
        pid = None
    if pid is not None and _pid_alive(pid):
        return False  # genuinely still held, not stale
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass
    return True


def _acquire_lock(log_path: Path, timeout: float) -> Path:
    lock_path = _lock_path(log_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, str(os.getpid()).encode("utf-8"))
            finally:
                os.close(fd)
            return lock_path
        except FileExistsError:
            if _try_reclaim_stale_lock(lock_path):
                # The holder is provably gone, so retry the open BEFORE consulting the
                # deadline. Without this, timeout=0 reclaims the stale lock and then refuses
                # anyway — the caller that did the cleanup is the one punished for it, and
                # every non-blocking caller loses a turn to a dead process.
                continue
            if time.monotonic() >= deadline:
                raise LockTimeoutError(
                    f"could not acquire ledger lock at {lock_path} within {timeout}s"
                )
            time.sleep(_LOCK_POLL_S)


def _release_lock(lock_path: Path) -> None:
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


@contextmanager
def held_lock(base_path: Path, timeout: float) -> Generator[None, None, None]:
    """Hold `<base_path>.lock` for the body, or raise LockTimeoutError.

    Public because `append`'s few-millisecond critical section is not the only one that needs
    it: the dispatcher holds a lock across a whole pass, where read-then-decide and
    write-ahead are separated by a 6-9s composition call (DX-5). Routing both through here
    means a caller cannot invent a second stale-reclaim policy that disagrees with this one —
    the dispatcher previously reached into `_acquire_lock`/`_release_lock` directly, which is
    exactly how two policies drift apart.

    `timeout=0` means try once and refuse, which is the right answer for a periodic caller.
    """
    lock_path = _acquire_lock(base_path, timeout)
    try:
        yield
    finally:
        _release_lock(lock_path)


# Retained so the module's own append path reads unchanged; `held_lock` is the public name.
_held_lock = held_lock


# --------------------------------------------------------------------------------------
# Append / verify
# --------------------------------------------------------------------------------------


def _read_last_row(log_path: Path) -> dict | None:
    """Last *parseable* JSON line in the log, or None if empty/missing. Corrupt trailing
    lines are skipped here (this is the append-side fast path, not the audit); a tampered
    or truncated row is still caught by verify_chain, which does not skip anything."""
    if not log_path.exists():
        return None
    last: dict | None = None
    with log_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                last = json.loads(line)
            except json.JSONDecodeError:
                continue
    return last


def _append_line(log_path: Path, row: dict) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(str(log_path), os.O_APPEND | os.O_CREAT | os.O_WRONLY)
    try:
        os.write(fd, line)  # single write, O_APPEND — atomic on POSIX at this row size
    finally:
        os.close(fd)


def _write_head_cache(head_path: Path, head: dict) -> None:
    """Best-effort convenience index for readers (doctor/debrief); never authoritative —
    append() re-derives the true head from the log tail every call, and verify_chain()
    ignores this file entirely."""
    try:
        head_path.parent.mkdir(parents=True, exist_ok=True)
        head_path.write_text(json.dumps(head), encoding="utf-8")
    except OSError:
        pass  # advisory only — a failed cache write must never fail the append


def append(
    record: dict,
    log_path: Path,
    head_cache_path: Path,
    lock_timeout: float = 30.0,
) -> dict:
    """Append `record` as the next chained row and return the written row (with
    seq/prev_hash/entry_hash filled in). `record` must not already carry chain keys.
    """
    if any(k in record for k in _CHAIN_KEYS):
        raise ValueError(f"record must not pre-set chain keys {_CHAIN_KEYS}")
    with _held_lock(log_path, lock_timeout):
        last = _read_last_row(log_path)
        if last is not None and all(k in last for k in _CHAIN_KEYS):
            seq = last["seq"] + 1
            prev_hash = last["entry_hash"]
        else:
            seq = 1
            prev_hash = GENESIS_HASH
        entry_hash = compute_entry_hash(seq, prev_hash, record)
        chained = dict(record)
        chained["seq"] = seq
        chained["prev_hash"] = prev_hash
        chained["entry_hash"] = entry_hash
        _append_line(log_path, chained)
        _write_head_cache(head_cache_path, {"seq": seq, "hash": entry_hash})
        return chained


def verify_chain(log_path: Path) -> dict:
    """Recompute every row's hash straight from the raw log (never the head cache).
    Returns {"ok", "chained", "unchained", "broken_at_seq", "reason"}. `unchained` counts
    rows missing one or more chain keys (there are none by construction — append() always
    fills them in — so a nonzero count itself is evidence of direct file tampering)."""
    if not log_path.exists():
        return {"ok": True, "chained": 0, "unchained": 0, "broken_at_seq": None, "reason": None}
    chained = 0
    unchained = 0
    expected_seq = 1
    expected_prev = GENESIS_HASH
    with log_path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                return {
                    "ok": False, "chained": chained, "unchained": unchained,
                    "broken_at_seq": expected_seq, "reason": f"unparseable JSON at line {lineno}",
                }
            if not all(k in row for k in _CHAIN_KEYS):
                unchained += 1
                continue
            if row["seq"] != expected_seq:
                return {
                    "ok": False, "chained": chained, "unchained": unchained,
                    "broken_at_seq": expected_seq,
                    "reason": f"seq mismatch: expected {expected_seq}, got {row.get('seq')!r}",
                }
            if row["prev_hash"] != expected_prev:
                return {
                    "ok": False, "chained": chained, "unchained": unchained,
                    "broken_at_seq": expected_seq, "reason": f"prev_hash mismatch at seq {expected_seq}",
                }
            recomputed = compute_entry_hash(row["seq"], row["prev_hash"], row)
            if recomputed != row["entry_hash"]:
                return {
                    "ok": False, "chained": chained, "unchained": unchained,
                    "broken_at_seq": expected_seq,
                    "reason": f"entry_hash mismatch at seq {expected_seq} (tamper detected)",
                }
            chained += 1
            expected_seq += 1
            expected_prev = row["entry_hash"]
    return {"ok": True, "chained": chained, "unchained": unchained, "broken_at_seq": None, "reason": None}


def iter_rows(log_path: Path) -> Iterator[dict]:
    """Runtime query iterator — resilient (skips unparseable lines) rather than an audit.
    Use verify_chain() when tamper-evidence matters."""
    if not log_path.exists():
        return
    with log_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


# --------------------------------------------------------------------------------------
# Event-vocabulary writers (D3)
# --------------------------------------------------------------------------------------


