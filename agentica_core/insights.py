"""Scores, summaries, remediation, and trend history — the analytical layer the dashboard needs,
mirroring Jarvis's category_scores / summaries / mitigation / history. Pure, data-driven (no LLM).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_THIS = Path(__file__).resolve()
_local_root = _THIS.parents[1]
if (_local_root / "config").exists() and not (_local_root / "Order Samurai").exists():
    _default_root = _local_root
else:
    _default_root = _local_root / "Order Samurai"
_ORDER_SAMURAI_ROOT = Path(os.environ.get("ORDER_SAMURAI_ROOT", str(_default_root)))

# Single source: skill/command (all metrics) + dir/warn/fail (graded only) + per (rate metrics).
# Gate_Fires is protective-activity: shown but NOT graded (no dir key).
METRIC_CONFIG: dict[str, dict] = {
    # Bow — operational
    "Error_Rate":               {"skill": "investigate",                  "command": "/investigate",                         "dir": "lower",  "warn": 2,     "fail": 5,   "readonly": True, "weight": 3.0, "maturity": "DRY-RUN-GRADED", "mechanism": {"script": "error_triage.py", "args": [], "read_only": True, "timeout_s": 120}},
    "Latency_P50":              {"skill": "investigate",                  "command": "/investigate",                         "dir": "lower",  "warn": 30000, "fail": 60000,  "readonly": True, "weight": 1.0},
    "Latency_P95":              {"skill": "investigate",                  "command": "/investigate",                         "dir": "lower",  "warn": 90000, "fail": 180000, "readonly": True, "weight": 1.0},
    "Complexity_Weighted_Throughput": {"skill": "insights",               "command": "/insights",                                                                          "readonly": True},
    "Tool_Calls":               {"skill": "tool-diversity-audit",         "command": "/tool-diversity-audit",                                                              "readonly": True},
    "Fallback_Recovery_Rate":   {"skill": "model-selector",               "command": "/model-selector",                      "dir": "higher", "warn": 90,    "fail": 80,  "auto_remediable": False, "weight": 2.0},
    "Session_Count":            {},  # informational — no remediation action makes sense
    "Avg_Session_Turns":        {"skill": "insights",                     "command": "/insights",                            "dir": "lower",  "warn": 8,     "fail": 15,  "readonly": True, "auto_remediable": False, "calibrate": False, "weight": 1.0},  # SENSEI-1: /insights 8x no_change -> advisory; bimodal history (autonomous vs interactive) -> never percentile-calibrate
    "Processes_Reaped":         {},  # protective activity — high count means guard is working correctly
    "Agent_Autonomy_Ratio":     {"skill": "sensei-cycle",                 "command": "/sensei-cycle",                        "dir": "higher", "warn": 70,    "fail": 50, "auto_remediable": False, "weight": 2.0},  # mis-route (DO-NOT-USE): sensei-cycle is circular (its own runs are the numerator) — advisory only, never auto-fire
    "Agent_Process_Count":      {"skill": "self-heal",                    "command": "/self-heal", "auto_remediable": False},  # mis-route (DO-NOT-USE): self-heal kills procs by age (no allowlist/dry-run) — would target the governance stack; never auto-fire
    "Mechanism_Orphans":        {"skill": "audit-mechanisms",             "command": "/audit-mechanisms",                    "dir": "lower",  "warn": 1,     "fail": 3, "weight": 1.0},
    "Governance_Pass_Rate":     {"skill": "runtime-refactor-hardening",   "command": "/runtime-refactor-hardening",          "dir": "higher", "warn": 85,    "fail": 70, "weight": 2.0},
    "Verifier_Failures":        {"skill": "runtime-refactor-hardening",   "command": "/runtime-refactor-hardening"},
    # Sword — security
    "Vulnerability_MTTR":       {"skill": "pip-safe-upgrade",             "command": "/pip-safe-upgrade",                    "dir": "lower",  "warn": 2.0,   "fail": 5.0, "weight": 2.0},
    "Boundary_Violations":      {"skill": "guard",                        "command": "/guard",                               "dir": "lower",  "warn": 1,     "fail": 3, "auto_remediable": False, "weight": 3.0},  # mis-route (DO-NOT-USE): guard is a preventive session toggle, not a remediator — can't fix existing violations; advisory only (real fix = a quarantine bin, not yet built)
    "Secrets_Detected":         {"skill": "security-audit",               "command": "/security-audit",                      "dir": "lower",  "warn": 1,     "fail": 1, "weight": 3.0, "maturity": "DRY-RUN-GRADED", "mechanism": {"script": "secret_scrub.py", "args": [], "read_only": True, "timeout_s": 120}},
    # Guardrail_Blocks RETIRED 2026-07-19 (dead emitter — no security_gate_log.jsonl writer on this host).
    "Rule_Violations":          {"skill": "policy-enforcement-audit",     "command": "/policy-enforcement-audit",            "dir": "lower",  "warn": 1,     "fail": 5,  "per": "session", "readonly": True, "weight": 2.0, "maturity": "DRY-RUN-GRADED", "mechanism": {"script": "policy_enforcement_audit.py", "args": [], "read_only": True, "timeout_s": 120}},
    # Canary_Failures reads canary_status.json (behavioral_canary.py), NOT security_gate_canary.json.
    # /canary-fault-diagnosis + canary_fault_detect.py only inspect the security-gate canary, so they
    # could never move this metric — that misrouting caused a permanent stuck loop -> false needs_human
    # escalation. A behavioral skill regression has no cheap deterministic auto-fix; re-run the canary to
    # re-measure, then investigate the regressed skill. Non-remediable, like other detect-only metrics.
    "Canary_Failures":          {"skill": "behavioral-canary",            "command": "python ~/.claude/scripts/behavioral_canary.py", "dir": "lower", "warn": 0, "fail": 1, "auto_remediable": False, "weight": 3.0},
    "Gate_Canary_Fault":        {"skill": "canary-fault-diagnosis",       "command": "/canary-fault-diagnosis",              "readonly": True, "mechanism": {"script": "canary_fault_detect.py", "args": [], "read_only": True, "timeout_s": 120}},
    # Loop_Breaker_Fires RETIRED 2026-07-19 (metric-surface review Part E item 3):
    # ~/.claude/data/loop_breaker_state.json is never written on this host, so the
    # graded weight-2 metric was permanently dark (no envelope ever built) —
    # removal, never faking. Re-add only together with a live emitter.
    "Security_Scorecard":       {"skill": "security-audit",               "command": "/security-audit",                      "dir": "higher", "warn": 85,    "fail": 70, "weight": 2.0},
    "Skill_Safety_Findings":    {"skill": "supply-chain-risk-auditor",    "command": "/supply-chain-risk-auditor",            "dir": "lower",  "warn": 1,     "fail": 5,   "readonly": True, "auto_remediable": False, "weight": 2.0},  # mis-route (DO-NOT-USE): supply-chain-risk-auditor audits dep packages, not installed skills — can't move this metric; advisory only (real fix = a skill-quarantine bin, not yet built)
    "Deprecated_Deps":          {"skill": "pip-safe-upgrade",             "command": "/pip-safe-upgrade",                    "dir": "lower",  "warn": 20,    "fail": 120, "weight": 1.0, "maturity": "DRY-RUN-GRADED", "mechanism": {"script": "codebase_deps_audit.py", "args": [], "read_only": True, "timeout_s": 120}},
    "Governance_Review_Findings": {"skill": "governance-review",          "command": "/governance-review",                   "dir": "lower",  "warn": 3,     "fail": 8, "weight": 2.0},
    # Secret_Scrubs RETIRED 2026-07-19 (metric-surface review Part E item 3):
    # ~/.claude/data/secret_scrubber.jsonl is absent on this host — the protective
    # counter never fired. Secrets_Detected (secret_scrub.py mechanism) is the live
    # secrets metric. Re-add only together with a real scrubber emitter.
    # Brush — architecture & efficiency
    "Total_Cost":               {"skill": "cost-breakdown-audit",         "command": "/cost-breakdown-audit",                                                              "readonly": True},
    "Token_Spend":              {"skill": "token-optimizer",              "command": "/token-optimizer"},
    "Cost_Per_Task":            {"skill": "cost-breakdown-audit",         "command": "/cost-breakdown-audit",                                                              "readonly": True},
    "Token_Execution_Density":  {"skill": "token-optimizer",              "command": "/token-optimizer",                     "dir": "lower",  "warn": 40000, "fail": 80000, "weight": 1.0},
    "Local_Routing_Share":      {"skill": "model-selector",               "command": "/model-selector",                      "dir": "higher", "warn": 25,    "fail": 10,  "auto_remediable": False, "weight": 2.0},
    "Revision_Ratio":           {"skill": "simplify",                     "command": "/simplify"},
    "Subagent_Efficiency_Index": {"skill": "subagent-audit",              "command": "/subagent-audit",                      "dir": "higher", "warn": 80,    "fail": 60, "weight": 2.0, "maturity": "DRY-RUN-GRADED", "mechanism": {"script": "subagent_audit.py", "args": [], "read_only": True, "timeout_s": 120}},
    "Chain_Depth_Avg":          {"skill": "subagent-audit",               "command": "/subagent-audit",                      "dir": "lower",  "warn": 3,     "fail": 5, "weight": 1.0, "maturity": "DRY-RUN-GRADED", "mechanism": {"script": "chain_depth_audit.py", "args": [], "read_only": True, "timeout_s": 120}},
    "Hardcoded_Path_Incidents": {"skill": "arch-hygiene",                 "command": "/arch-hygiene",                        "dir": "lower",  "warn": 1,     "fail": 5, "weight": 1.0},
    "Root_Hygiene_Issues":      {"skill": "arch-hygiene",                 "command": "/arch-hygiene",                        "dir": "lower",  "warn": 1,     "fail": 4, "weight": 1.0},
    "Architecture_Scorecard_Grade": {"skill": "runtime-refactor-hardening", "command": "/runtime-refactor-hardening",        "dir": "higher", "warn": 85,    "fail": 70, "weight": 3.0},
    # Arts — craft & UX
    "Slop_Density":             {"skill": "humanizer",                    "command": "/humanizer",                           "dir": "lower",  "warn": 15,    "fail": 30, "weight": 3.0, "maturity": "DRY-RUN-GRADED", "mechanism": {"script": "slop_strip.py", "args": [], "read_only": True, "timeout_s": 120}},
    "Frustration_Signals":      {"skill": "insights",                     "command": "/insights",                            "dir": "lower",  "warn": 0.5,   "fail": 2,  "per": "session", "readonly": True, "auto_remediable": False, "weight": 2.0},
    "Rework_Loops":             {"skill": "insights",                     "command": "/insights",                            "dir": "lower",  "warn": 1,     "fail": 3,  "per": "session", "auto_remediable": False, "weight": 2.0},
    "Simplify_Runs":            {"skill": "simplify",                     "command": "/simplify",                            "dir": "higher", "warn": 1,     "fail": 0, "weight": 1.0},
    "Simplify_Age":             {"skill": "simplify",                     "command": "/simplify",                            "dir": "lower",  "warn": 7,     "fail": 21, "weight": 1.0},
    "Doc_Parity_Issues":        {"skill": "wiki",                         "command": "/wiki",                                "dir": "lower",  "warn": 1,     "fail": 5, "weight": 2.0},
    # Skills_Optimized + Skill_Promotions RETIRED 2026-07-19 (metric-surface review
    # Part E item 3): their sources (skill_improve_after_use_log.jsonl /
    # skill_promotion_log.jsonl) are never written on this host — both counters were
    # permanently dark. Re-add only together with live emitters.
    "Skill_Conflicts":          {"skill": "skill-consolidator",           "command": "/skill-consolidator",                  "dir": "lower",  "warn": 1,     "fail": 5, "weight": 1.0, "maturity": "DRY-RUN-GRADED", "mechanism": {"script": "skill_conflict_audit.py", "args": [], "read_only": True, "timeout_s": 120}},
    "MCP_Smoke_Fails":          {"skill": "mcp-setup",                    "command": "/mcp-setup",                           "dir": "lower",  "warn": 1,     "fail": 3, "weight": 1.0},
    # Knowledge vault health (arts/Knowledge group — cross-component integration)
    "Wiki_Health_Score":        {"skill": "wiki",                         "command": "/wiki"},
    "Wiki_Article_Count":       {},  # informational — volume, not a failure signal
    "Raw_Pending":              {"skill": "wiki",                         "command": "/wiki",                                "dir": "lower",  "warn": 1,     "fail": 5,  "weight": 1.0, "maturity": "DRY-RUN-GRADED", "mechanism": {"script": "wiki_compile.py", "args": [], "read_only": True, "timeout_s": 120}},
    "Wiki_Orphans":             {"skill": "wiki",                         "command": "/wiki",                                "dir": "lower",  "warn": 2,     "fail": 10, "weight": 1.0, "maturity": "DRY-RUN-GRADED", "mechanism": {"script": "wiki_link.py", "args": [], "read_only": True, "timeout_s": 120}},
    # Meta — informational, not scored (no dir). Shows pillar instrumentation depth.
    "Instrumentation_Coverage": {"skill": "audit-mechanisms",             "command": "/audit-mechanisms"},
}

# Direction-only overrides for the 24h summary clause — used ONLY for improved/worsened labels.
# These metrics deliberately lack warn/fail thresholds (protective activities or informational
# counters) so they cannot go in METRIC_CONFIG.dir without breaking scoring. Separate lookup.
_24H_DIRECTION: dict[str, str] = {
    "Complexity_Weighted_Throughput": "higher",   # more throughput = more work done = good
    "Processes_Reaped":               "higher",   # more reaped = reaper is healthy = good
    "Total_Cost":                     "lower",    # lower spend is better
    "Token_Spend":                    "lower",    # lower is better
    "Cost_Per_Task":                  "lower",    # lower is better
}


def _clamp_threshold(direction: str, manual_warn, manual_fail, cal_warn, cal_fail) -> tuple:
    """Calibration may only TIGHTEN a guard, never loosen it past the manual policy value.
    Drift in thresholds.json had loosened cost guards up to ~20x (e.g. Token_Execution_Density
    warn 40000 -> 299002); clamping caps the calibrated value at the manual ceiling (dir:lower)
    or floor (dir:higher) so data drift can only make a guard stricter, never weaker."""
    if direction == "lower":    # lower is better -> a tighter guard is a SMALLER number
        return min(cal_warn, manual_warn), min(cal_fail, manual_fail)
    return max(cal_warn, manual_warn), max(cal_fail, manual_fail)   # higher is better -> tighter is LARGER


def _apply_calibration(cal: dict | None = None) -> None:
    """Overlay data-derived warn/fail from thresholds.json onto METRIC_CONFIG, but only where it
    TIGHTENS the guard (see _clamp_threshold). Manual values stay as the fallback for metrics with
    no calibration data. dir/per never overridden. `cal` is injectable for tests."""
    if cal is None:
        path = _THIS.parent / "thresholds.json"
        try:
            cal = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
    for metric, t in cal.items():
        cfg = METRIC_CONFIG.get(metric)
        if cfg and "dir" in cfg and "warn" in t and "fail" in t and "warn" in cfg and "fail" in cfg:
            cfg["warn"], cfg["fail"] = _clamp_threshold(
                cfg["dir"], cfg["warn"], cfg["fail"], t["warn"], t["fail"])


_apply_calibration()

# Governance opt-in grant — seed every metric APPLY so legacy reflex behavior is
# preserved until the live engine's REFLEX_REQUIRE_GRANT gate is flipped. Phase 3's
# maturity ladder (agentica_core.maturity.resolve_maturity) may later demote a seed
# on a GOTCHA grade or promote a non-APPLY seed on a RULE grade.
for _cfg in METRIC_CONFIG.values():
    _cfg.setdefault("maturity", "APPLY")

# Public aliases for external consumers (calibrate.py, reflexes.py, remediation.py, tests).
METRIC_RULES: dict[str, dict] = {
    k: {p: v[p] for p in ("dir", "warn", "fail", "per") if p in v}
    for k, v in METRIC_CONFIG.items() if "dir" in v
}
REMEDIATION: dict[str, dict] = {
    k: {"skill": v["skill"], "command": v["command"]}
    for k, v in METRIC_CONFIG.items() if "skill" in v
}


def batch_deferred_metrics(metric_config: dict[str, dict] | None = None) -> list[str]:
    """Metrics whose real-time remediation is an EXPENSIVE, code-modifying LLM skill with
    no deterministic mechanism and no urgency — the reflex fire-path fail-open class.

    The reflex engine uses this set two ways (both fed via state/batch_metrics.json):
      1. verify-gate (2a): re-measure live before spawning the skill (bin/remeasure_gate.py),
      2. batch-defer (2b): outside REFLEX_BATCH_WINDOW, hold the fire for the overnight batch
         instead of spending a live skill spawn (verify-real-time / improve-overnight).

    Membership: auto-remediable, has a skill/command, NOT readonly (a readonly metric's skill
    is diagnostic — the `readonly` flag is the operator's per-metric assertion; skill-level
    classification is intentionally NOT used because one un-flagged metric can drag an
    otherwise-diagnostic skill like /insights into "code-modifying"), no deterministic
    remediation `mechanism` (those already get a fast, safe real-time path), and not `urgent`.
    Urgent+deterministic security metrics (Secrets_Detected, Gate_Canary_Fault) are excluded
    by the mechanism test; there is no urgent+agent metric in the current registry.
    """
    mc = metric_config if metric_config is not None else METRIC_CONFIG
    return sorted(
        mk for mk, cfg in mc.items()
        if cfg.get("auto_remediable") is not False
        and cfg.get("skill")
        and cfg.get("command")
        and not cfg.get("readonly")
        and "mechanism" not in cfg
        and not cfg.get("urgent")
    )


def _num(s) -> float | None:
    try:
        return float(str(s).replace("%", "").replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def count_live_sim(payload: dict) -> tuple[int, int]:
    """(live, simulated) metric counts across a payload's pillars. Single source for the
    LIVE/SIMULATED tally the renderer, state report, and aggregator summary all need."""
    live = sim = 0
    for pillar in payload.get("pillars", {}).values():
        for group in pillar.values():
            for env in group.values():
                if env.get("is_simulated"):
                    sim += 1
                else:
                    live += 1
    return live, sim


def live_numeric_metrics(pillars: dict) -> dict[str, float]:
    """Flatten pillars to {pillar/group/metric: value} for live (non-simulated) numeric
    metrics — the canonical history/backfill key format."""
    out: dict[str, float] = {}
    for pk, groups in pillars.items():
        for gname, metrics in groups.items():
            for mk, env in metrics.items():
                if env.get("is_simulated"):
                    continue
                v = _num(env.get("val"))
                if v is not None:
                    out[f"{pk}/{gname}/{mk}"] = v
    return out


def _health(v: float | None, rule: dict) -> float:
    """Continuous 0–100 health for one metric. 100 = at/inside warn; 40 = at the fail
    threshold; →0 as it runs further past fail. Smooth, severity-aware (not coarse A/D/F)."""
    if v is None:
        return 100.0
    warn, fail = float(rule["warn"]), float(rule["fail"])
    if rule["dir"] == "higher":
        if v >= warn:
            return 100.0
        if v <= fail:
            return max(0.0, 40.0 * (v / fail)) if fail > 0 else 0.0
        if warn == fail:
            return 40.0
        return 40.0 + 60.0 * (v - fail) / (warn - fail)
    # lower-is-better
    if v <= warn:
        return 100.0
    if v >= fail:
        over = (v - fail) / (fail if fail > 0 else 1.0)
        return max(0.0, 40.0 * (1.0 - over))
    # Reached only when warn < v < fail. Guard against degenerate warn==fail
    # (floating-point near-equality) where fail-warn→0 would divide by zero.
    if warn >= fail:
        return 40.0
    return 100.0 - 60.0 * (v - warn) / (fail - warn)


def _letter(h: float) -> str:
    return "A" if h >= 90 else "B" if h >= 75 else "C" if h >= 60 else "D" if h >= 40 else "F"


def _session_count(pillars: dict) -> float:
    for groups in pillars.get("bow", {}).values():
        if "Session_Count" in groups:
            n = _num(groups["Session_Count"].get("val"))
            if n and n > 0:
                return n
    return 1.0


def annotate(pillars: dict) -> dict:
    """Attach remediation to every live metric and return category_scores: per-pillar STATUS
    (worst-tier rollup + passing/graded counts), flags, and coverage. No weighted-mean pillar
    score is computed — per the de-aggregation doctrine, status counts are the only rollup
    (a mean can average a hard FAIL away; counts cannot). Injects Instrumentation_Coverage
    metric per pillar.

    NOTE: mutates *pillars* in-place — adds ``mitigation_skill``, ``mitigation_command``,
    ``is_graded``, and ``flagged`` keys to metric envelopes, and injects the synthetic
    ``Instrumentation_Coverage`` metric into each pillar's "Coverage" group. Callers that
    need an unmodified copy must deep-copy before calling.
    """
    sessions = _session_count(pillars)
    stuck_reflex_ids = set()
    try:
        state_path = _ORDER_SAMURAI_ROOT / "state" / "reflex_engine_state.json"
        if state_path.exists():
            state_data = json.loads(state_path.read_text(encoding="utf-8"))
            ni = state_data.get("noImprovement", {})
            stuck_reflex_ids = {
                key.split("::")[0]
                for key, val in ni.items()
                if isinstance(val, dict) and val.get("stuck")
            }
    except Exception:
        pass
    scores: dict[str, dict] = {}
    for pk, groups in pillars.items():
        healths: list[float] = []
        flags: list[dict] = []
        total_gradeable = 0  # metrics with a dir rule (live OR simulated)
        tier_counts = {"PASS": 0, "HIGH": 0, "CRITICAL": 0}
        for metrics in groups.values():
            for mk, env in metrics.items():
                cfg = METRIC_CONFIG.get(mk, {})
                if "skill" in cfg and "mitigation_command" not in env and not env.get("is_simulated"):
                    env["mitigation_skill"] = cfg["skill"]
                    env["mitigation_command"] = cfg["command"]
                if "mechanism" in cfg and not env.get("is_simulated"):
                    env["mitigation_mechanism"] = cfg["mechanism"]
                if "dir" not in cfg:
                    continue
                total_gradeable += 1
                if env.get("is_simulated"):
                    continue
                v = _num(env.get("val"))
                if cfg.get("per") == "session" and v is not None:
                    v = v / sessions
                h = _health(v, cfg)
                healths.append(h)
                # Tier classification mirrors the health curve anchors:
                # inside warn = PASS, past fail = CRITICAL, between = HIGH.
                tier = "PASS" if h >= 100.0 else "CRITICAL" if h <= 40.0 else "HIGH"
                tier_counts[tier] += 1
                # Per-metric SLO status — single source of truth for the status-first
                # surfaces (badges) AND the needs-attention count. Computed from the same
                # session-normalized health as the rollup so the two can never disagree.
                env["status"] = {"PASS": "OK", "HIGH": "WARN", "CRITICAL": "FAIL"}[tier]
                if f"metric:{pk}:{mk}" in stuck_reflex_ids:
                    env["status"] = "needs:human"

                # Weight is a sort/priority hint on the rule (rubric display, needs_attention
                # ordering) — it multiplies nothing; there is no blended score.
                w = float(cfg.get("weight", 1.0))
                env["is_graded"] = True   # carries an SLO status — UI heartbeat
                # Effective (post-calibration) rule — single source of truth for the
                # dashboard's Scoring Rubric page. Hardcoding these in the UI drifts
                # within a week because _apply_calibration() overlays thresholds.json.
                env["rule"] = {
                    "dir": cfg["dir"], "warn": float(cfg["warn"]), "fail": float(cfg["fail"]),
                    "weight": w, "per": cfg.get("per"),
                }
                if h < 60:
                    flags.append({"name": mk, "val": env.get("val"), "grade": _letter(h), "flagged": True})
                    env["flagged"] = True
        flags.sort(key=lambda f: {"F": 0, "D": 1, "C": 2}.get(f["grade"], 3))

        graded_count = len(healths)
        coverage_pct: float | None = (
            round(100 * graded_count / total_gradeable, 1) if total_gradeable else None
        )

        # Inject Instrumentation_Coverage as an informational metric (no dir → not scored,
        # but displayed + flagged so low coverage surfaces as a governance gap).
        if coverage_pct is not None and "Instrumentation_Coverage" not in groups.get("Coverage", {}):
            groups.setdefault("Coverage", {})["Instrumentation_Coverage"] = {
                "val": str(coverage_pct), "delta": "0", "trend": "neutral",
                "history": [], "is_percent": True, "is_count": False,
                "is_simulated": False, "tier": "DERIVED", "timestamp": "",
                "mitigation_skill": "audit-mechanisms",
                "mitigation_command": "/audit-mechanisms",
            }

        scores[pk] = {
            # No "score"/"grade" keys: the weighted-mean pillar score was removed 2026-07-19
            # (de-aggregation doctrine — removal, never faking). Status lives in "rollup";
            # per-metric letter grades live in "flags" (reflex tier mapping reads those).
            "graded_count": graded_count,
            "total_gradeable": total_gradeable,
            "coverage_pct": coverage_pct,
            "flags": flags[:3],
            # Tier rollup — the pillar's STATUS, consistent with the per-metric reflex
            # philosophy: worst tier wins, no averaging away of hard failures.
            "rollup": {
                "worst": ("CRITICAL" if tier_counts["CRITICAL"] else "HIGH" if tier_counts["HIGH"] else "PASS"),
                "passing": tier_counts["PASS"],
                "graded": graded_count,
            },
        }
    return scores


def needs_attention(pillars: dict) -> dict:
    """The ONE legitimate composite (plan Phase 2): every metric currently breaching its SLO
    (WARN or FAIL), with a count. Decomposable by construction — the count is just len(items)
    and the full list ships beside it, so the number is never a standalone KPI.

    ANTI-GAMING (plan §Phase 2 guard 1): this is presentation only. It NEVER drives a reflex or
    grade — only the per-metric thresholds (via reflexes.build_reflexes) drive remediation. Sorted
    by severity then weight; weight is a *sort hint*, not a multiplier (no blended score here).

    Requires pillars already annotated (env["status"] set by annotate())."""
    items: list[dict] = []
    for pk, groups in pillars.items():
        for metrics in groups.values():
            for mk, env in metrics.items():
                st = env.get("status")
                if st in ("WARN", "FAIL", "needs:human"):
                    items.append({
                        "metric": mk, "status": st, "pillar": pk,
                        "severity": 0 if st in ("FAIL", "needs:human") else 1,
                        "weight": float((env.get("rule") or {}).get("weight", 1.0)),
                        "val": env.get("val"),
                    })
    items.sort(key=lambda x: (x["severity"], -x["weight"], x["metric"]))
    return {"count": len(items), "items": items}


def _val(pillars: dict, pk: str, key: str):
    for g in pillars.get(pk, {}).values():
        if key in g:
            return g[key].get("val")
    return "—"


def _movers(pillars: dict, pk: str, top: int = 2) -> list[dict]:
    """Largest-magnitude trending metrics in a pillar (live only, |delta|>0), worst first."""
    out: list[dict] = []
    for groups in pillars.get(pk, {}).values():
        for mk, env in groups.items():
            if env.get("is_simulated"):
                continue
            d = _num(env.get("delta"))
            if d is None or d == 0:
                continue
            out.append({"name": mk, "val": env.get("val"), "delta": env.get("delta"),
                        "trend": env.get("trend"), "mag": abs(d)})
    out.sort(key=lambda m: m["mag"], reverse=True)
    return out[:top]


def _24h_clause(pk: str, store: Path) -> str:
    """Plain-language sentence about metrics that shifted in the last 24 hours and whether
    each shift was an improvement or a regression. Returns '' when no 24h data exists."""
    from datetime import datetime, timezone, timedelta
    rows: list[dict] = []
    if store.exists():
        for ln in store.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                rows.append(json.loads(ln))
            except ValueError:
                pass
    if len(rows) < 2:
        return ""

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    # Parse timestamps, drop rows with unparseable ts, then sort chronologically.
    # Without sorting, JSONL rows written out-of-order produce wrong baseline/recent.
    parsed: list[tuple[datetime, dict]] = []
    for r in rows:
        try:
            ts_raw = str(r.get("ts", ""))
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            parsed.append((ts, r.get("values") or {}))
        except ValueError:
            continue
    parsed.sort(key=lambda x: x[0])

    # Walk rows chronologically; keep last pre-cutoff snapshot as baseline, collect all post-cutoff.
    baseline_vals: dict = {}
    recent_vals: dict = {}
    for ts, vals in parsed:
        if ts < cutoff:
            baseline_vals = vals          # keep updating — last pre-24h snapshot wins
        else:
            recent_vals = vals            # latest post-24h snapshot wins

    if not baseline_vals or not recent_vals:
        return ""

    movers: list[dict] = []
    new_metrics: list[str] = []  # appeared in recent window with no prior baseline
    for key, cur in recent_vals.items():
        # Only metrics in this pillar; skip internal pillar-score keys
        if not key.startswith(f"{pk}/"):
            continue
        if not isinstance(cur, (int, float)):
            continue
        mk = key.rsplit("/", 1)[-1]
        rule = METRIC_CONFIG.get(mk, {})
        prev = baseline_vals.get(key)
        if prev is None:
            # First time this metric was ever snapshotted — note it as newly tracked
            new_metrics.append(mk.replace("_", " ").lower())
            continue
        delta = round(float(cur) - float(prev), 2)
        if delta == 0:
            continue
        # Primary direction from METRIC_CONFIG; fallback to _24H_DIRECTION for metrics that
        # are intentionally unscored (protective counters, spending totals without thresholds).
        d = rule.get("dir") or _24H_DIRECTION.get(mk)
        good = None if d is None else ((d == "lower" and delta < 0) or (d == "higher" and delta > 0))
        movers.append({"name": mk, "delta": delta, "good": good, "mag": abs(delta)})

    movers.sort(key=lambda m: m["mag"], reverse=True)
    top = movers[:3]

    parts = []
    for m in top:
        label = m["name"].replace("_", " ").lower()
        direction = "up" if m["delta"] > 0 else "down"
        sign = "+" if m["delta"] > 0 else ""
        outcome = ("improved" if m["good"] else "worsened") if m["good"] is not None else None
        suffix = f" ({outcome})" if outcome is not None else ""
        parts.append(f"{label} moved {direction} by {sign}{m['delta']}{suffix}")

    # Newly tracked metrics (no pre-24h baseline): surface the most important ones.
    # Priority: graded metrics (have a rule) first, then alphabetical. Cap at 2 to avoid noise.
    if new_metrics:
        graded_new = [mk for mk in new_metrics
                      if METRIC_CONFIG.get(mk.replace(" ", "_"), {}).get("dir")]
        notable = (graded_new or new_metrics)[:2]
        if notable:
            parts.append(f"now tracking (no prior baseline): {', '.join(notable)}")

    if not parts:
        return ""
    joined = "; ".join(parts)
    return f"In the last 24 hours: {joined}."


def _trend_clause(pillars: dict, pk: str, scores: dict) -> str:
    """Plain-language note on the metric to watch and what moved (humanizer style)."""
    parts: list[str] = []
    flags = scores[pk].get("flags", [])
    if flags:
        f = flags[0]
        parts.append(f"The thing to watch most is {f['name'].replace('_',' ').lower()}, now at {f['val']}")
    movers = _movers(pillars, pk)
    if movers:
        ms = "; ".join(
            f"{m['name'].replace('_',' ').lower()} went {'up' if m['trend']=='up' else 'down'} by {str(m['delta']).lstrip('+')}"
            for m in movers
        )
        parts.append(f"Since the last check, {ms}")
    else:
        parts.append("Nothing has moved since the last check")
    return ". ".join(parts) + "."


def _recommendations(pk: str, scores: dict) -> str:
    """Plain-language skill recommendations to raise the score, from the flagged metrics."""
    flags = scores[pk].get("flags", [])
    if not flags:
        return "Nothing needs fixing right now — keep it steady to hold the score."
    seen: list[str] = []
    recs: list[str] = []
    for f in flags:
        cfg = METRIC_CONFIG.get(f["name"], {})
        cmd = cfg.get("command")
        if not cmd or cmd in seen:
            continue
        seen.append(cmd)
        recs.append(f"run {cmd} to fix {f['name'].replace('_', ' ').lower()}")
    if not recs:
        return "Run the suggested skill on each flagged metric to raise the score."
    joined = recs[0] if len(recs) == 1 else ("; ".join(recs[:-1]) + "; and " + recs[-1])
    return f"To raise this score, {joined}."


def build_summaries(pillars: dict, scores: dict, store: Path | None = None) -> dict:
    store = store or default_history_path()
    v = lambda pk, k: _val(pillars, pk, k)
    t = lambda pk: _trend_clause(pillars, pk, scores)
    rec = lambda pk: _recommendations(pk, scores)
    def h24(pk: str) -> str:
        c = _24h_clause(pk, store)
        return (c + " ") if c else ""
    sc = lambda pk: scores[pk]["score"] if scores[pk]["score"] is not None else "—"
    cost_savings = v('brush', 'Estimated_Cost_Savings')
    cost_savings_str = f"${cost_savings}" if cost_savings != "—" else "—"

    return {
        "bow": (
            f"This pillar tracked {v('bow','Estimated_Agent_Time_Saved')} hours of agent execution time saved by automated task runs. The agent ran {v('bow','Complexity_Weighted_Throughput')} complexity-weighted tasks across "
            f"{v('bow','Session_Count')} work sessions and got them right {v('bow','Governance_Pass_Rate')}% of the time, "
            f"with errors on only {v('bow','Error_Rate')}%. It reached for its tools {v('bow','Tool_Calls')} times (recovering via fallbacks {v('bow','Fallback_Recovery_Rate')}% of the time), and most sessions took about {v('bow','Avg_Session_Turns')} back-and-forth turns. "
            f"{h24('bow')}{t('bow')} {rec('bow')}"
        ),
        "sword": (
            f"This pillar tracked {v('sword','Kill_Chains_Disrupted')} distinct threat vectors intercepted and disrupted (with {v('sword','Pending_Chain_Proposals')} pending proposals). Right now, the vulnerability mean time to resolution is {v('sword','Vulnerability_MTTR')} days and there are "
            f"{v('sword','Secrets_Detected')} leaked passwords or keys. The agent stepped out of bounds "
            f"{v('sword','Boundary_Violations')} times and broke a house rule {v('sword','Rule_Violations')} times. "
            f"{h24('sword')}{t('sword')} {rec('sword')}"
        ),
        "brush": (
            f"This pillar tracked {cost_savings_str} saved from cost-per-task improvement vs last week at this week's task volume (with a code-tidiness grade of {v('brush','Architecture_Scorecard_Grade')}). The agent spent ${v('brush','Total_Cost')} in total, about ${v('brush','Cost_Per_Task')} per task, using "
            f"{v('brush','Token_Spend')} tokens. It left {v('brush','Hardcoded_Path_Incidents')} hard-coded paths and "
            f"{v('brush','Root_Hygiene_Issues')} messy-folder issues to clean up. "
            f"{h24('brush')}{t('brush')} {rec('brush')}"
        ),
        "arts": (
            f"This pillar logged {v('arts','Craft_Improvements')} craft improvements this week (skill promotions plus completed arts deliverables). The agent's writing carried {v('arts','Slop_Density')} bits of "
            f"filler per 1,000 words. The user sounded frustrated {v('arts','Frustration_Signals')} times and asked for redos "
            f"{v('arts','Rework_Loops')} times, while the cleanup pass ran {v('arts','Simplify_Runs')} times and "
            f"{v('arts','Doc_Parity_Issues')} docs are out of date. "
            f"The knowledge vault holds {v('arts','Wiki_Article_Count')} curated articles at a health score of {v('arts','Wiki_Health_Score')}/100. "
            f"{h24('arts')}{t('arts')} {rec('arts')}"
        ),
    }


def default_history_path() -> Path:
    return _THIS.parents[2] / "Data" / "telemetry" / "metrics_history.jsonl"


def populate_history(pillars: dict, store: Path | None = None, max_points: int = 7) -> dict:
    """Read prior snapshots and set each metric's history[]/delta/trend.
    Returns the current snapshot dict (caller persists it via append_snapshot)."""
    store = store or default_history_path()
    rows: list[dict] = []
    if store.exists():
        for ln in store.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                rows.append(json.loads(ln))
            except ValueError:
                pass
    rows = rows[-(max_points - 1):]

    # Per-metric history + delta
    current: dict[str, float] = {}
    for pk, groups in pillars.items():
        for gname, metrics in groups.items():
            for mk, env in metrics.items():
                v = _num(env.get("val"))
                if v is None or env.get("is_simulated"):
                    continue
                key = f"{pk}/{gname}/{mk}"
                current[key] = v
                hist = [r["values"][key] for r in rows if key in r.get("values", {})]
                hist = hist + [v]
                env["history"] = hist[-max_points:]
                if len(env["history"]) >= 2:
                    delta = round(v - env["history"][-2], 2)
                    env["delta"] = ("+" if delta >= 0 else "") + str(delta)
                    env["trend"] = "up" if delta > 0 else ("down" if delta < 0 else "neutral")

                # Trajectory alerting (#G3): linear regression over history to project
                # days until the fail threshold is breached.  Stored in env so reflexes.py
                # can generate early-warning reflex entries (HIGH ≤3 days, MEDIUM ≤7 days).
                rule = METRIC_RULES.get(mk)
                env["trajectory_breach_days"] = None
                if rule and len(env["history"]) >= 3:
                    hist_vals = env["history"]
                    n = len(hist_vals)
                    x_mean = (n - 1) / 2.0
                    y_mean = sum(hist_vals) / n
                    denom = sum((i - x_mean) ** 2 for i in range(n))
                    if denom > 0:
                        slope = (
                            sum((i - x_mean) * (h - y_mean) for i, h in enumerate(hist_vals))
                            / denom
                        )
                        # History snapshots are weekly → slope per week → ÷7 for per-day slope
                        slope_per_day = slope / 7
                        fail_val = rule.get("fail")
                        if fail_val is not None and slope_per_day != 0:
                            direction = rule.get("dir", "lower")
                            current_v = hist_vals[-1]
                            breach_days: float | None = None
                            if direction == "lower" and slope_per_day > 0:
                                # Value rising toward fail threshold (bad direction)
                                breach_days = (fail_val - current_v) / slope_per_day
                            elif direction == "higher" and slope_per_day < 0:
                                # Value falling toward fail threshold (bad direction)
                                breach_days = (current_v - fail_val) / (-slope_per_day)
                            if breach_days is not None and breach_days > 0:
                                # Guard: if the most recent 3 points show no worsening trend,
                                # the full-history regression is an artifact of an earlier spike.
                                # Suppress the prediction to avoid false "imminent breach" alerts.
                                recent = hist_vals[-3:]
                                if len(recent) >= 2:
                                    recent_slope = (recent[-1] - recent[0]) / max(len(recent) - 1, 1)
                                    if direction == "lower" and recent_slope <= 0:
                                        breach_days = None
                                    elif direction == "higher" and recent_slope >= 0:
                                        breach_days = None
                            if breach_days is not None and breach_days > 0:
                                env["trajectory_breach_days"] = round(breach_days, 1)

    # Pillar-score history retired 2026-07-19 with the weighted mean itself: no new
    # `_pillar_score/{pk}` keys are written (old snapshot rows keep theirs — read-only
    # history, never rewritten). Per-metric values above are the whole record.
    return current


def append_snapshot(store: Path, timestamp: str, current: dict) -> None:
    store.parent.mkdir(parents=True, exist_ok=True)
    with store.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": timestamp, "values": current}) + "\n")
