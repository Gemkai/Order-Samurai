"""Drift guard: bin/chain_depth_audit.py must stay in lockstep with the kernel.

This lives under agentica_core/tests (not Order Samurai/tests) so `agentica_core` resolves to
the canonical Governance package — Order Samurai has a partial shadow `agentica_core/` package
that would otherwise hijack the import. The bin is loaded by explicit file path (it is
stdlib-only at import time) to avoid the same shadow.

The mechanism's breach_confirmed gate must grade the IDENTICAL value the dashboard grades, so
this asserts: (1) the bin's median == aggregate.r_chain_depth_avg on a shared fixture, and
(2) the bin's live FAIL threshold == the (post-calibration) METRIC_CONFIG value.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from agentica_core import aggregate as agg
from agentica_core.insights import METRIC_CONFIG

_BIN_PATH = Path(__file__).resolve().parents[2] / "bin" / "chain_depth_audit.py"


def _load_bin():
    spec = importlib.util.spec_from_file_location("chain_depth_audit", _BIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_chain_depth_matches_kernel_reducer():
    # The bin's standalone median must agree with the kernel reducer on the same input,
    # including the empty/uncalibrated case (both -> None) and the bool/non-numeric drop.
    bin_mod = _load_bin()
    recs = [{"chain_depth": d} for d in (2, 4, 6, 600, 1019)] + [{"status": "success"}]
    assert bin_mod.chain_depth_median(recs) == agg.r_chain_depth_avg(recs)
    assert bin_mod.chain_depth_stats(recs)[0] == agg.r_chain_depth_avg(recs)
    assert bin_mod.chain_depth_median([{"status": "success"}]) == agg.r_chain_depth_avg([{"status": "success"}])


def test_fail_threshold_matches_metric_config():
    # The bin resolves the LIVE (post-calibration-clamp) fail the dashboard grades on, not a
    # stale static default — Chain_Depth_Avg is calibration-eligible.
    bin_mod = _load_bin()
    assert bin_mod._live_fail_threshold() == float(METRIC_CONFIG["Chain_Depth_Avg"]["fail"])


def test_chain_depth_field_name_no_drift():
    # The field the bin reads must be the field the kernel reducer reads.
    bin_mod = _load_bin()
    assert bin_mod.CHAIN_DEPTH_FIELD == "chain_depth"
    # Sanity: the kernel reducer is registered and live for this metric.
    assert callable(agg.r_chain_depth_avg)
