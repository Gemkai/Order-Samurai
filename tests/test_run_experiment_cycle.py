"""run_experiment_cycle: the weekly experiment lane's guards, same discipline as
test_self_harness_cycle.py — dark-by-default, spacing guard, no-candidate handling, and
an injected ablation function so no test ever spends a real token or subprocess call.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin import run_experiment_cycle as cycle  # type: ignore[import-not-found]
from agentica_core import experiments  # type: ignore[import-not-found]


def _paths(tmp_path):
    return {
        "lineage_path": tmp_path / "experiment_lineage.jsonl",
        "experiments_path": tmp_path / "EXPERIMENTS.jsonl",
        "backlog_path": tmp_path / "PROPOSED_BACKLOG.json",
    }


def _file_one(tmp_path, hypothesis="skill body is load-bearing"):
    return experiments.file_experiment(
        hypothesis=hypothesis,
        primary_metric="Retrieval_Relevance",
        guardrails=[],
        arm="/does/not/matter/for/this/test.json",
        sample_size=5,
        filed_by="test",
        path=tmp_path / "EXPERIMENTS.jsonl",
    )


def _ok_ablation(_pending):
    return {"verdict": "LOAD-BEARING", "evidence": "control 100% / ablated 20%", "guardrail_violated": False}


# ---------------------------------------------------------------------------
# Dark by default
# ---------------------------------------------------------------------------

def test_round_is_dark_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("EXPERIMENT_LANE_ENABLED", raising=False)
    out = cycle.run_round(**_paths(tmp_path))
    assert out["ran"] is False
    assert "dark by default" in out["reason"]


def test_force_bypasses_the_dark_switch(tmp_path, monkeypatch):
    monkeypatch.delenv("EXPERIMENT_LANE_ENABLED", raising=False)
    out = cycle.run_round(force=True, **_paths(tmp_path))
    assert out["ran"] is True


# ---------------------------------------------------------------------------
# Spacing guard
# ---------------------------------------------------------------------------

def test_spacing_guard_skips_a_recent_round(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPERIMENT_LANE_ENABLED", "true")
    paths = _paths(tmp_path)
    cycle.experiment_lineage.append_entry(
        {"round": 1, "experiment_id": "EXP-1", "decision": "ran", "reason": ""},
        paths["lineage_path"],
    )
    out = cycle.run_round(**paths)
    assert out["ran"] is False
    assert "spacing" in out["reason"]


def test_spacing_guard_skip_writes_nothing_to_the_lineage(tmp_path, monkeypatch):
    # The bug self_harness_cycle.py's own comment warns about: logging a spacing-skip
    # would reset the clock every check and the guard could never clear.
    monkeypatch.setenv("EXPERIMENT_LANE_ENABLED", "true")
    paths = _paths(tmp_path)
    cycle.experiment_lineage.append_entry(
        {"round": 1, "experiment_id": "EXP-1", "decision": "ran", "reason": ""},
        paths["lineage_path"],
    )
    before = list(cycle.experiment_lineage.iter_entries(paths["lineage_path"]))
    cycle.run_round(**paths)
    after = list(cycle.experiment_lineage.iter_entries(paths["lineage_path"]))
    assert after == before


def test_force_bypasses_the_spacing_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPERIMENT_LANE_ENABLED", "true")
    paths = _paths(tmp_path)
    cycle.experiment_lineage.append_entry(
        {"round": 1, "experiment_id": "EXP-1", "decision": "ran", "reason": ""},
        paths["lineage_path"],
    )
    out = cycle.run_round(force=True, **paths)
    assert out["ran"] is True
    assert "spacing" not in (out.get("reason") or "")


# ---------------------------------------------------------------------------
# No pending experiment
# ---------------------------------------------------------------------------

def test_no_pending_experiment_writes_a_skipped_no_candidate_row(tmp_path):
    paths = _paths(tmp_path)
    out = cycle.run_round(force=True, **paths)
    assert out["ran"] is True
    assert out["experiment_id"] is None
    assert "no pending experiment" in out["reason"]
    rows = list(cycle.experiment_lineage.iter_entries(paths["lineage_path"]))
    assert rows[0]["decision"] == "skipped_no_candidate"


# ---------------------------------------------------------------------------
# Full run-through-to-verdict, injected ablation function
# ---------------------------------------------------------------------------

def test_full_round_records_the_verdict_and_ledgers_it(tmp_path):
    paths = _paths(tmp_path)
    eid = _file_one(tmp_path)

    out = cycle.run_round(force=True, invoke_ablation_fn=_ok_ablation, **paths)

    assert out["ran"] is True
    assert out["experiment_id"] == eid
    assert out["verdict"] == "LOAD-BEARING"

    pending_after = experiments.next_pending(paths["experiments_path"])
    assert pending_after is None  # the experiment is no longer pending — verdict recorded

    lineage_rows = list(cycle.experiment_lineage.iter_entries(paths["lineage_path"]))
    assert lineage_rows[0]["decision"] == "ran"
    assert lineage_rows[0]["experiment_id"] == eid


def test_dry_run_writes_nothing_even_with_a_pending_experiment(tmp_path):
    paths = _paths(tmp_path)
    _file_one(tmp_path)

    out = cycle.run_round(dry_run=True, invoke_ablation_fn=_ok_ablation, **paths)

    assert out["ran"] is True
    assert not paths["lineage_path"].exists() or paths["lineage_path"].read_text() == ""
    # The experiment must still be pending — dry-run must not consume it.
    pending_after = experiments.next_pending(paths["experiments_path"])
    assert pending_after is not None


# ---------------------------------------------------------------------------
# Ablation invocation failure
# ---------------------------------------------------------------------------

def test_ablation_failure_logs_an_error_row_and_does_not_crash(tmp_path):
    paths = _paths(tmp_path)
    eid = _file_one(tmp_path)

    def _boom(_pending):
        raise RuntimeError("run_ablation.py exited 1: config not found")

    out = cycle.run_round(force=True, invoke_ablation_fn=_boom, **paths)

    assert out["ran"] is True
    assert out["experiment_id"] == eid
    assert "ablation invocation failed" in out["reason"]

    lineage_rows = list(cycle.experiment_lineage.iter_entries(paths["lineage_path"]))
    assert lineage_rows[0]["decision"] == "error"

    # The experiment must still be pending — a failed invocation must not consume it.
    pending_after = experiments.next_pending(paths["experiments_path"])
    assert pending_after is not None
    assert pending_after["experiment_id"] == eid
