"""Drift guard: bin/error_triage.py constants must stay in lockstep with the kernel.

This lives under agentica_core/tests (not Order Samurai/tests) so `agentica_core`
resolves to the canonical Governance package — Order Samurai has a partial shadow
`agentica_core/` package that would otherwise hijack the import. The bin is loaded by
explicit file path (it is stdlib-only at import time) to avoid the same shadow.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from agentica_core import aggregate as agg
from agentica_core.insights import METRIC_CONFIG

_BIN_PATH = Path(__file__).resolve().parents[2] / "bin" / "error_triage.py"


def _load_bin():
    spec = importlib.util.spec_from_file_location("error_triage", _BIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_error_rate_classification_no_drift():
    bin_mod = _load_bin()
    assert bin_mod.ERROR_STATUSES == agg.ERROR_STATUSES
    assert bin_mod.MIN_ERROR_SAMPLE == agg.MIN_ERROR_SAMPLE


def test_fail_threshold_matches_metric_config():
    bin_mod = _load_bin()
    assert bin_mod.DEFAULT_FAIL_THRESHOLD == float(METRIC_CONFIG["Error_Rate"]["fail"])


def test_error_rate_stats_matches_kernel_reducer():
    # The bin's standalone computation must agree with the kernel reducer on the same input.
    bin_mod = _load_bin()
    recs = [{"status": "error"}] * 3 + [{"status": "success"}] * 9   # 12 records, 25%
    assert bin_mod.error_rate_stats(recs) == agg.error_rate_stats(recs)
    assert agg.r_error_rate(recs) == 25.0
