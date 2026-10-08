"""RECON1PUB: regression tests for the aggregate.py forward port (monorepo hunks 4-9 and 19).

Each test below is copied verbatim from the monorepo's agentica_core/tests/test_aggregate.py
(withheld from the public export as a whole): vibe-alignment gaps stay gaps (a69f42070),
a telemetry tag is counted into at most one project (969aa026b / #286), and an
agent-spawn scan tolerates a transcript that vanishes mid-scan (5eab518ea).
"""
import json
from datetime import datetime, timezone

from agentica_core import aggregate as agg


def test_vibe_alignment_score_returns_none_when_state_file_absent(tmp_path):
    # No state/vibe_alignment.json at all (scout never ran / was retired) — a
    # gap, not a measured 0.0 (which would read as "very slopped code").
    from agentica_core.aggregate import _vibe_alignment_score
    assert _vibe_alignment_score([], tmp_path) is None


def test_vibe_alignment_score_returns_none_when_score_is_null(tmp_path):
    # Scout ran but recorded score=null (e.g. all LLM backends unavailable).
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "vibe_alignment.json").write_text(
        json.dumps({"score": None, "reason": "offline"}), encoding="utf-8")
    from agentica_core.aggregate import _vibe_alignment_score
    assert _vibe_alignment_score([], tmp_path) is None


def test_vibe_alignment_score_returns_none_when_file_unreadable(tmp_path):
    # Corrupt/truncated JSON on disk must not raise out of the reducer.
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "vibe_alignment.json").write_text("{not valid json", encoding="utf-8")
    from agentica_core.aggregate import _vibe_alignment_score
    assert _vibe_alignment_score([], tmp_path) is None


def test_vibe_alignment_score_returns_float_for_a_real_score(tmp_path):
    # Sanity: the gap fix must not turn a genuine measured score into a gap too.
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "vibe_alignment.json").write_text(
        json.dumps({"score": 82, "generated_at": "2026-09-09T00:00:00+00:00"}),
        encoding="utf-8")
    from agentica_core.aggregate import _vibe_alignment_score
    assert _vibe_alignment_score([], tmp_path) == 82.0


def test_craft_improvements_vibe_detail_reads_no_data_on_gap(tmp_path):
    # _craft_improvements formats vibe_now into its "detail" string; a gap must
    # render as an explicit "no data" fragment, not crash on `None:g` or leak
    # a fabricated level/delta into the dashboard.
    from agentica_core.aggregate import _craft_improvements
    out = _craft_improvements([], repo_root=tmp_path)
    assert "Vibe no data" in out["detail"]


def test_estimated_human_time_saved_vibe_gap_contributes_no_hours(tmp_path):
    # A vibe gap must not raise inside the hour-savings arithmetic, and must
    # contribute exactly 0.0 (never a fabricated or negative gain).
    from agentica_core.aggregate import _estimated_human_time_saved
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "calibration_coefficients.json").write_text(json.dumps({
        "craft": {
            "skill_promotion_hrs_per_promotion": {"benchmark": 0.25},
            "arts_backlog_hrs_per_effort_point": {"benchmark": 3.0},
            "vibe_alignment_hrs_per_point": {"benchmark": 0.5},
            "doc_parity_latency_hrs_per_day": {"benchmark": 2.0},
        }}), encoding="utf-8")
    out = _estimated_human_time_saved([], repo_root=tmp_path)
    assert "vibe" not in out.get("error", "")
    assert out["val"] == 0.0


def test_build_project_scores_does_not_double_count_a_tag_across_two_folders(tmp_path):
    """`matched_tproj` is tracked but was never consulted to stop a tag matching more
    than one folder: the >=6-char substring guard only bounds which folder/tag PAIRS
    are eligible, not how many folders a single tag may be counted into. A folder
    whose normalized name is a substring of another real folder's normalized name
    (e.g. "AgenticaOS" is a substring of "AgenticaOS-bot") absorbed the longer
    folder's own exactly-matching tag via the substring branch, so that tag's
    records were summed into BOTH folders' Session_Count instead of just the one
    it exactly names."""
    (tmp_path / "AgenticaOS").mkdir()
    (tmp_path / "AgenticaOS-bot").mkdir()

    recs = [
        {"project": "AgenticaOS-bot", "session_id": f"s{i}",
         "timestamp": "2026-07-20T10:00:00+00:00", "error": False}
        for i in range(4)
    ]
    proj_platform = {"AgenticaOS-bot": "claude"}

    out = agg.build_project_scores(recs, proj_platform, root=tmp_path)

    # The tag exactly names "AgenticaOS-bot" -- it must count there and nowhere else.
    assert out["AgenticaOS-bot"]["records"] == 4
    assert out["AgenticaOS"]["records"] == 0
    assert out["AgenticaOS"]["has_data"] is False


def test_agent_spawn_events_skips_a_transcript_that_vanishes_before_stat(monkeypatch, tmp_path):
    """A session JSONL deleted between rglob and stat (factory worktree cleanup) is
    skipped instead of raising FileNotFoundError out of the whole aggregate refresh."""
    monkeypatch.setattr(agg, "_AGENT_SPAWN_CACHE", {"t": 0.0, "v": None})
    projects_dir = tmp_path / ".claude" / "projects" / "proj"
    projects_dir.mkdir(parents=True)
    entry = {
        "type": "assistant",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": {"content": [{"type": "tool_use", "name": "Agent",
                                  "input": {"subagent_type": "kept_agent"}}]},
    }
    for name in ("kept.jsonl", "gone.jsonl"):
        (projects_dir / name).write_text(json.dumps(entry) + "\n", encoding="utf-8")
    real_stat = agg.Path.stat

    def racing_stat(self, *args, **kwargs):
        if self.name == "gone.jsonl":
            raise FileNotFoundError(2, "No such file or directory", str(self))
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(agg.Path, "stat", racing_stat)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert agg._count_agent_types(window_days=7) == {"kept_agent": 1}



def test_idle_by_design_envelope_is_live_and_carries_its_state(monkeypatch, tmp_path):
    """Hunks 15-18: a Mechanism_Liveness reading that is idle by design keeps its LIVE
    tier and exposes `state`, instead of being labelled SIMULATED. This is the envelope
    half of the monorepo's test_idle_envelope_reaches_the_card_as_a_live_reported_state;
    the card half depends on render.py, which this port does not touch."""
    events = tmp_path / "autonomic_events.jsonl"
    heartbeat = tmp_path / "reflex_output.jsonl"
    events.write_text("", encoding="utf-8")
    heartbeat.write_text(json.dumps({
        "timestamp": "2026-10-06T12:00:00Z", "metric": "reflex",
        "line": "[starved] 0 eligible with 6 CRITICAL/HIGH entries in the payload"}) + "\n",
        encoding="utf-8")
    monkeypatch.setattr(agg, "default_events_path", lambda: events)
    monkeypatch.setattr(agg, "_REFLEX_HEARTBEAT_LOG", heartbeat)
    monkeypatch.setattr(agg, "_REFLEX_EXEC_LOG", tmp_path / "exec_log.jsonl", raising=False)
    monkeypatch.setattr(agg, "REGISTRY", [r for r in agg.REGISTRY if r[2] == "Mechanism_Liveness"])

    env = agg.build_pillars([{"timestamp": "2026-10-07T13:00:00Z"}])["bow"]["Autonomic"]["Mechanism_Liveness"]

    assert env["is_simulated"] is False
    assert env["state"] == "idle_by_design"
