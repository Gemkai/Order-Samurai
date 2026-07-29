"""Eval-suite integrity: the yardstick itself must be ungameable and uncoupled.

Until a suite has been checked this hard it isn't a yardstick, it's a guess (the video's rule).
The load-bearing properties: graders never read the editable surface, a tampered seed fails
outright, the replay simulator mirrors the engine's contract, and the split never leaks.
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin import harness_eval_runner as runner  # type: ignore[import-not-found]
from evalsuite import replay_sim  # type: ignore[import-not-found]

SUITE = REPO_ROOT / "evalsuite"


# --- coupling audit (the video's dry-run bug) --------------------------------------------------

def test_graders_never_read_the_editable_surface():
    """A grader comparing against the live editable value auto-passes every knob change —
    the measuring stick must not be coupled to the thing it measures. Checked via the import
    graph (an AST walk), not a text grep: the docstring legitimately EXPLAINS this rule."""
    import ast

    tree = ast.parse((SUITE / "graders.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(a.name for a in node.names)
    assert not any("harness_config" in name for name in imported), imported


def test_task_definitions_carry_no_dynamic_references():
    for p in (SUITE / "tasks").glob("*.json"):
        raw = p.read_text(encoding="utf-8")
        assert "harness_config" not in raw
        assert "get_value" not in raw


# --- suite shape -------------------------------------------------------------------------------

def _tasks():
    return {json.loads(p.read_text())["id"]: json.loads(p.read_text()) for p in (SUITE / "tasks").glob("*.json")}


def _split():
    return json.loads((SUITE / "split.json").read_text(encoding="utf-8"))


def test_every_task_is_assigned_to_exactly_one_split():
    tasks, split = _tasks(), _split()
    held_in, held_out = set(split["held_in"]), set(split["held_out"])
    assert not held_in & held_out
    assert held_in | held_out == set(tasks)


def test_every_task_declares_a_knob_it_is_sensitive_to():
    """An insensitive task can never fail under any proposal — a spectator, not a gate."""
    surface_keys = {
        "reflex_cooldown_minutes", "loop_breaker_limit", "incomplete_limit",
        "scout_max_files", "judge_max_tokens", "context_cliff_token_threshold",
    }
    for tid, task in _tasks().items():
        declared = set(task["sensitive_to"])
        assert declared, f"{tid} declares no knob sensitivity"
        assert declared <= surface_keys, f"{tid} names an undeclared knob: {declared - surface_keys}"


def test_the_held_out_split_contains_a_miner():
    """The A2/A4 structure: a hidden task sharing the held-in miner's root cause is the only
    thing distinguishing a real fix from one that memorised the shown failure."""
    tasks, split = _tasks(), _split()
    assert any(tasks[tid]["kind"] == "miner" for tid in split["held_out"])


def test_every_task_is_grounded_in_something_real():
    for tid, task in _tasks().items():
        assert task.get("grounded_in", "").strip(), f"{tid} is an invented task"


# --- fingerprint tamper detection ---------------------------------------------------------------

def test_a_grader_that_edits_its_own_seed_fails_the_task(monkeypatch):
    task = {
        "id": "tamper_probe", "kind": "guard", "group": "probe",
        "fixture": "a3_all_clean", "grader": "graders.check_a3_no_false_retry", "repeats": 1,
    }

    from evalsuite import graders

    def cheating_grader(workspace):
        seed = next((workspace / "projects").rglob("*.jsonl"))
        seed.write_text('{"type": "user", "message": {"content": "answer key edited"}}\n', encoding="utf-8")
        return True  # claims success

    monkeypatch.setattr(graders, "check_a3_no_false_retry", cheating_grader)
    passed, note = runner.run_task_once(task)

    assert passed is False
    assert "seed_tampered" in note


def test_a_crashing_grader_fails_with_a_surfaced_reason(monkeypatch):
    from evalsuite import graders

    def broken(workspace):
        raise RuntimeError("suite defect")

    monkeypatch.setattr(graders, "check_a3_no_false_retry", broken)
    task = {
        "id": "crash_probe", "kind": "guard", "group": "probe",
        "fixture": "a3_all_clean", "grader": "graders.check_a3_no_false_retry", "repeats": 1,
    }
    passed, note = runner.run_task_once(task)
    assert passed is False
    assert "grader_error" in note


# --- replay simulator: the mirrored engine contract --------------------------------------------

def test_replay_parks_a_permanent_failure_at_the_hard_limit(monkeypatch):
    monkeypatch.setenv("OS_HARNESS_LOOP_BREAKER_LIMIT", "2")
    r = replay_sim.simulate(["error"], duration_minutes=24 * 60)
    assert r.parked is True
    assert r.spawns == 2


def test_replay_lets_a_transient_error_recover_under_the_default_limit(monkeypatch):
    monkeypatch.setenv("OS_HARNESS_LOOP_BREAKER_LIMIT", "2")
    r = replay_sim.simulate(["error", "improved"], duration_minutes=24 * 60)
    assert r.recovered is True
    assert r.parked is False


def test_replay_improvement_resets_the_hard_counter(monkeypatch):
    monkeypatch.setenv("OS_HARNESS_LOOP_BREAKER_LIMIT", "2")
    # error, improve, error, improve... never two consecutive -> never parked.
    r = replay_sim.simulate(["error", "improved", "error", "improved"],
                            duration_minutes=24 * 60, re_degrade=True)
    assert r.parked is False


def test_replay_counts_incompletes_against_the_lenient_budget(monkeypatch):
    monkeypatch.setenv("OS_HARNESS_INCOMPLETE_LIMIT", "4")
    r = replay_sim.simulate(["quota"], duration_minutes=24 * 60)
    assert r.parked is True
    assert r.spawns == 4  # more tolerant than the hard limit's 2


def test_replay_cooldown_paces_a_thrashing_reflex(monkeypatch):
    monkeypatch.setenv("OS_HARNESS_REFLEX_COOLDOWN_MINUTES", "30")
    r = replay_sim.simulate(["improved"], duration_minutes=24 * 60, re_degrade=True)
    assert 40 <= r.spawns <= 50  # ~24h / 30min

    monkeypatch.setenv("OS_HARNESS_REFLEX_COOLDOWN_MINUTES", "240")
    slow = replay_sim.simulate(["improved"], duration_minutes=24 * 60, re_degrade=True)
    assert slow.spawns < r.spawns


def test_replay_rejects_an_unknown_outcome():
    import pytest
    with pytest.raises(ValueError):
        replay_sim.simulate(["exploded"], duration_minutes=60)


# --- fingerprint provenance ---------------------------------------------------------------------

def test_run_suite_stamps_the_effective_surface_for_a_pinned_run(monkeypatch):
    """A candidate run pins its edits via OS_HARNESS_* overrides; the stamped
    harness_fingerprint must reflect that effective surface, not the baseline file.
    task_filter=set() skips every task, so this only exercises the stamp."""
    monkeypatch.delenv("OS_HARNESS_REFLEX_COOLDOWN_MINUTES", raising=False)
    baseline_fp = runner.run_suite(task_filter=set())["harness_fingerprint"]

    monkeypatch.setenv("OS_HARNESS_REFLEX_COOLDOWN_MINUTES", "45")  # in-bounds, != declared 30
    pinned_fp = runner.run_suite(task_filter=set())["harness_fingerprint"]

    assert pinned_fp != baseline_fp
