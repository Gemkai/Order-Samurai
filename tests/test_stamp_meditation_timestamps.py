"""Tests for the meditation timestamp backstop — the code-level guarantee that
calibration samples (started_at, completed_at) are captured.

Contract (post-#41 "Option C" — no snapshot/transition tracking): the backstop
stamps `started_at` on any `doing` item that lacks one, and `completed_at`
(flagged `backfilled: true`) on any `done` item that lacks one. It also
normalizes a date-only `completed_at` when a real `started_at` is present. It
never fabricates a `started_at` for an item that first appears already `done` —
that is a lost sample, honestly left as one, not recovered.
"""
import importlib.util
import json
from pathlib import Path

_STAMP = Path(__file__).resolve().parents[1] / "bin" / "stamp_meditation_timestamps.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("stamp_meditation_timestamps", _STAMP)
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


def _setup(mod, tmp_path, monkeypatch, backlog):
    state = _write_state(tmp_path / "MEDITATION_STATE.json", backlog)
    monkeypatch.setattr(mod, "STATE", state)
    return state


def _read_first(state):
    return json.loads(state.read_text(encoding="utf-8"))["backlog"][0]


def test_started_at_stamped_for_dispatched_doing_item(tmp_path, monkeypatch):
    """A dispatched (status='doing') item with no started_at gets one — the
    dispatch-time capture calibration depends on."""
    mod = _load_module()
    state = _setup(mod, tmp_path, monkeypatch, [_item(status="doing")])

    mod.main()

    assert _read_first(state).get("started_at")


def test_completed_at_stamped_for_done_item_and_flagged_backfilled(tmp_path, monkeypatch):
    """A done item with no completed_at gets one, flagged backfilled."""
    mod = _load_module()
    state = _setup(mod, tmp_path, monkeypatch,
                   [_item(status="done", started_at="2026-06-01T00:00:00Z")])

    mod.main()

    item = _read_first(state)
    assert item.get("completed_at")
    assert item.get("backfilled") is True


def test_dispatch_then_complete_yields_a_calibration_sample(tmp_path, monkeypatch):
    """The full Step C -> Step F lifecycle leaves both timestamps, so the item is a
    countable (started_at, completed_at) calibration sample."""
    mod = _load_module()
    state = _setup(mod, tmp_path, monkeypatch, [_item(status="doing")])

    mod.main()  # Step C: dispatch -> started_at
    dispatched = _read_first(state)
    assert dispatched.get("started_at")

    dispatched["status"] = "done"  # ... work happens, item completes ...
    _write_state(state, [dispatched])

    mod.main()  # Step F: completion -> completed_at
    final = _read_first(state)
    assert final.get("started_at") and final.get("completed_at")
    assert final["completed_at"] >= final["started_at"]


def test_fully_stamped_item_is_left_untouched(tmp_path, monkeypatch):
    """Idempotent: an item already carrying both timestamps is not re-stamped."""
    mod = _load_module()
    original = _item(status="done", started_at="2026-06-01T00:00:00Z",
                     completed_at="2026-06-01T00:30:00Z")
    state = _setup(mod, tmp_path, monkeypatch, [dict(original)])

    mod.main()

    item = _read_first(state)
    assert item["started_at"] == original["started_at"]
    assert item["completed_at"] == original["completed_at"]
    assert "backfilled" not in item


def test_doing_item_with_existing_started_at_is_not_restamped(tmp_path, monkeypatch):
    """A doing item that already carries a started_at keeps it verbatim."""
    mod = _load_module()
    state = _setup(mod, tmp_path, monkeypatch,
                   [_item(status="doing", started_at="2026-07-12T01:00:00Z")])

    mod.main()

    assert _read_first(state)["started_at"] == "2026-07-12T01:00:00Z"


def test_todo_item_is_never_stamped(tmp_path, monkeypatch):
    """A todo item has neither dispatched nor completed, so it gets no timestamps."""
    mod = _load_module()
    state = _setup(mod, tmp_path, monkeypatch, [_item(status="todo")])

    mod.main()

    item = _read_first(state)
    assert item.get("started_at") is None
    assert item.get("completed_at") is None


def test_done_item_without_started_at_is_left_as_a_lost_sample(tmp_path, monkeypatch):
    """An item that first appears already done without a started_at has no honest
    start time; the backstop must not fabricate one (it stays a lost sample)."""
    mod = _load_module()
    state = _setup(mod, tmp_path, monkeypatch,
                   [_item(status="done", started_at=None,
                          completed_at="2026-06-07T00:00:00Z")])

    mod.main()

    assert _read_first(state).get("started_at") is None


def test_date_only_completed_at_is_normalized_when_started_at_present(tmp_path, monkeypatch):
    """A date-only completed_at parses as midnight -> negative duration -> discarded
    sample. With a real started_at present, the backstop normalizes it to a full
    timestamp and flags backfilled."""
    mod = _load_module()
    state = _setup(mod, tmp_path, monkeypatch,
                   [_item(status="done", started_at="2026-06-07T00:00:00Z",
                          completed_at="2026-06-07")])

    mod.main()

    item = _read_first(state)
    assert len(item["completed_at"]) > 10  # normalized to a full timestamp
    assert item["completed_at"].endswith("Z")
    assert item["backfilled"] is True


def test_date_only_completed_at_without_started_at_is_left_untouched(tmp_path, monkeypatch):
    """A pre-instrumentation historical item (date-only completed_at, no started_at)
    has no honest duration to recover, so it is left exactly as-is."""
    mod = _load_module()
    state = _setup(mod, tmp_path, monkeypatch,
                   [_item(status="done", started_at=None, completed_at="2026-06-07")])

    mod.main()

    item = _read_first(state)
    assert item["completed_at"] == "2026-06-07"
    assert "backfilled" not in item


def test_non_string_completed_at_does_not_crash_the_backstop(tmp_path, monkeypatch):
    """A cycle model can write completed_at as a non-string value (e.g. a raw
    epoch number) instead of an ISO-8601 string -- this backstop exists precisely
    because 'prompt instructions are not guarantees' (module docstring). The
    date-only-normalization branch's `len(item.get("completed_at") or "")` must
    not crash on that malformed value; it should just leave the item untouched
    rather than take down the whole stamping run for every other item."""
    mod = _load_module()
    state = _setup(mod, tmp_path, monkeypatch,
                   [_item(status="done", started_at="2026-06-07T00:00:00Z",
                          completed_at=1234567890)])

    mod.main()  # must not raise TypeError: object of type 'int' has no len()

    item = _read_first(state)
    assert item["completed_at"] == 1234567890
    assert "backfilled" not in item
