"""Drift guard: bin/wiki_link.py must stay in lockstep with the kernel.

Lives under agentica_core/tests so `agentica_core` resolves to the canonical Governance package.
The bin is loaded by explicit file path (stdlib-only at import).

Wiki_Orphans = len(vault_health.find_orphaned_wiki()) via aggregate._vault_health_metrics. The
mechanism re-runs the SAME check at the SAME resolved path, so this asserts (1) the bin's orphan
count == the kernel's Wiki_Orphans on the live vault, and (2) the bin's live FAIL threshold ==
the (post-calibration) METRIC_CONFIG value. The count check skips when the vault is unavailable.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from agentica_core import aggregate as agg
from agentica_core.insights import METRIC_CONFIG

_BIN_PATH = Path(__file__).resolve().parents[2] / "bin" / "wiki_link.py"


def _load_bin():
    spec = importlib.util.spec_from_file_location("wiki_link", _BIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_orphan_count_matches_kernel_wiki_orphans():
    bin_mod = _load_bin()
    vault = agg._vault_health_metrics()
    if vault is None:
        pytest.skip("vault-health script unavailable in this environment")
    orphans, calibrated = bin_mod._real_orphans()
    assert calibrated is True
    assert len(orphans) == vault["Wiki_Orphans"]


def test_uncalibrated_when_vault_script_missing(monkeypatch):
    bin_mod = _load_bin()
    monkeypatch.setattr(agg, "_VAULT_HEALTH_SCRIPT", Path("Z:/nonexistent/vault_health.py"))
    orphans, calibrated = bin_mod._real_orphans()
    assert (orphans, calibrated) == ([], False)


def test_fail_threshold_matches_metric_config():
    bin_mod = _load_bin()
    assert bin_mod._live_fail_threshold() == float(METRIC_CONFIG["Wiki_Orphans"]["fail"])
