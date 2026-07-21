import json

from agentica_core import aggregate as agg

_SCORECARD = {
    "categories": [
        {"id": "path_authority", "label": "Path Authority", "weight": 15},
        {"id": "truth_separation", "label": "Truth Separation", "weight": 15},
    ],
    "reporting": {"emitJsonTo": "artifacts/architecture_score.json"},
}

_ARTIFACT = {
    "generated_at": "2026-06-19T00:00:00+00:00",
    "score": 60, "target_score": 100, "merge_floor": 70, "release_floor": 85,
    "meets_merge_floor": False, "meets_release_floor": False,
    "enforcement_mode": "advisory-until-verifiers-exist",
    "blocking_categories": [],
    "advisory_gaps": ["truth_separation"],
    "categories": [
        {"id": "path_authority", "label": "Path Authority", "weight": 15, "earned": 15,
         "status": "pass", "missing_verifiers": [], "warnings": []},
        {"id": "truth_separation", "label": "Truth Separation", "weight": 15, "earned": 0,
         "status": "advisory_gap", "missing_verifiers": ["execution/verify_truth.py"],
         "warnings": []},
    ],
}


def _write_pair(tmp_path):
    """Lay out config/ + artifacts/ as the real repo does (artifact resolved from
    config's parent.parent + reporting.emitJsonTo)."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (tmp_path / "artifacts").mkdir()
    sc = cfg_dir / "architecture_scorecard.json"
    sc.write_text(json.dumps(_SCORECARD), encoding="utf-8")
    (tmp_path / "artifacts" / "architecture_score.json").write_text(
        json.dumps(_ARTIFACT), encoding="utf-8")
    return sc


def test_breakdown_reads_artifact_via_reporting_path(tmp_path):
    b = agg.architecture_breakdown(_write_pair(tmp_path))
    assert b is not None
    assert b["score"] == 60
    assert b["advisory_gaps"] == ["truth_separation"]
    assert len(b["categories"]) == 2
    gap = next(c for c in b["categories"] if c["id"] == "truth_separation")
    assert gap["status"] == "advisory_gap"
    assert gap["earned"] == 0
    assert gap["missing_verifiers"] == ["execution/verify_truth.py"]


def test_breakdown_none_when_scorecard_missing():
    assert agg.architecture_breakdown(None) is None


def test_breakdown_none_when_artifact_absent(tmp_path):
    """Config present, artifact never emitted -> None (panel shows no-data, not false 0)."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    sc = cfg_dir / "architecture_scorecard.json"
    sc.write_text(json.dumps(_SCORECARD), encoding="utf-8")
    assert agg.architecture_breakdown(sc) is None
