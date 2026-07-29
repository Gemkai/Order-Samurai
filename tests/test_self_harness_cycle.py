"""Self-harness cycle: the gate must reject everything the papers and the video say it must.

Red cases that carry the design: a per-task regression hidden by an improved aggregate (the
video's third candidate), a proposal outside the declared surface, held-out leakage into the
proposer's evidence, and the dark-by-default switch. All runs use injected propose/run functions
— no model calls, no file writes outside tmp ledgers.
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin import self_harness_cycle as cycle  # type: ignore[import-not-found]


# --- suite-result factories --------------------------------------------------------------------

_HELD_IN = ["a1", "a2_miner", "b1"]
_HELD_OUT = ["a4_miner", "d3"]


def _suite_result(fractions: dict[str, float]) -> dict:
    split_of = {t: "held_in" for t in _HELD_IN} | {t: "held_out" for t in _HELD_OUT}
    results = {
        tid: {"fraction": f, "repeats": 1, "kind": "miner" if "miner" in tid else "guard",
              "group": "g", "split": split_of[tid]}
        for tid, f in fractions.items()
    }
    def mean(which):
        vals = [r["fraction"] for r in results.values() if r["split"] == which]
        return round(sum(vals) / len(vals), 3)
    return {
        "generated_at": "2026-07-16T00:00:00+00:00",
        "harness_fingerprint": "testfp123456",
        "results": results,
        "held_in_mean": mean("held_in"),
        "held_out_mean": mean("held_out"),
        "red_miners": [], "red_guards": [],
    }


BASELINE = _suite_result({"a1": 1.0, "a2_miner": 0.0, "b1": 1.0, "a4_miner": 0.0, "d3": 1.0})


# --- acceptance rule ---------------------------------------------------------------------------

def test_accepts_a_fix_that_greens_a_miner_without_touching_anything_else():
    candidate = _suite_result({"a1": 1.0, "a2_miner": 1.0, "b1": 1.0, "a4_miner": 1.0, "d3": 1.0})
    ok, why, deltas = cycle.acceptance(BASELINE, candidate)
    assert ok is True
    assert deltas["held_out_delta"] > 0


def test_rejects_a_per_task_regression_even_when_the_aggregate_improves():
    """THE video case: candidate 3 flipped one task always-pass -> mostly-failing while another
    task recovered by more — the aggregate rule alone would have accepted it."""
    candidate = _suite_result({"a1": 0.5, "a2_miner": 1.0, "b1": 1.0, "a4_miner": 1.0, "d3": 1.0})
    assert candidate["held_in_mean"] > BASELINE["held_in_mean"]  # aggregate says yes...
    ok, why, _ = cycle.acceptance(BASELINE, candidate)
    assert ok is False                                            # ...the gate says no
    assert "per-task regression" in why
    assert "a1" in why


def test_rejects_a_candidate_that_changes_nothing():
    ok, why, _ = cycle.acceptance(BASELINE, _suite_result(
        {"a1": 1.0, "a2_miner": 0.0, "b1": 1.0, "a4_miner": 0.0, "d3": 1.0}))
    assert ok is False
    assert "no improvement" in why


def test_rejects_a_candidate_whose_run_lost_a_task():
    candidate = _suite_result({"a1": 1.0, "a2_miner": 1.0, "b1": 1.0, "a4_miner": 1.0, "d3": 1.0})
    del candidate["results"]["d3"]
    ok, why, _ = cycle.acceptance(BASELINE, candidate)
    assert ok is False
    assert "missing task" in why


# --- structural rejection ----------------------------------------------------------------------

def _surface():
    return {"values": {
        "scout_max_files": {"value": 60, "type": "int", "min": 10, "max": 200},
        "loop_breaker_limit": {"value": 2, "type": "int", "min": 1, "max": 5},
    }}


def test_structurally_rejects_an_unknown_surface_key():
    reason = cycle.structural_check({"edits": {"graders_path": 1}}, _surface())
    assert reason is not None
    assert "outside the declared editable surface" in reason


def test_structurally_rejects_an_out_of_bounds_value():
    assert "declared max" in cycle.structural_check({"edits": {"scout_max_files": 900}}, _surface())
    assert "declared min" in cycle.structural_check({"edits": {"scout_max_files": 1}}, _surface())


def test_structurally_rejects_a_non_integer_value():
    assert "not an int" in cycle.structural_check({"edits": {"scout_max_files": "100"}}, _surface())
    assert "not an int" in cycle.structural_check({"edits": {"scout_max_files": True}}, _surface())


def test_structurally_rejects_an_empty_or_absent_edit_set():
    assert cycle.structural_check({"edits": {}}, _surface()) is not None
    assert cycle.structural_check({"audit": {}}, _surface()) is not None
    assert cycle.structural_check("not an object", _surface()) is not None


def test_structurally_rejects_a_no_op_edit():
    assert "equals the current value" in cycle.structural_check(
        {"edits": {"scout_max_files": 60}}, _surface())


def test_accepts_a_structurally_sound_candidate():
    assert cycle.structural_check({"edits": {"scout_max_files": 100}}, _surface()) is None


# --- evidence bundle: held-out never leaks ------------------------------------------------------

def test_evidence_bundle_never_mentions_held_out_tasks(tmp_path):
    real_baseline = json.loads(
        (REPO_ROOT / "state" / "harness_eval_baseline.json").read_text(encoding="utf-8"))
    split = json.loads((REPO_ROOT / "evalsuite" / "split.json").read_text(encoding="utf-8"))

    bundle = cycle.build_evidence_bundle(real_baseline, tmp_path / "ledger.jsonl")
    blob = json.dumps(bundle)
    for held_out_id in split["held_out"]:
        assert held_out_id not in blob, f"held-out task {held_out_id} leaked into the proposer bundle"


def test_evidence_bundle_includes_failing_held_in_miners(tmp_path):
    real_baseline = json.loads(
        (REPO_ROOT / "state" / "harness_eval_baseline.json").read_text(encoding="utf-8"))
    bundle = cycle.build_evidence_bundle(real_baseline, tmp_path / "ledger.jsonl")
    failing_ids = {t["task_id"] for t in bundle["failing_held_in_tasks"]}
    assert "a2_deep_retry_detected" in failing_ids


# --- the round, end to end (injected fns) -------------------------------------------------------

def _propose(edits_list):
    def fn(bundle):
        return [{"edits": e,
                 "audit": {"target_pattern": "bounded_scan", "expected_effect": "reach deeper",
                           "regression_risks": []},
                 "predicted_impact": {"expected_fixes": ["a2_miner"], "at_risk": []}}
                for e in edits_list]
    return fn


def _runner_for(mapping):
    """Suite runner keyed by the pinned OS_HARNESS_SCOUT_MAX_FILES value ('' = baseline)."""
    import os

    def fn():
        return mapping.get(os.environ.get("OS_HARNESS_SCOUT_MAX_FILES", ""), BASELINE)
    return fn


GOOD = _suite_result({"a1": 1.0, "a2_miner": 1.0, "b1": 1.0, "a4_miner": 1.0, "d3": 1.0})
BAD = _suite_result({"a1": 0.5, "a2_miner": 1.0, "b1": 1.0, "a4_miner": 1.0, "d3": 1.0})


def _fake_bundle(monkeypatch, failing=True):
    """The round tests use synthetic task ids the real split.json doesn't know, so the real
    bundle-builder would (correctly) find nothing held-in failing and end the round early.
    Pin the bundle instead — the bundle-builder has its own tests against the real baseline."""
    bundle = {
        "editable_surface": {},
        "failing_held_in_tasks": (
            [{"task_id": "a2_miner", "kind": "miner", "fraction": 0.0,
              "description": "deep retry missed", "grounded_in": "test",
              "candidate_surface_keys": ["scout_max_files"]}] if failing else []),
        "actionable_weakness_clusters": [],
        "passing_behaviours_to_preserve": [],
        "previously_attempted_edits": [],
    }
    monkeypatch.setattr(cycle, "build_evidence_bundle", lambda baseline, ledger_path=None: bundle)


def test_round_is_dark_by_default(monkeypatch):
    monkeypatch.delenv("SELF_HARNESS_ENABLED", raising=False)
    out = cycle.run_round(propose_fn=_propose([]), run_suite_fn=lambda: BASELINE)
    assert out["ran"] is False
    assert "dark by default" in out["reason"]


def test_round_accepts_the_good_candidate_and_ledgers_everything(tmp_path, monkeypatch):
    _fake_bundle(monkeypatch)
    ledger = tmp_path / "ledger.jsonl"
    out = cycle.run_round(
        propose_fn=_propose([{"scout_max_files": 100}, {"scout_max_files": 999999},
                             {"loop_breaker_limit": 2}]),
        run_suite_fn=_runner_for({"100": GOOD}),
        ledger_path=ledger, force=True,
    )

    assert out["winner_edits"] == {"scout_max_files": 100}
    assert len(out["structural_rejects"]) == 2  # out-of-bounds + no-op edit
    rows = list(cycle.harness_lineage.iter_entries(ledger))
    decisions = sorted(r["decision"] for r in rows)
    assert decisions == ["accepted", "structural_reject", "structural_reject"]


def test_round_rejects_the_aggregate_masked_regression(tmp_path, monkeypatch):
    _fake_bundle(monkeypatch)
    ledger = tmp_path / "ledger.jsonl"
    out = cycle.run_round(
        propose_fn=_propose([{"scout_max_files": 100}]),
        run_suite_fn=_runner_for({"100": BAD}),
        ledger_path=ledger, force=True,
    )
    assert out["accepted"] == []
    assert "winner_edits" not in out
    assert "per-task regression" in out["rejected"][0]["reason"]


def test_round_restores_the_environment_after_pinning(tmp_path, monkeypatch):
    _fake_bundle(monkeypatch)
    import os
    cycle.run_round(
        propose_fn=_propose([{"scout_max_files": 100}]),
        run_suite_fn=_runner_for({"100": GOOD}),
        ledger_path=tmp_path / "ledger.jsonl", force=True,
    )
    assert "OS_HARNESS_SCOUT_MAX_FILES" not in os.environ


def test_round_with_nothing_to_propose_exits_cleanly(tmp_path, monkeypatch):
    all_green = _suite_result({"a1": 1.0, "a2_miner": 1.0, "b1": 1.0, "a4_miner": 1.0, "d3": 1.0})
    _fake_bundle(monkeypatch, failing=False)
    called = []
    out = cycle.run_round(
        propose_fn=lambda b: called.append(1) or [],
        run_suite_fn=lambda: all_green,
        ledger_path=tmp_path / "ledger.jsonl", force=True,
    )
    assert out["candidates"] == 0
    assert "nothing to propose" in out["reason"]
    assert not called  # the proposer spawn (the expensive step) never happened


def test_round_spacing_guard_skips_a_recent_round(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.jsonl"
    cycle.harness_lineage.append_entry(
        {"round": 1, "candidate_id": "r1c1", "decision": "rejected", "reason": "x"}, ledger)
    monkeypatch.setenv("SELF_HARNESS_ENABLED", "true")
    monkeypatch.setattr(cycle, "budget_exhausted", lambda: False)

    out = cycle.run_round(propose_fn=_propose([]), run_suite_fn=lambda: BASELINE, ledger_path=ledger)
    assert out["ran"] is False
    assert "spacing" in out["reason"]


def test_budget_guard_skips_when_ledger_is_exhausted(tmp_path, monkeypatch):
    monkeypatch.setenv("SELF_HARNESS_ENABLED", "true")
    monkeypatch.setattr(cycle, "budget_exhausted", lambda: True)
    out = cycle.run_round(propose_fn=_propose([]), run_suite_fn=lambda: BASELINE,
                          ledger_path=tmp_path / "ledger.jsonl")
    assert out["ran"] is False
    assert "budget" in out["reason"]


def test_proposer_failure_ends_the_round_loudly(tmp_path, monkeypatch):
    """A proposer that cannot run must never read as 'the model proposed nothing' — observed
    2026-07-16: a nested spawn failing OAuth produced a silent zero-candidate round."""
    _fake_bundle(monkeypatch)

    def broken_proposer(bundle):
        raise RuntimeError("proposer CLI exited 1: Failed to authenticate")

    out = cycle.run_round(propose_fn=broken_proposer, run_suite_fn=lambda: BASELINE,
                          ledger_path=tmp_path / "ledger.jsonl", force=True)
    assert out["ran"] is True
    assert "authenticate" in out["proposer_error"]
    assert out["candidates"] == 0


def _shim_claude(tmp_path, monkeypatch, script_body: str):
    """Put a fake `claude` executable first on PATH to exercise the real subprocess path."""
    import os
    shim = tmp_path / "claude"
    shim.write_text(f"#!/bin/sh\n{script_body}\n", encoding="utf-8")
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")


def test_spawn_proposer_parses_a_real_subprocess_reply(tmp_path, monkeypatch):
    _shim_claude(tmp_path, monkeypatch,
                 """echo 'Here are my proposals:' && echo '[{"edits": {"scout_max_files": 100}}]'""")
    out = cycle.spawn_proposer({"failing_held_in_tasks": []}, k=3)
    assert out == [{"edits": {"scout_max_files": 100}}]


def test_spawn_proposer_raises_on_a_nonzero_exit(tmp_path, monkeypatch):
    import pytest
    _shim_claude(tmp_path, monkeypatch, "echo 'Failed to authenticate'; exit 1")
    with pytest.raises(RuntimeError, match="exited 1"):
        cycle.spawn_proposer({"failing_held_in_tasks": []})


def test_spawn_proposer_raises_on_prose_with_no_json(tmp_path, monkeypatch):
    import pytest
    _shim_claude(tmp_path, monkeypatch, "echo 'I think raising the limit would be wise.'")
    with pytest.raises(RuntimeError, match="no JSON array"):
        cycle.spawn_proposer({"failing_held_in_tasks": []})


def test_dry_run_writes_no_ledger_rows(tmp_path, monkeypatch):
    _fake_bundle(monkeypatch)
    ledger = tmp_path / "ledger.jsonl"
    out = cycle.run_round(
        propose_fn=_propose([{"scout_max_files": 100}]),
        run_suite_fn=_runner_for({"100": GOOD}),
        ledger_path=ledger, dry_run=True,
    )
    assert out["winner_edits"] == {"scout_max_files": 100}
    assert not ledger.exists()


# --- delivery idempotency ----------------------------------------------------------------------

def test_deliver_does_not_duplicate_an_identical_pending_proposal(tmp_path, monkeypatch):
    """A later round re-selecting the SAME winning edit must not append a second pending
    proposal — the id is a hash of the edits (34 copies of one edit accumulated before this)."""
    monkeypatch.setattr(cycle, "_HITL", tmp_path / "hitl_queue.json")
    monkeypatch.setattr(cycle, "_PROPOSED_DIR", tmp_path / "proposed")
    winner = {"edits": {"scout_max_files": 100}, "audit": {}}
    deltas = {"held_in_delta": 0.1, "held_out_delta": 0.1, "per_task": {"a1": (1.0, 1.0)}}

    first = cycle.deliver(winner, deltas, "r1c1", dry_run=False)
    second = cycle.deliver(winner, deltas, "r2c1", dry_run=False)

    assert first["id"] == second["id"]  # same edit -> same deterministic id
    queue = json.loads((tmp_path / "hitl_queue.json").read_text(encoding="utf-8"))
    matching = [i for i in queue["items"] if i["id"] == first["id"]]
    assert len(matching) == 1  # not duplicated


def test_deliver_re_proposes_after_the_prior_copy_is_resolved(tmp_path, monkeypatch):
    """The guard is scoped to PENDING: once a copy is rejected/expired, a fresh round may
    re-propose the same edit (mirrors the bushido enqueue idempotency convention)."""
    monkeypatch.setattr(cycle, "_HITL", tmp_path / "hitl_queue.json")
    monkeypatch.setattr(cycle, "_PROPOSED_DIR", tmp_path / "proposed")
    winner = {"edits": {"scout_max_files": 100}, "audit": {}}
    deltas = {"held_in_delta": 0.1, "held_out_delta": 0.1, "per_task": {"a1": (1.0, 1.0)}}

    first = cycle.deliver(winner, deltas, "r1c1", dry_run=False)
    qpath = tmp_path / "hitl_queue.json"
    q = json.loads(qpath.read_text(encoding="utf-8"))
    q["items"][0]["status"] = "expired"
    qpath.write_text(json.dumps(q), encoding="utf-8")

    cycle.deliver(winner, deltas, "r2c1", dry_run=False)
    q2 = json.loads(qpath.read_text(encoding="utf-8"))
    matching = [i for i in q2["items"] if i["id"] == first["id"]]
    assert len(matching) == 2  # one expired + one fresh pending
    assert sorted(i["status"] for i in matching) == ["expired", "pending"]
