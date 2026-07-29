"""Tests for the stale-'doing' requeue backstop (reap_stale_doing.py).

STEP C of the meditation cycle marks items 'doing'; a cycle that dies mid-run
strands them there forever. This backstop resets stale 'doing' items to 'todo'.
These tests pin that behaviour — including the timezone-robustness of the age
calculation, which decides whether an item is stale.
"""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REAP = Path(__file__).resolve().parents[1] / "bin" / "reap_stale_doing.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("reap_stale_doing", _REAP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_state(state_path, backlog):
    state_path.write_text(json.dumps({"backlog": backlog}), encoding="utf-8")
    return state_path


def _item(**overrides):
    base = {"id": "x", "kind": "field", "pillar": "bow", "status": "doing"}
    base.update(overrides)
    return base


def test_naive_started_at_does_not_crash_and_reaps_stale_item(tmp_path, monkeypatch):
    """A 'doing' item whose started_at is a full ISO datetime WITHOUT a timezone
    (the historical style cycle models copy) must be treated as UTC, not crash the
    run. Before the fix, `now(aware) - started(naive)` raised TypeError and the
    whole backstop aborted, leaving every stale item stranded."""
    mod = _load_module()
    # 48h ago, written naive (no Z / offset) — well past the default 6h window.
    naive_old = (datetime.now(timezone.utc) - timedelta(hours=48)).replace(
        tzinfo=None).isoformat()
    state = _write_state(tmp_path / "MEDITATION_STATE.json",
                         [_item(started_at=naive_old)])
    monkeypatch.setattr(mod, "STATE", state)

    rc = mod.main()  # must not raise TypeError

    assert rc == 0
    item = json.loads(state.read_text(encoding="utf-8"))["backlog"][0]
    assert item["status"] == "todo"          # reaped
    assert item["started_at"] is None
    assert item.get("requeue_count") == 1


def test_naive_started_at_recent_item_is_left_in_flight(tmp_path, monkeypatch):
    """A naive started_at that is recent (inside the staleness window) must be
    parsed as UTC and recognised as plausibly in-flight — left as 'doing'."""
    mod = _load_module()
    naive_recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(
        tzinfo=None).isoformat()
    state = _write_state(tmp_path / "MEDITATION_STATE.json",
                         [_item(started_at=naive_recent)])
    monkeypatch.setattr(mod, "STATE", state)

    rc = mod.main()

    assert rc == 0
    item = json.loads(state.read_text(encoding="utf-8"))["backlog"][0]
    assert item["status"] == "doing"         # left in flight, not reaped
