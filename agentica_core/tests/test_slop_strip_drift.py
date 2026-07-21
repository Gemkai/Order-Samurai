"""Drift guard: bin/slop_strip.py must stay in lockstep with the kernel.

Lives under agentica_core/tests so `agentica_core` resolves to the canonical Governance package.
The bin is loaded by explicit file path (stdlib-only at import).

Slop_Density = aggregate.r_slop_density = round(sum(slop_markers)/sum(output_words)*1000, 2). The
mechanism re-measures the same value, so this asserts (1) bin.slop_density_stats == r_slop_density
on a shared fixture (including the zero-denominator -> None case), and (2) the bin's live FAIL
threshold == the (post-calibration) METRIC_CONFIG value.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from agentica_core import aggregate as agg
from agentica_core.insights import METRIC_CONFIG

_BIN_PATH = Path(__file__).resolve().parents[2] / "bin" / "slop_strip.py"


def _load_bin():
    spec = importlib.util.spec_from_file_location("slop_strip", _BIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_slop_density_matches_kernel_reducer():
    bin_mod = _load_bin()
    recs = [
        {"slop_markers": 12, "output_words": 1000},
        {"slop_markers": 8, "output_words": 1500},
        {"status": "success"},  # no fields — must not skew
    ]
    assert bin_mod.slop_density_stats(recs)[0] == agg.r_slop_density(recs)
    # zero denominator -> both None
    zero = [{"slop_markers": 5, "output_words": 0}]
    assert bin_mod.slop_density_stats(zero)[0] == agg.r_slop_density(zero) is None


def test_field_names_no_drift():
    bin_mod = _load_bin()
    assert bin_mod.SLOP_MARKERS_FIELD == "slop_markers"
    assert bin_mod.OUTPUT_WORDS_FIELD == "output_words"
    assert callable(agg.r_slop_density)


def test_fail_threshold_matches_metric_config():
    bin_mod = _load_bin()
    assert bin_mod._live_fail_threshold() == float(METRIC_CONFIG["Slop_Density"]["fail"])
