"""Tests for bin/bushido_check.py fail-closed classification.

Phase 1c (P7): an engine error must not let a high-blast/irreversible skill
auto-fire. `_is_sensitive_skill` reads state/skill_tiers.json DIRECTLY (the
engine may be what errored) and decides whether the decision-error path returns
HARD_STOP (exit 2, fail-closed) instead of the historical fail-open exit 3.

These unit-test `_is_sensitive_skill` against the REAL shipped tier table so the
classification stays honest if the table is re-tiered.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# Load bin/bushido_check.py by path — `bin` is not an importable package.
_REPO = Path(__file__).resolve().parents[1]
_BUSHIDO_CHECK = _REPO / "bin" / "bushido_check.py"
_spec = importlib.util.spec_from_file_location("bushido_check", _BUSHIDO_CHECK)
bushido_check = importlib.util.module_from_spec(_spec)
sys.modules["bushido_check"] = bushido_check
_spec.loader.exec_module(bushido_check)

_is_sensitive_skill = bushido_check._is_sensitive_skill


# ── Sensitive skills (system blast / irreversible / hitl|hard_stop tier) ──────

@pytest.mark.parametrize("skill", ["skill-creator", "skill-consolidator", "self-heal"])
def test_system_blast_skills_are_sensitive(skill):
    assert _is_sensitive_skill(skill, _REPO) is True


def test_leading_slash_is_tolerated():
    # Callers may pass "/skill-creator"; classification must still hold.
    assert _is_sensitive_skill("/skill-creator", _REPO) is True


# ── Non-sensitive skills (confined/repo + auto/queue, reversible) ─────────────

@pytest.mark.parametrize("skill", ["simplify", "investigate", "governance-review"])
def test_low_risk_skills_are_not_sensitive(skill):
    assert _is_sensitive_skill(skill, _REPO) is False


# ── Unknown skills fail open (return False) ───────────────────────────────────

def test_unknown_skill_is_not_sensitive():
    assert _is_sensitive_skill("totally-made-up-skill", _REPO) is False


def test_empty_skill_is_not_sensitive():
    assert _is_sensitive_skill("", _REPO) is False


# ── Unreadable table fails open (return False, do not manufacture a hard-stop) ─

def test_missing_table_is_not_sensitive(tmp_path):
    # repo_root with no state/skill_tiers.json -> read error -> False.
    assert _is_sensitive_skill("skill-creator", tmp_path) is False
