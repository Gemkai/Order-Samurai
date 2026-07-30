"""Regression test for _estimated_human_time_saved (Est. Human Hours Saved hero).

A completed arts backlog item with an explicit effort of 0 must contribute 0
effort points, not silently count as 1 — the `effort or 1` falsy-zero trap
(same class as the reflex_eureka 0%-rate fix, #50) inflates the hero metric.
"""
import json
from datetime import datetime, timezone

from agentica_core import aggregate as agg


def _write_fixture(repo_root, *, effort):
    state_dir = repo_root / "state"
    state_dir.mkdir(parents=True)
    now_iso = datetime.now(timezone.utc).isoformat()
    item = {"status": "done", "pillar": "arts", "completed_at": now_iso}
    if effort is not None:
        item["effort"] = effort
    (state_dir / "MEDITATION_STATE.json").write_text(
        json.dumps({"backlog": [item]}), encoding="utf-8")
    # Only the arts coefficient is non-zero, so val isolates the arts term
    # (promotions/vibe/docs all multiply by a 0 benchmark).
    (state_dir / "calibration_coefficients.json").write_text(
        json.dumps({"craft": {"arts_backlog_hrs_per_effort_point": {"benchmark": 2.0}}}),
        encoding="utf-8")


def test_zero_effort_done_item_contributes_zero_hours(tmp_path):
    _write_fixture(tmp_path, effort=0)
    out = agg._estimated_human_time_saved([], repo_root=tmp_path)
    # effort 0 point × 2.0 h/point = 0.0 h, not 1 phantom point (2.0 h).
    assert out["val"] == 0.0


def test_missing_effort_defaults_to_one_point(tmp_path):
    # The default-of-1 for a missing effort key must be preserved.
    _write_fixture(tmp_path, effort=None)
    out = agg._estimated_human_time_saved([], repo_root=tmp_path)
    assert out["val"] == 2.0
