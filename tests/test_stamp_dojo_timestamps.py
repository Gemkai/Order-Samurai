"""Tests for the dojo timestamp backstop — the code-level guarantee that
calibration samples (started_at, completed_at) are captured.

Calibration coefficients only accumulate from (started_at, completed_at) pairs;
an item that reaches "done" without a started_at is a permanently lost sample.
The dojo cycle stamps started_at at dispatch (Step C) and completed_at at
completion (Step F) via this backstop. These tests pin that behaviour.
"""
import importlib.util
import json
from pathlib import Path

_STAMP = Path(__file__).resolve().parents[1] / "bin" / "stamp_dojo_timestamps.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("stamp_dojo_timestamps", _STAMP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_state(state_path, backlog):
    state_path.write_text(json.dumps({"backlog": backlog}), encoding="utf-8")
    return state_path


def _item(**overrides):
    base = {"id": "x", "kind": "field", "status": "todo"}
    base.update(overrides)
    return base


def test_started_at_stamped_for_dispatched_item(tmp_path, monkeypatch):
    """A dispatched (status='doing') item with no started_at gets one — the
    dispatch-time capture calibration depends on."""
    mod = _load_module()
    state = _write_state(tmp_path / "DOJO_STATE.json", [_item(status="doing")])
    monkeypatch.setattr(mod, "STATE", state)

    mod.main()

    item = json.loads(state.read_text(encoding="utf-8"))["backlog"][0]
    assert item.get("started_at")


def test_completed_at_stamped_for_done_item(tmp_path, monkeypatch):
    """A done item with no completed_at gets one, flagged backfilled."""
    mod = _load_module()
    state = _write_state(tmp_path / "DOJO_STATE.json",
                         [_item(status="done", started_at="2026-06-01T00:00:00Z")])
    monkeypatch.setattr(mod, "STATE", state)

    mod.main()

    item = json.loads(state.read_text(encoding="utf-8"))["backlog"][0]
    assert item.get("completed_at")
    assert item.get("backfilled") is True


def test_dispatch_then_complete_yields_a_calibration_sample(tmp_path, monkeypatch):
    """The full Step C -> Step F lifecycle leaves both timestamps, so the item is a
    countable (started_at, completed_at) calibration sample."""
    mod = _load_module()
    state = tmp_path / "DOJO_STATE.json"
    _write_state(state, [_item(status="doing")])
    monkeypatch.setattr(mod, "STATE", state)

    mod.main()  # Step C: dispatch -> started_at
    dispatched = json.loads(state.read_text(encoding="utf-8"))["backlog"][0]
    assert dispatched.get("started_at")

    dispatched["status"] = "done"  # ... work happens, item completes ...
    _write_state(state, [dispatched])

    mod.main()  # Step F: completion -> completed_at
    final = json.loads(state.read_text(encoding="utf-8"))["backlog"][0]
    assert final.get("started_at") and final.get("completed_at")
    assert final["completed_at"] >= final["started_at"]


def test_fully_stamped_item_is_left_untouched(tmp_path, monkeypatch):
    """Idempotent: an item already carrying both timestamps is not re-stamped."""
    mod = _load_module()
    original = _item(status="done", started_at="2026-06-01T00:00:00Z",
                     completed_at="2026-06-01T00:30:00Z")
    state = _write_state(tmp_path / "DOJO_STATE.json", [dict(original)])
    monkeypatch.setattr(mod, "STATE", state)

    mod.main()

    item = json.loads(state.read_text(encoding="utf-8"))["backlog"][0]
    assert item["started_at"] == original["started_at"]
    assert item["completed_at"] == original["completed_at"]
    assert "backfilled" not in item
