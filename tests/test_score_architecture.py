"""Unit tests for execution/score_architecture.compute_score.

The scoring logic is exercised against canned verifier evidence (the real
verifiers are monkeypatched out), so the test is deterministic and never touches
disk for the verifier runs.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution import score_architecture as S


_SCORECARD = {
    "scoring": {"targetScore": 100, "mergeFloor": 70, "releaseFloor": 85,
                "enforcementMode": "advisory-until-verifiers-exist"},
    "categories": [
        {"id": "a", "label": "A", "weight": 50, "requiredArtifacts": [],
         "requiredVerifiers": ["execution/verify_a.py"]},   # pass
        {"id": "b", "label": "B", "weight": 30, "requiredArtifacts": [],
         "requiredVerifiers": ["execution/verify_b.py"]},   # blocking (FAIL)
        {"id": "c", "label": "C", "weight": 15, "requiredArtifacts": [],
         "requiredVerifiers": ["execution/verify_c.py"]},   # advisory_gap (missing)
        {"id": "d", "label": "D", "weight": 5, "requiredArtifacts": [],
         "requiredVerifiers": ["execution/verify_d.py"]},   # advisory_warn (WARN)
    ],
}

_CANNED = {
    "execution/verify_a.py": ("ran", []),
    "execution/verify_b.py": ("ran", [{"status": "FAIL", "label": "b", "message": "boom"}]),
    "execution/verify_c.py": ("missing", []),
    "execution/verify_d.py": ("ran", [{"status": "WARN", "label": "d", "message": "meh"}]),
}


def _patched(monkeypatch):
    monkeypatch.setattr(S, "_run_verifier", lambda v, repo_root: _CANNED[v])


def test_score_sums_only_passing_and_warned_weights(monkeypatch):
    _patched(monkeypatch)
    r = S.compute_score(_SCORECARD)
    # earned = A(50, pass) + D(5, advisory_warn); B blocking=0; C gap=0
    assert r["score"] == 55
    assert r["target_score"] == 100


def test_achievable_excludes_unbuilt_verifier_categories(monkeypatch):
    _patched(monkeypatch)
    r = S.compute_score(_SCORECARD)
    # A + B + D are built (50+30+5); C is missing
    assert r["achievable_now"] == 85


def test_blocking_and_advisory_gap_are_distinguished(monkeypatch):
    _patched(monkeypatch)
    r = S.compute_score(_SCORECARD)
    assert r["blocking_categories"] == ["b"]
    assert r["advisory_gaps"] == ["c"]


def test_floor_flags(monkeypatch):
    _patched(monkeypatch)
    r = S.compute_score(_SCORECARD)
    assert r["meets_merge_floor"] is False   # 55 < 70
    assert r["meets_release_floor"] is False  # 55 < 85


def test_category_statuses(monkeypatch):
    _patched(monkeypatch)
    r = S.compute_score(_SCORECARD)
    by_id = {c["id"]: c["status"] for c in r["categories"]}
    assert by_id == {"a": "pass", "b": "blocking", "c": "advisory_gap", "d": "advisory_warn"}


def test_broken_verifier_does_not_crash_scorer(monkeypatch):
    # _run_verifier itself catches exceptions; ensure a FAIL from 'error' state counts blocking.
    monkeypatch.setattr(S, "_run_verifier", lambda v, repo_root: (
        "error", [{"status": "FAIL", "label": v, "message": "raised"}]))
    r = S.compute_score(_SCORECARD)
    # every category's verifier "ran" (error returns results) → all blocking → score 0
    assert r["score"] == 0
    assert set(r["blocking_categories"]) == {"a", "b", "c", "d"}
