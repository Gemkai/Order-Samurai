"""Identify remediation skills that failed to improve their target metrics.

Reads exec_log.jsonl and reflex_engine_state.json to surface:
  - Skills currently stuck (loop-breaker fired after LOOP_BREAKER_LIMIT failures)
  - Per-entry run history, impact rate, and human-readable recommendations

Output is injected into payload["remediation_efficacy"]["stuck_remediations"]
by refresh_dashboard.py so the dashboard can render it as an actionable report.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import TypedDict

# Must match LOOP_BREAKER_LIMIT in reflex-engine.ts
LOOP_BREAKER_LIMIT = 2

# ── Failure mode classification ───────────────────────────────────────────────
# Explains WHY a skill isn't moving its metric — not just that it isn't.
# This guides the operator toward the correct type of intervention.
#
# Classes:
#   audit_only   — skill diagnoses/reports but the fix requires human action
#   accumulation — skill improves but new instances regenerate between runs
#   behavioral   — metric depends on human workflow choices no skill can change
#   auto_fixable — skill modifies code/state directly; if stuck, investigate root cause

FAILURE_MODES: dict[str, str] = {
    "audit_only":   "Skill diagnoses and reports — the actual fix requires a human action or sprint.",
    "accumulation": "Skill improves the metric, but new instances regenerate between runs faster than the skill can resolve them.",
    "behavioral":   "Metric reflects human workflow choices that no skill can change autonomously.",
    "auto_fixable": "Skill modifies code or config directly. If stuck, the root cause may have changed or the skill needs updating.",
}

# Primary failure mode per skill name (same skill may have different modes per metric —
# see SKILL_METRIC_FAILURE_MODE for overrides).
SKILL_FAILURE_MODE: dict[str, str] = {
    # Audit-only: generate a report; human must act on it
    "audit-mechanisms":          "audit_only",
    "codebase-cleanup-deps-audit": "audit_only",
    "subagent-audit":            "audit_only",
    "governance-review":         "audit_only",
    "security-audit":            "audit_only",
    "cost-breakdown-audit":      "audit_only",
    "investigate":               "audit_only",
    "policy-enforcement-audit":  "audit_only",
    "supply-chain-risk-auditor": "audit_only",
    "tool-diversity-audit":      "audit_only",
    "skill-consolidator":        "audit_only",
    # Accumulation: improvement outpaced by regeneration
    "humanizer":                 "accumulation",
    "wiki":                      "accumulation",
    # Behavioral: metric measures human choices
    "model-selector":            "behavioral",
    "insights":                  "behavioral",
}

# Per-(skill, metric) overrides — takes precedence over SKILL_FAILURE_MODE.
SKILL_METRIC_FAILURE_MODE: dict[tuple[str, str], str] = {
    # /simplify improves code verbosity but Rework_Loops is a workflow problem
    ("simplify", "Rework_Loops"):    "behavioral",
    # /simplify directly fixes these
    ("simplify", "Simplify_Age"):    "auto_fixable",
    ("simplify", "Revision_Ratio"):  "auto_fixable",
}


def get_failure_mode(skill: str, metric: str) -> str:
    """Return the failure mode class for a (skill, metric) pair."""
    override = SKILL_METRIC_FAILURE_MODE.get((skill, metric))
    if override:
        return override
    return SKILL_FAILURE_MODE.get(skill, "auto_fixable")


# ── Recommendation library ────────────────────────────────────────────────────
# Lookup order: RECOMMENDATIONS[skill][metric_name]
#            → RECOMMENDATIONS[skill]["default"]
#            → generic fallback
# Keep entries factual and actionable — no padding.

RECOMMENDATIONS: dict[str, dict[str, str]] = {
    "simplify": {
        "Rework_Loops": (
            "Rework loops reflect repeated revision cycles — typically caused by unclear "
            "requirements or scope drift, not code verbosity. /simplify addresses surface "
            "quality but not workflow root causes. Review recent sessions for ambiguous "
            "task handoffs or goal changes mid-session."
        ),
        "default": (
            "Code simplification did not move this metric. The issue may be architectural "
            "(deep call stacks, over-abstracted modules) rather than surface verbosity. "
            "Review the highest-churn files manually."
        ),
    },
    "wiki": {
        "Doc_Parity_Issues": (
            "Doc parity issues include both stale and entirely missing docs. /wiki can "
            "update existing content but cannot generate domain documentation from scratch. "
            "Audit which modules have zero coverage before re-running — different remediation "
            "is needed for 'missing' vs. 'outdated'."
        ),
        "default": (
            "Documentation updates did not close the parity gap. Identify whether the issues "
            "are missing files vs. stale files — different approaches apply to each."
        ),
    },
    "humanizer": {
        "Slop_Density": (
            "Marginal improvements suggest the skill is working but new slop accumulates as "
            "fast as it is cleaned. Address slop at the source: review /anti-slop nudge "
            "frequency and CLAUDE.md anti-slop rules to reduce generation rate."
        ),
        "default": (
            "Anti-slop pass did not reduce density. Content may require targeted rewriting "
            "at the session level rather than a global sweep."
        ),
    },
    "model-selector": {
        "Local_Routing_Share": (
            "Local routing share depends on which tasks naturally suit local models. If most "
            "work requires frontier reasoning, this metric stays low regardless of routing "
            "guidance. Consider whether the warn/fail threshold in METRIC_CONFIG reflects a "
            "realistic ceiling for this project's task mix."
        ),
        "default": (
            "Model routing recommendations were not adopted or did not change the measured "
            "ratio. Check whether sessions where /model-selector ran are representative of "
            "typical usage, or whether the metric covers contexts the skill cannot influence."
        ),
    },
    "codebase-cleanup-deps-audit": {
        "Deprecated_Deps": (
            "Dependency audits identify issues but cannot auto-resolve them — most updates "
            "require manual testing and compatibility verification. Recommended: "
            "(1) batch-update semver-compatible patch/minor versions first, "
            "(2) tackle breaking major-version changes individually, "
            "(3) a dedicated dependency update sprint is more effective than autonomous retries."
        ),
        "default": (
            "Dependency cleanup did not improve the metric. Automated fixes are likely blocked "
            "by breaking changes or missing test coverage. Manual follow-through is required."
        ),
    },
    "insights": {
        "Frustration_Signals": (
            "Running /insights in response to frustration identifies patterns but does not fix "
            "the root cause. Frustration typically signals workflow friction: ambiguous handoffs, "
            "overly long sessions, or unclear scope. Review the flagged sessions manually and "
            "adjust the upstream workflow — not the downstream analysis."
        ),
        "default": (
            "/insights surfaces patterns but does not change behavior. Follow-up action by a "
            "human or a more targeted skill is required to move this metric."
        ),
    },
    "subagent-audit": {
        "Chain_Depth_Avg": (
            "Chain depth may be inherent to the task structure. If complex orchestration "
            "requires deep nesting, reducing it would break functionality. Verify whether "
            "flagged depths reflect over-nesting or complex-but-correct workflows before "
            "treating reduction as the goal."
        ),
        "default": (
            "Subagent audit did not improve chain metrics. The depth may be load-bearing — "
            "verify whether reducing it would break orchestration patterns."
        ),
    },
    "audit-mechanisms": {
        "Mechanism_Orphans": (
            "Mechanism orphans may be classified as unused but triggered indirectly (schedule, "
            "WS event, HTTP callback). Verify the orphan detection covers all mechanism types "
            "before treating entries as safe to remove."
        ),
        "default": (
            "Mechanism audit found orphans but the metric did not move. Some orphans may be "
            "intentionally dormant. Manual classification is needed before cleanup."
        ),
    },
    "skill-consolidator": {
        "Skill_Conflicts": (
            "Skill conflicts require careful manual review — automated merging risks breaking "
            "active workflows. Review the conflict list and consolidate only clearly duplicate "
            "skills with no active users."
        ),
        "default": (
            "Skill consolidation did not reduce conflicts. Some detected conflicts may be "
            "intentional variations rather than true duplicates."
        ),
    },
    "governance-review": {
        "Governance_Review_Findings": (
            "Governance code findings persisted after review. Critical findings may require "
            "architectural changes that the skill can identify but not auto-fix. Review the "
            "GOVERNANCE_REVIEW.md output and address CRITICAL findings manually."
        ),
        "default": (
            "Governance review did not clear findings. Review docs/GOVERNANCE_REVIEW.md for "
            "specific issues that require manual fixes."
        ),
    },
}


def get_recommendation(skill: str, metric_name: str) -> str:
    skill_rec = RECOMMENDATIONS.get(skill, {})
    specific = skill_rec.get(metric_name)
    if specific:
        return specific
    default = skill_rec.get("default")
    if default:
        return default
    return (
        f"/{skill} did not improve {metric_name} after {LOOP_BREAKER_LIMIT} attempts. "
        f"The metric may require a different remediation approach or manual investigation."
    )


# ── Types ─────────────────────────────────────────────────────────────────────

class StuckRemediation(TypedDict):
    reflex_id: str
    pillar: str
    metric: str
    skill: str
    command: str
    runs_attempted: int
    improved_count: int
    impact_rate: float | None       # None when 0 runs recorded in exec_log
    last_run_at: str | None
    last_status: str | None         # "done" | "error" | "timeout"
    failure_mode: str               # "audit_only" | "accumulation" | "behavioral" | "auto_fixable"
    recommendation: str
    unstick_endpoint: str


# ── Main entry point ──────────────────────────────────────────────────────────

def analyze(log_path: Path, state_path: Path) -> list[StuckRemediation]:
    """Return stuck remediation entries with per-entry recommendations.

    Args:
        log_path:   exec_log.jsonl  (ORDER_SAMURAI_ROOT/state/)
        state_path: reflex_engine_state.json  (ORDER_SAMURAI_ROOT/state/)

    Returns empty list when state file is absent (server not yet run) or
    when no stuck entries exist.
    """
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    no_improvement: dict[str, dict] = state.get("noImprovement", {})
    stuck_keys = {k for k, v in no_improvement.items() if v.get("stuck")}
    if not stuck_keys:
        return []

    # Build run history from exec_log — keyed by "reflex_id::command"
    run_history: dict[str, list[dict]] = defaultdict(list)
    if log_path.exists():
        try:
            for line in log_path.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                    reflex_id = r.get("reflex_id")
                    command = r.get("command", "")
                    if not reflex_id:
                        continue
                    run_history[f"{reflex_id}::{command}"].append(r)
                except (json.JSONDecodeError, TypeError):
                    pass
        except OSError:
            pass

    results: list[StuckRemediation] = []
    for key in sorted(stuck_keys):
        # key format: "reflex_id::command"
        parts = key.split("::", 1)
        if len(parts) != 2:
            continue
        reflex_id, command = parts[0], parts[1]

        # reflex_id format: "metric:pillar:MetricName" or "trajectory:pillar:MetricName"
        id_parts = reflex_id.split(":")
        if len(id_parts) < 3:
            continue
        pillar = id_parts[1]
        metric = id_parts[2]
        skill = command.lstrip("/").split()[0] if command else "unknown"

        runs = run_history.get(key, [])
        runs_attempted = len(runs)
        improved_count = sum(1 for r in runs if r.get("improved") is True)
        impact_rate = round(improved_count / runs_attempted, 3) if runs_attempted else None
        last_run = runs[-1] if runs else None

        # Bushido HITL queue (Phase 3.2): replaces the legacy needs_human_*.md
        # ticket. We push the stuck remediation into ORDER_SAMURAI_ROOT/state/hitl_queue.json
        # by invoking bin/bushido_check.py — the Bushido decision module dedups,
        # writes atomically, and is read by the TS Reflex Engine + .mex TUI.
        #
        # ORDER_SAMURAI_ROOT is inferred from log_path: log_path.parent is .../state/,
        # so log_path.parent.parent is the repo root.
        order_samurai_root = log_path.parent.parent
        bushido_check = order_samurai_root / "bin" / "bushido_check.py"
        python_bin = sys.executable or ("python" if os.name == "nt" else "python3")
        if bushido_check.exists():
            ctx_lines = [
                f"Failure mode: {get_failure_mode(skill, metric)}",
                f"Runs attempted: {runs_attempted}",
                f"Impact rate: {impact_rate}",
                f"Recommendation: {get_recommendation(skill, metric)}",
            ]
            context = " | ".join(ctx_lines)
            try:
                subprocess.run(
                    [
                        python_bin, str(bushido_check),
                        "--skill", skill,
                        "--pillar", pillar,
                        "--metric", reflex_id,
                        "--source", "reflex",
                        "--stuck",
                        "--consecutive", str(runs_attempted),
                        "--command", command,
                        "--context", context,
                    ],
                    cwd=str(order_samurai_root),
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                    capture_output=True,
                    timeout=10,
                    check=False,  # exit 1 = QUEUE/HITL (normal); exit 2 = HARD_STOP; exit 3 = error
                )
            except (OSError, subprocess.SubprocessError):
                # Non-fatal — dashboard refresh must never fail because of HITL push.
                pass

        results.append(StuckRemediation(
            reflex_id=reflex_id,
            pillar=pillar,
            metric=metric,
            skill=skill,
            command=command,
            runs_attempted=runs_attempted,
            improved_count=improved_count,
            impact_rate=impact_rate,
            last_run_at=last_run.get("timestamp") if last_run else None,
            last_status=last_run.get("status") if last_run else None,
            failure_mode=get_failure_mode(skill, metric),
            recommendation=get_recommendation(skill, metric),
            unstick_endpoint=f"POST /api/reflex/unstick/{reflex_id}",
        ))

    return results
