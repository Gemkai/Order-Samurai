import json
from threading import Event, Thread

import pytest

from agentica_core import backfill_history as bh
from agentica_core import insights


def test_week_monday_iso():
    # ISO week 2026-W23 Monday = 2026-06-01
    assert bh._week_monday_iso("2026-W23").startswith("2026-06-01")
    assert bh._week_monday_iso("garbage") == "garbage"  # graceful fallback


def test_week_values_flattens_keys():
    recs = [{"timestamp": "2026-06-01T00:00:00+00:00", "project": "x", "model_tier": "FAST",
             "tokens_prompt": 100, "tokens_completion": 50, "tool_calls": 3,
             "status": "success", "session_id": "s1"}]
    vals = bh._week_values(recs)
    assert all("/" in k for k in vals)            # keys are pillar/group/metric
    assert all(isinstance(v, (int, float)) for v in vals.values())


def test_backfill_never_clobbers_history_when_telemetry_yields_nothing(tmp_path, monkeypatch):
    """load_records() returns [] for an unreachable platform or a moved telemetry
    file — a source failure, not evidence that history is empty. Rewriting the
    store from zero rows would wipe every sigma baseline, sparkline and prior-week
    comparison the reducers read back (and refresh_dashboard calls backfill() on
    every --snapshot run)."""
    store = tmp_path / "hist.jsonl"
    existing = ('{"ts": "2026-06-01T00:00:00+00:00", "week": "2026-W23", '
                '"values": {"bow/Activity/Session_Count": 4}}\n')
    store.write_text(existing, encoding="utf-8")

    monkeypatch.setattr(bh.agg, "list_platforms", lambda: [])

    assert bh.backfill(store=store) == 0
    assert store.read_text(encoding="utf-8") == existing


def test_backfill_idempotent(tmp_path):
    store = tmp_path / "hist.jsonl"
    n1 = bh.backfill(store=store)
    rows1 = store.read_text(encoding="utf-8").strip().splitlines()
    n2 = bh.backfill(store=store)
    rows2 = store.read_text(encoding="utf-8").strip().splitlines()
    assert n1 == n2 == len(rows1) == len(rows2)    # stable, no growth
    for ln in rows2:
        row = json.loads(ln)
        assert "week" in row and "values" in row   # weekly-canonical series


# ---------------------------------------------------------------------------
# FIX C — carry live-only metrics across the truncate-and-rebuild (seq 9c)
# ---------------------------------------------------------------------------
# _week_values() can only recompute what telemetry supports. Every live-only
# reducer (scouts, exec-log metrics, security signals) lived exclusively on the
# snapshot rows this rebuild truncates — so each run erased those series and left
# 24 metrics pinned to a single history point forever.

_LIVE_ONLY = "bow/Autonomic/Self_Correction_Rate"
_WEEKLY = "bow/Activity/Session_Count"


def _stub_two_weeks(monkeypatch):
    """Telemetry in ISO weeks 2026-W23 (Mon 06-01) and 2026-W24 (Mon 06-08)."""
    recs = [{"timestamp": "2026-06-02T00:00:00+00:00"},
            {"timestamp": "2026-06-09T00:00:00+00:00"}]
    monkeypatch.setattr(bh.agg, "list_platforms", lambda: ["claude"])
    monkeypatch.setattr(bh.agg, "load_records", lambda _p: recs)
    monkeypatch.setattr(bh, "_week_row", lambda w, wrecs: {
        "ts": bh._week_monday_iso(w), "week": w, "kind": "weekly",
        "values": {_WEEKLY: float(len(wrecs))}})


def _seed_live_row(store, ts: str, value: float) -> None:
    store.write_text(json.dumps({"ts": ts, "kind": "live",
                                 "values": {_LIVE_ONLY: value}}) + "\n", encoding="utf-8")


