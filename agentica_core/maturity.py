"""Maturity ladder — compute effective maturity + reflex_ready + mechanism_status per metric.

The grant decision (governance opt-in grant, plan 2026-06-17) ties autonomous
remediation to a deterministic mechanism passing N graded runs. This helper is
the single Python entry point that classifies a metric into one of:

  APPLY            mechanism graded RULE (>= _HIGH_IMPROVEMENT) over >= _MIN_RUNS,
                   OR seed default with no contrary grading signal       reflex_ready=true
  DRY-RUN-GRADED   mechanism present, has runs but < _MIN_RUNS (or neutral rate)
                   — runs read-only, logs, but not auto-ready             reflex_ready=false
  OBSERVE          no mechanism backs the metric, OR seed APPLY demoted
                   by a GOTCHA grade (< _LOW_IMPROVEMENT)                  reflex_ready=false

H2 (hardening item from the cross-model review): mechanism_status distinguishes
a CRASHED mechanism ("error") from an ungraded one — a broken bin/*.py must NOT
look the same as a healthy-but-young one to the operator. Pure-error detection
would require exit-code tracking; the pragmatic signal we have is "_MIN_RUNS
samples with zero improvements" (canary_fault_detect.py exiting 1 surfaces here
because it never moves the metric).

Inputs are loaded lazily: a missing/empty skill_efficacy.json degrades to the
seed behavior (every metric remains APPLY, reflex_ready=true).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import insights
from .reflex_eureka import (
    _MIN_RUNS,
    _LOW_IMPROVEMENT_THRESHOLD,
    _HIGH_IMPROVEMENT_THRESHOLD,
)

# state/skill_efficacy.json is written by agentica_core.skill_efficacy.compute(),
# called from refresh_dashboard.py with the canonical out_path under Order Samurai's
# state dir. Match that path so resolve_maturity reads the live grading file
# (refresh_dashboard.py:154). Override via ORDER_SAMURAI_ROOT env var for tests.
# Schema (per skill_efficacy.py):
#   { "<skill>": {
#       "total_runs":          int,
#       "success_count":       int,    # runs that moved the metric past threshold
#       "success_rate":        float | None,
#       "cooldown_multiplier": float,
#     }, ... }
_ORDER_SAMURAI_ROOT = Path(os.environ.get(
    "ORDER_SAMURAI_ROOT", str(Path(__file__).resolve().parents[1] / "Order Samurai")))
_SKILL_EFFICACY_PATH = _ORDER_SAMURAI_ROOT / "state" / "skill_efficacy.json"

# Maturity tiers.
APPLY = "APPLY"
DRY_RUN_GRADED = "DRY-RUN-GRADED"
OBSERVE = "OBSERVE"

# A skill-routed metric (no mechanism) with this many graded fires and ZERO
# genuine improvements demotes to OBSERVE: the remediation is proven unable to
# move the metric, so auto-firing it only burns ~20-min agent spawns (simplify
# was 0/20, consolidate-memory 0/12, humanizer 0/6 — 2026-07-26 audit W3).
# Counts come from skill_efficacy.json, which now grades on explicit `improved`
# verdicts only, so a demotion here is earned on real evidence. Recovery is
# automatic: land one genuine improvement (e.g. via a manual run after fixing
# the skill) and success_count > 0 restores the seed on the next refresh.
_SKILL_INEFFECTIVE_MIN_RUNS = 6

# Mechanism status — orthogonal to maturity. Surfaces *why* a mechanism is or isn't
# contributing to the grant decision. H2: "error" must never collapse into "ungraded".
MECH_NONE = "no_mechanism"
MECH_UNGRADED = "ungraded"
MECH_ERROR = "error"
MECH_RULE = "graded_rule"
MECH_GOTCHA = "graded_gotcha"
MECH_NEUTRAL = "graded_neutral"


def _load_skill_efficacy(path: Path = _SKILL_EFFICACY_PATH) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _stats_for(metric: str, cfg: dict, efficacy: dict) -> dict | None:
    """Pull a grading record. skill_efficacy.json is keyed by skill (one record per
    skill aggregates all its metrics); we also accept a metric-keyed override so
    per-pair grading can be added later without changing the consumer."""
    rec = efficacy.get(metric)
    if isinstance(rec, dict):
        return rec
    skill = cfg.get("skill")
    if not skill:
        return None
    mech = cfg.get("mechanism")
    if isinstance(mech, dict) and mech.get("read_only"):
        # A read-only-mechanism metric grades strictly on its mechanism's own exit-0
        # record ("<skill>::mechanism"), never blended with the retired LLM skill's
        # improved-based history. Absent record → None → UNGRADED → DRY-RUN-GRADED
        # (ready to earn its grade), not the skill's stale GOTCHA/ERROR.
        mrec = efficacy.get(f"{skill}::mechanism")
        return mrec if isinstance(mrec, dict) else None
    rec = efficacy.get(skill)
    return rec if isinstance(rec, dict) else None


def _classify_mechanism(cfg: dict, stats: dict | None) -> str:
    """Map grading stats to a mechanism_status. H2: surface 'error' independently
    from 'ungraded' so a broken mechanism never looks like a healthy-but-young one."""
    if "mechanism" not in cfg:
        return MECH_NONE
    if not stats:
        return MECH_UNGRADED
    total = int(stats.get("total_runs", 0) or 0)
    # skill_efficacy.py writes "success_count" + "success_rate"; legacy callers
    # may use "effective" / "rate". Accept either for forward compatibility.
    success = int(stats.get("success_count", stats.get("effective", 0)) or 0)
    rate = stats.get("success_rate", stats.get("rate"))
    if rate is None and total > 0:
        rate = success / total

    if total >= _MIN_RUNS and success == 0:
        # H2: enough samples to judge, every one failed to move the metric.
        # canary_fault_detect.py exiting 1 lands here because it never improves.
        return MECH_ERROR
    if total < _MIN_RUNS:
        return MECH_UNGRADED
    if rate is None:
        return MECH_UNGRADED
    if rate >= _HIGH_IMPROVEMENT_THRESHOLD:
        return MECH_RULE
    if rate < _LOW_IMPROVEMENT_THRESHOLD:
        return MECH_GOTCHA
    return MECH_NEUTRAL


def resolve_maturity(metric: str, *, metric_config: dict | None = None,
                     efficacy: dict | None = None) -> dict:
    """Resolve effective maturity + reflex_ready + mechanism_status for one metric.

    The seed on METRIC_CONFIG[metric]["maturity"] is the operator-set intent;
    this function may promote a seeded OBSERVE→APPLY when a mechanism earns a
    RULE grade, or demote a seeded APPLY→OBSERVE on a GOTCHA / ERROR grade.
    Neutral grades and missing data preserve the seed (Phase 1 default).
    """
    cfg_table = metric_config if metric_config is not None else insights.METRIC_CONFIG
    cfg = cfg_table.get(metric, {})
    seed = cfg.get("maturity", APPLY)
    efficacy = efficacy if efficacy is not None else _load_skill_efficacy()
    stats = _stats_for(metric, cfg, efficacy)
    mech_status = _classify_mechanism(cfg, stats)

    skill_ineffective = False
    if mech_status == MECH_GOTCHA:
        maturity = OBSERVE                     # demote: broken-in-practice mechanism
    elif mech_status == MECH_RULE:
        maturity = APPLY                       # promote any seed once the mechanism earns it
    elif mech_status == MECH_ERROR:
        maturity = OBSERVE                     # mechanism crashes — never auto-ready
    elif mech_status == MECH_UNGRADED and seed != APPLY:
        maturity = DRY_RUN_GRADED              # mechanism present, not yet proven
    else:
        maturity = seed                        # legacy + Phase-1 default path
        # Skill-only metrics (no mechanism) previously NEVER demoted on evidence —
        # a 0/20 skill kept auto-firing forever because only mechanism grades
        # could move the ladder. Zero genuine improvements over enough graded
        # fires now parks the metric at OBSERVE until the skill earns one.
        if mech_status == MECH_NONE and isinstance(stats, dict):
            total = int(stats.get("total_runs", 0) or 0)
            success = int(stats.get("success_count", stats.get("effective", 0)) or 0)
            if total >= _SKILL_INEFFECTIVE_MIN_RUNS and success == 0:
                maturity = OBSERVE
                skill_ineffective = True

    out = {
        "maturity": maturity,
        "reflex_ready": maturity == APPLY,
        "mechanism_status": mech_status,
    }
    if skill_ineffective:
        # Additive key: operator-facing reason for the demotion (why is this
        # OBSERVE when its seed says APPLY and it has no mechanism grade?).
        out["demoted_by"] = "skill_ineffective"
    return out
