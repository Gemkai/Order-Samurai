"""Drift guard: bin/skill_conflict_audit.py must stay in lockstep with the kernel.

Lives under agentica_core/tests so `agentica_core` resolves to the canonical Governance package.
The bin is loaded by explicit file path (stdlib-only at import).

Skill_Conflicts = len(skill_conflicts.json["groups"] or []) in scouts.security_signals
(scouts/__init__.py:159-161). The mechanism re-reads the SAME file and re-counts groups, so this
asserts (1) the bin's conflict_count == that scout formula (on a fixture and on the live file when
present), and (2) the bin's live FAIL threshold == the (post-calibration) METRIC_CONFIG value.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from agentica_core.insights import METRIC_CONFIG

_BIN_PATH = Path(__file__).resolve().parents[2] / "bin" / "skill_conflict_audit.py"


def _load_bin():
    spec = importlib.util.spec_from_file_location("skill_conflict_audit", _BIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _scout_formula(conf: dict) -> int:
    # Mirror scouts/__init__.py:161 exactly.
    return len(conf.get("groups") or [])


def test_conflict_count_matches_scout_formula_on_fixture():
    bin_mod = _load_bin()
    conf = {"groups": {"security": ["a", "b"], "research": ["c"]}}
    assert bin_mod.conflict_count(conf["groups"]) == _scout_formula(conf) == 2
    assert bin_mod.conflict_count({}) == _scout_formula({"groups": {}}) == 0


def test_conflict_count_matches_scout_on_live_file():
    bin_mod = _load_bin()
    path = bin_mod._conflicts_path()
    if path is None or not path.exists():
        pytest.skip("skill_conflicts.json absent in this environment")
    conf = json.loads(path.read_text(encoding="utf-8"))
    groups, calibrated = bin_mod._real_groups()
    assert calibrated is True
    assert bin_mod.conflict_count(groups) == _scout_formula(conf)


def test_uncalibrated_when_source_missing(monkeypatch):
    bin_mod = _load_bin()
    monkeypatch.setattr(bin_mod, "_conflicts_path", lambda: Path("Z:/nonexistent/skill_conflicts.json"))
    assert bin_mod._real_groups() == (None, False)


def test_fail_threshold_matches_metric_config():
    bin_mod = _load_bin()
    assert bin_mod._live_fail_threshold() == float(METRIC_CONFIG["Skill_Conflicts"]["fail"])