def _rows(store) -> list[dict]:
    return [json.loads(ln) for ln in store.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_backfill_carries_live_only_metric_onto_the_rebuilt_rows(tmp_path, monkeypatch):
    _stub_two_weeks(monkeypatch)
    store = tmp_path / "hist.jsonl"
    _seed_live_row(store, "2026-06-05T00:00:00+00:00", 2.2)

    assert bh.backfill(store=store) == 2
    rows = _rows(store)
    assert rows[1]["values"][_LIVE_ONLY] == 2.2          # week starting 06-08 (after the obs)
    assert rows[1]["carried_forward"] == [_LIVE_ONLY]


def test_backfill_does_not_back_date_a_value_onto_earlier_weeks(tmp_path, monkeypatch):
    _stub_two_weeks(monkeypatch)
    store = tmp_path / "hist.jsonl"
    _seed_live_row(store, "2026-06-05T00:00:00+00:00", 2.2)

    bh.backfill(store=store)
    rows = _rows(store)
    assert _LIVE_ONLY not in rows[0]["values"]           # week starting 06-01, before the obs
    assert "carried_forward" not in rows[0]


def test_two_consecutive_backfills_retain_the_live_only_key(tmp_path, monkeypatch):
    _stub_two_weeks(monkeypatch)
    store = tmp_path / "hist.jsonl"
    _seed_live_row(store, "2026-06-05T00:00:00+00:00", 2.2)

    bh.backfill(store=store)
    first = _rows(store)
    bh.backfill(store=store)
    second = _rows(store)

    assert first == second                               # idempotent, not eroding
    assert second[1]["values"][_LIVE_ONLY] == 2.2


# ---------------------------------------------------------------------------
# The prior snapshot's kind:"live" ROW (not just its values) must survive the
# rebuild -- insights._comparable_history_rows() diffs against `kind in (None,
# "live")` rows to compute delta/trend and feed the sigma-anomaly tier. Every
# --snapshot run calls backfill() BEFORE populate_history() (refresh_dashboard.py),
# so if backfill() drops the live row entirely, populate_history() always finds a
# 1-point series and delta/trend/sigma stay stuck at their neutral defaults forever.
# ---------------------------------------------------------------------------

def test_backfill_preserves_the_latest_live_row_across_the_rebuild(tmp_path, monkeypatch):
    _stub_two_weeks(monkeypatch)
    store = tmp_path / "hist.jsonl"
    store.write_text(json.dumps({"ts": "2026-06-10T00:00:00+00:00", "week": "2026-W24",
                                 "kind": "live", "values": {_WEEKLY: 42.0}}) + "\n",
                     encoding="utf-8")

    bh.backfill(store=store)
    rows = _rows(store)
    live = [r for r in rows if r.get("kind") == "live"]
    assert len(live) == 1                                # max_live_rows=1 invariant
    assert live[0]["values"][_WEEKLY] == 42.0


def test_recomputed_weekly_value_wins_over_a_carried_one(tmp_path, monkeypatch):
    _stub_two_weeks(monkeypatch)
    store = tmp_path / "hist.jsonl"
    store.write_text(json.dumps({"ts": "2026-06-05T00:00:00+00:00",
                                 "values": {_WEEKLY: 999.0}}) + "\n", encoding="utf-8")

    bh.backfill(store=store)
    rows = _rows(store)
    assert rows[1]["values"][_WEEKLY] == 1.0             # the real recomputation
    assert "carried_forward" not in rows[1]


def test_carry_forward_kill_switch_restores_the_pure_rebuild(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKFILL_CARRY_FORWARD", "false")
    _stub_two_weeks(monkeypatch)
    store = tmp_path / "hist.jsonl"
    _seed_live_row(store, "2026-06-05T00:00:00+00:00", 2.2)

    bh.backfill(store=store)
    rows = _rows(store)
    assert all(_LIVE_ONLY not in r["values"] for r in rows)


def test_backfill_ignores_unparseable_rows_when_carrying_forward(tmp_path, monkeypatch):
    _stub_two_weeks(monkeypatch)
    store = tmp_path / "hist.jsonl"
    store.write_text(
        "not-json\n"
        + json.dumps({"ts": "2026-06-05T00:00:00+00:00", "values": {_LIVE_ONLY: 7.0}}) + "\n",
        encoding="utf-8")

    assert bh.backfill(store=store) == 2
    assert _rows(store)[1]["values"][_LIVE_ONLY] == 7.0


def test_backfill_preserves_concurrent_live_append(tmp_path, monkeypatch):
    """A live snapshot appended during carry-forward survives the rebuild."""
    fcntl = pytest.importorskip("fcntl")
    monkeypatch.setenv("BACKFILL_CARRY_FORWARD", "true")
    _stub_two_weeks(monkeypatch)
    store = tmp_path / "hist.jsonl"
    _seed_live_row(store, "2026-06-10T00:00:00+00:00", 1.0)
    newest_ts = "2026-06-11T00:00:00+00:00"
    append_observed = Event()
    errors = []
    original_carry_forward = bh._carry_forward

    def append_concurrently():
        try:
            # Signal either real lock contention or a completed append.
            # This avoids relying on a sleep to arrange the race.
            with store.open("r+", encoding="utf-8") as probe:
                try:
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    append_observed.set()
                else:
                    fcntl.flock(probe, fcntl.LOCK_UN)
            insights.append_snapshot(store, newest_ts, {_LIVE_ONLY: 2.0})
        except Exception as exc:
            errors.append(exc)
        finally:
            append_observed.set()

    appender = Thread(target=append_concurrently, daemon=True)

    def carry_forward_with_append(weekly_rows, prior_rows):
        appender.start()
        assert append_observed.wait(timeout=0.5), "Appender made no progress"
        return original_carry_forward(weekly_rows, prior_rows)

    monkeypatch.setattr(bh, "_carry_forward", carry_forward_with_append)
    try:
        assert bh.backfill(store=store) == 2
    finally:
        if appender.ident is not None:
            appender.join(timeout=0.5)

    assert appender.ident is not None, "Carry-forward hook was not reached"
    assert not appender.is_alive(), "Appender did not finish after backfill"
    assert not errors, f"Concurrent append failed: {errors!r}"

    live_rows = [row for row in _rows(store) if row.get("kind") == "live"]
    assert any(
        row["ts"] == newest_ts and row["values"].get(_LIVE_ONLY) == 2.0
        for row in live_rows
    ), "Backfill erased the concurrent live snapshot"
    newest = max(live_rows, key=lambda row: row["ts"])
    assert newest["ts"] == newest_ts
    assert newest["values"][_LIVE_ONLY] == 2.0

    monkeypatch.setattr(bh, "_carry_forward", original_carry_forward)
    assert bh.backfill(store=store) == 2
    live_rows = [row for row in _rows(store) if row.get("kind") == "live"]
    assert len(live_rows) == 1
    assert live_rows[0]["ts"] == newest_ts
    assert live_rows[0]["values"][_LIVE_ONLY] == 2.0


@pytest.mark.parametrize("carry_forward", ["true", "false"])
def test_empty_source_preserves_history_created_after_exists_check(
    tmp_path, monkeypatch, carry_forward
):
    """An empty source preserves history populated after the existence check."""
    monkeypatch.setattr(bh.agg, "list_platforms", lambda: [])
    monkeypatch.setenv("BACKFILL_CARRY_FORWARD", carry_forward)
    store = tmp_path / "hist.jsonl"
    concurrent_text = "\n".join(
        json.dumps(row)
        for row in [
            {
                "ts": "2026-06-01T00:00:00+00:00",
                "week": "2026-W23",
                "kind": "weekly",
                "values": {_WEEKLY: 4.0},
            },
            {
                "ts": "2026-06-10T00:00:00+00:00",
                "week": "2026-W24",
                "kind": "live",
                "values": {_LIVE_ONLY: 2.2},
            },
        ]
    ) + "\n"
    writer_ran = False

    class PopulatedAfterExistsCheck(type(tmp_path)):
        def exists(self):
            nonlocal writer_ran
            existed = super().exists()
            if not existed and not writer_ran:
                writer_ran = True
                store.write_text(concurrent_text, encoding="utf-8")
            return existed

    assert not store.exists()
    assert bh.backfill(store=PopulatedAfterExistsCheck(store)) == 0
    assert writer_ran
    assert store.read_text(encoding="utf-8") == concurrent_text


@pytest.mark.parametrize("carry_forward", ["true", "false"])
def test_empty_source_creates_missing_history_store(
    tmp_path, monkeypatch, carry_forward
):
    """An empty source still creates a missing store without history rows."""
    monkeypatch.setattr(bh.agg, "list_platforms", lambda: [])
    monkeypatch.setenv("BACKFILL_CARRY_FORWARD", carry_forward)
    store = tmp_path / "hist.jsonl"

    assert not store.exists()
    assert bh.backfill(store=store) == 0
    assert store.exists()
    assert _rows(store) == []


def _seed_history_for_failed_rebuild(store):
    rows = [
        {"ts": f"2026-06-{day:02d}T00:00:00+00:00", "kind": "live",
         "values": {_LIVE_ONLY: float(day), _WEEKLY: 42.0}}
        for day in (5, 10, 11)
    ]
    rows.append({"ts": "2026-06-08T00:00:00+00:00", "week": "2026-W24",
                 "kind": "weekly", "values": {_WEEKLY: 999.0},
                 "idle": [_LIVE_ONLY]})
    previous = ("not-json: preserve these bytes\r\n"
                + "\n".join(json.dumps(row) for row in rows) + "\n").encode("utf-8")
    store.write_bytes(previous)
    return previous


def test_backfill_fsync_failure_preserves_previous_bytes(tmp_path, monkeypatch):
    """A failed fsync propagates its OSError and preserves every previous history byte."""
    import errno
    import os

    _stub_two_weeks(monkeypatch)
    monkeypatch.setenv("BACKFILL_CARRY_FORWARD", "true")
    monkeypatch.setattr(bh.agg, "recorded_idle_still_valid", lambda _week: True)
    store = tmp_path / "hist.jsonl"
    previous = _seed_history_for_failed_rebuild(store)
    original_fsync = os.fsync
    failed = False

    def fail_first_fsync(fd):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError(errno.EIO, "injected history fsync failure")
        # A restore may fsync too; allow it to complete normally.
        return original_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_first_fsync)
    with pytest.raises(OSError) as caught:
        bh.backfill(store=store)

    assert failed
    assert caught.value.errno == errno.EIO
    assert store.read_bytes() == previous


def test_backfill_write_failure_preserves_previous_bytes(tmp_path, monkeypatch):
    """An OS write failure propagates its OSError and preserves every previous history byte."""
    import errno
    import signal

    resource = pytest.importorskip("resource")
    if not hasattr(resource, "RLIMIT_FSIZE") or not hasattr(signal, "SIGXFSZ"):
        pytest.skip("File-size limits and SIGXFSZ are required")

    _stub_two_weeks(monkeypatch)
    monkeypatch.setenv("BACKFILL_CARRY_FORWARD", "true")
    monkeypatch.setattr(bh.agg, "recorded_idle_still_valid", lambda _week: True)
    original_week_row = bh._week_row

    def large_week_row(week, records):
        row = original_week_row(week, records)
        row["values"].update({f"bow/Activity/Metric_{i:04d}": float(i) for i in range(512)})
        return row

    monkeypatch.setattr(bh, "_week_row", large_week_row)
    store = tmp_path / "hist.jsonl"
    previous = _seed_history_for_failed_rebuild(store)
    limit = 4096
    assert len(previous) < limit
    old_limits = resource.getrlimit(resource.RLIMIT_FSIZE)
    if old_limits[1] != resource.RLIM_INFINITY and old_limits[1] < limit:
        pytest.skip("Hard file-size limit is too small for this test")
    old_handler = signal.getsignal(signal.SIGXFSZ)

    try:
        signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        resource.setrlimit(resource.RLIMIT_FSIZE, (limit, old_limits[1]))
        with pytest.raises(OSError) as caught:
            bh.backfill(store=store)
    finally:
        try:
            resource.setrlimit(resource.RLIMIT_FSIZE, old_limits)
        finally:
            signal.signal(signal.SIGXFSZ, old_handler)

    assert caught.value.errno == errno.EFBIG
    assert store.read_bytes() == previous


@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit, MemoryError])
def test_backfill_interrupted_fsync_preserves_previous_bytes(
    tmp_path, monkeypatch, exception_type
):
    """A non-OSError during fsync propagates unchanged and preserves every previous byte."""
    import os

    _stub_two_weeks(monkeypatch)
    monkeypatch.setenv("BACKFILL_CARRY_FORWARD", "true")
    monkeypatch.setattr(bh.agg, "recorded_idle_still_valid", lambda _week: True)
    store = tmp_path / "hist.jsonl"
    previous = _seed_history_for_failed_rebuild(store)
    original_fsync = os.fsync
    interruption = exception_type("injected history fsync interruption")
    failed = False

    def interrupt_first_fsync(fd):
        nonlocal failed
        if not failed:
            failed = True
            raise interruption
        return original_fsync(fd)

    monkeypatch.setattr(os, "fsync", interrupt_first_fsync)
    with pytest.raises(exception_type) as caught:
        bh.backfill(store=store)

    assert failed
    assert caught.value is interruption
    assert store.read_bytes() == previous


def test_backfill_restore_failure_preserves_original_interruption(tmp_path, monkeypatch):
    """An OSError while restoring history must not replace the original KeyboardInterrupt."""
    import errno
    import os

    _stub_two_weeks(monkeypatch)
    monkeypatch.setenv("BACKFILL_CARRY_FORWARD", "true")
    monkeypatch.setattr(bh.agg, "recorded_idle_still_valid", lambda _week: True)
    store = tmp_path / "hist.jsonl"
    _seed_history_for_failed_rebuild(store)
    interruption = KeyboardInterrupt("injected history fsync interruption")
    failed = False

    def interrupt_then_fail_fsync(fd):
        nonlocal failed
        if not failed:
            failed = True
            raise interruption
        raise OSError(errno.EIO, "injected history restore fsync failure")

    monkeypatch.setattr(os, "fsync", interrupt_then_fail_fsync)
    with pytest.raises(KeyboardInterrupt) as caught:
        bh.backfill(store=store)

    assert failed
    assert caught.value is interruption
