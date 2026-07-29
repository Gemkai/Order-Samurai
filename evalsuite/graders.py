"""Graders for the harness eval suite — deterministic pass/fail, literal expectations.

THE COUPLING RULE (enforced by tests/test_harness_eval_runner.py::test_graders_never_read_the_
editable_surface): this module must never import `harness_config`. Expectations are literals
pinned the day the task was written. The Carbon Layer video's own first dry-run failed because a
test compared against the live editable value — every knob change then auto-passes its own test,
and the measuring stick is coupled to the thing being measured.

Knob sensitivity comes from the CONSUMERS the graders call (annotator, reducer, judge, replay
simulator), which read their knobs internally — including the OS_HARNESS_<KEY> env overrides the
self-harness cycle uses to pin candidate values.

Every grader: (workspace: Path) -> bool. Raise nothing on a normal miss — a False is a
measurement; an exception is a broken suite.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_GOV_ROOT = Path(__file__).resolve().parents[2]
if str(_GOV_ROOT) not in sys.path:
    sys.path.insert(0, str(_GOV_ROOT))
_OS_ROOT = Path(__file__).resolve().parents[1]
if str(_OS_ROOT) not in sys.path:
    sys.path.insert(0, str(_OS_ROOT))

from bin import tool_trust_annotator  # noqa: E402
from evalsuite import replay_sim  # noqa: E402


# --- Group A: bounded transcript scan (consumer: tool_trust_annotator) ------------------------

def _annotate(workspace: Path) -> dict:
    # max_files deliberately NOT passed: the annotator resolves it from the surface knob —
    # that resolution IS what group A measures.
    return tool_trust_annotator.run_annotator(projects_dir=workspace / "projects")


def check_a1_recent_retry_detected(workspace: Path) -> bool:
    return _annotate(workspace)["counts"]["retried_identical"] >= 1


def check_a2_deep_retry_detected(workspace: Path) -> bool:
    """MINER — red while the scan window is 60: the retry sits in the 75th-newest file."""
    return _annotate(workspace)["counts"]["retried_identical"] >= 1


def check_a3_no_false_retry(workspace: Path) -> bool:
    return _annotate(workspace)["counts"]["retried_identical"] == 0


def check_a4_deep_errored_detected(workspace: Path) -> bool:
    """MINER, held-out — same root cause as a2 (bounded scan), different symptom and depth."""
    return _annotate(workspace)["counts"]["errored_no_retry"] >= 1


# --- Group B: judge pipeline (consumer: evals.judge, live local model) -------------------------

def check_b1_judge_returns_valid_label(workspace: Path) -> bool:
    """The classifier pipeline must produce a usable judgment under the candidate's token budget.
    Pass = a real (non-gap) score with a label from the declared set; the *correctness* of the
    label is the model's business, the *usability* of the judgment is the harness's."""
    from agentica_core.evals.judge import ClassifierJudge

    fixture = json.loads((workspace / "judge_input.json").read_text(encoding="utf-8"))
    judge = ClassifierJudge(
        name="eval_b1",
        template="QUESTION: {input}\n\nANSWER GIVEN: {output}\n\nIs the answer arithmetically correct?",
        labels=["correct", "incorrect"],
        label_scores={"correct": 1.0, "incorrect": 0.0},
        direction="maximize",
    )
    score = judge.evaluate(input=fixture["input"], output=fixture["output"])
    return (not score.is_gap) and score.label in ("correct", "incorrect")


# --- Group C: cliff reducer (consumer: aggregate.r_context_cliff_events) ----------------------

def _count_cliffs(workspace: Path) -> int:
    """Point the reducer's home-dir discovery at the fixture tree for the duration of the call."""
    from agentica_core import aggregate

    prev = os.environ.get("USERPROFILE")
    os.environ["USERPROFILE"] = str(workspace)
    try:
        n = aggregate.r_context_cliff_events([])
    finally:
        if prev is None:
            os.environ.pop("USERPROFILE", None)
        else:
            os.environ["USERPROFILE"] = prev
    return -1 if n is None else n


def check_c1_counts_heavy_sessions(workspace: Path) -> bool:
    """3 sessions above 140k, 2 healthy below. Pinned literal: exactly 3. A threshold raised past
    150k undercounts; dropped below 100k overcounts — both directions fail this task."""
    return _count_cliffs(workspace) == 3


def check_c2_ignores_healthy_sessions(workspace: Path) -> bool:
    """Held-out: sessions at 100–120k are the healthy band; zero cliffs expected."""
    return _count_cliffs(workspace) == 0


# --- Group D: loop-breaker replay (consumer: replay_sim) --------------------------------------

def check_d1_parks_permanent_failure(workspace: Path) -> bool:  # noqa: ARG001
    """A permanently erroring skill must be parked within 4 spawns over a day — the waste bound.
    Raising loop_breaker_limit to 5 spends 5 runs on a dead skill and fails this."""
    r = replay_sim.simulate(["error"], duration_minutes=24 * 60)
    return r.parked and r.spawns <= 4


def check_d2_recovers_after_single_transient_error(workspace: Path) -> bool:  # noqa: ARG001
    """One transient error then success. Dropping loop_breaker_limit to 1 parks the reflex before
    its recovery attempt — the reason cooldown arming says a transient error must not lock out."""
    r = replay_sim.simulate(["error", "improved"], duration_minutes=24 * 60)
    return r.recovered


def check_d3_survives_quota_window(workspace: Path) -> bool:  # noqa: ARG001
    """Three quota-classified incompletes then success — the documented 2026-07-13 quota shape.
    incomplete_limit below 4 parks the skill during the dry window and fails this."""
    r = replay_sim.simulate(["quota", "quota", "quota", "improved"], duration_minutes=24 * 60)
    return r.recovered


def check_d4_bounds_daily_thrash(workspace: Path) -> bool:  # noqa: ARG001
    """A metric that re-degrades after every fix refires forever; cooldown is the only spend
    brake. Pinned literal: ≤ 50 spawns/day (true at 30-minute cooldown; 10 minutes → ~145)."""
    r = replay_sim.simulate(["improved"], duration_minutes=24 * 60, re_degrade=True)
    return r.spawns <= 50
