"""Tests for the keiko loop-until-dry early-stop signal (bin/keiko_improvement.py)."""
import importlib.util
from pathlib import Path

_MOD = Path(__file__).resolve().parents[1] / "bin" / "keiko_improvement.py"
_spec = importlib.util.spec_from_file_location("keiko_improvement", _MOD)
keiko = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(keiko)


def _state(bow=10, sword=10, **extra):
    return {"pillars": {"bow": {"live_current": bow}, "sword": {"live_current": sword}}, **extra}


def test_first_observation_sets_baseline_and_continues():
    st, code, msg = keiko.evaluate(_state(10, 10), k=3)
    assert code == 0
    assert st["_keiko_last_signal"] == 20
    assert st["flat_cycle_count"] == 0
    assert "baseline" in msg


def test_improvement_resets_counter():
    st = _state(10, 10, _keiko_last_signal=15, flat_cycle_count=2)
    st, code, msg = keiko.evaluate(st, k=3)
    assert code == 0
    assert st["flat_cycle_count"] == 0  # 20 > 15 → reset
    assert "improved" in msg


def test_flat_cycle_increments_and_halts_at_k():
    st = _state(10, 10, _keiko_last_signal=20, flat_cycle_count=2)  # 20 == 20 → flat
    st, code, msg = keiko.evaluate(st, k=3)
    assert st["flat_cycle_count"] == 3
    assert code == 3  # reached K → halt
    assert "no improvement" in msg


def test_regression_counts_as_flat():
    st = _state(8, 8, _keiko_last_signal=20, flat_cycle_count=0)  # 16 < 20
    st, code, _ = keiko.evaluate(st, k=3)
    assert st["flat_cycle_count"] == 1
    assert code == 0


def test_null_live_current_treated_as_zero():
    st = {"pillars": {"bow": {"live_current": None}, "sword": {"live_current": 5}}}
    s = keiko._signal(st)
    assert s == 5


def test_below_k_keeps_going():
    st = _state(10, 10, _keiko_last_signal=20, flat_cycle_count=0)
    st, code, _ = keiko.evaluate(st, k=5)
    assert st["flat_cycle_count"] == 1
    assert code == 0
