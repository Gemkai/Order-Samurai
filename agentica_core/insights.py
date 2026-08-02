"""Scores, summaries, remediation, and trend history — the analytical layer the dashboard needs,
mirroring Jarvis's category_scores / summaries / mitigation / history. Pure, data-driven (no LLM).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_THIS = Path(__file__).resolve()
_ORDER_SAMURAI_ROOT = Path(os.environ.get(
    "ORDER_SAMURAI_ROOT", str(_THIS.parents[1] / "Order Samurai")))

# Single source: skill/command (all metrics) + dir/warn/fail (graded only) + per (rate metrics).
# Guardrail_Blocks is protective-activity: shown but NOT graded (no dir key).
METRIC_CONFIG: dict[str, dict] = {
    # Bow — operational
    "Error_Rate":               {"skill": "investigate",                  "command": "/investigate",                         "dir": "lower",  "warn": 2,     "fail": 5,   "readonly": True, "weight": 3.0, "maturity": "DRY-RUN-GRADED", "mechanism": {"script": "error_triage.py", "args": ["--json"], "read_only": True, "timeout_s": 120}},
    # Latency_P50 consolidated into Latency_P95 (2026-07-08 audit) — the median was
    # claude's constant zeros, not speed. Re-add with the emitter fix.
    # RETUNE 2026-07-08 audit: an audit skill can't move infra latency — advisory only.
    "Latency_P95":              {"skill": "investigate",                  "command": "/investigate",                         "dir": "lower",  "warn": 90000, "fail": 180000, "readonly": True, "auto_remediable": False, "weight": 1.0},
    "Complexity_Weighted_Throughput": {"skill": "insights",               "command": "/insights",                                                                          "readonly": True},
    "Tool_Calls":               {"skill": "tool-diversity-audit",         "command": "/tool-diversity-audit",                                                              "readonly": True},
    # Fallback_Recovery_Rate / Agent_Autonomy_Ratio / Processes_Reaped RETIRED
    # 2026-07-08 audit (dead source / structural 100 / no Mac reaper) — removal,
    # never faking. Re-add only with a live emitter.
    "Session_Count":            {},  # informational — no remediation action makes sense
    "Avg_Session_Turns":        {"skill": "insights",                     "command": "/insights",                            "dir": "lower",  "warn": 8,     "fail": 15,  "readonly": True, "auto_remediable": False, "calibrate": False, "weight": 1.0},  # SENSEI-1: /insights 8x no_change -> advisory; bimodal history (autonomous vs interactive) -> never percentile-calibrate
    "Agent_Process_Count":      {"skill": "self-heal",                    "command": "/self-heal", "auto_remediable": False, "kind": "mis_route"},  # mis-route (DO-NOT-USE): self-heal kills procs by age (no allowlist/dry-run) — would target the governance stack; never auto-fire
    # REMAP 2026-07-08 audit: audit-mechanisms REPORTS orphans; removing one is human
    # work (0/8 improved, audit_only failure_mode) — advisory, never auto-fire.
    "Mechanism_Orphans":        {"skill": "audit-mechanisms",             "command": "/audit-mechanisms",                    "dir": "lower",  "warn": 1,     "fail": 3, "auto_remediable": False, "weight": 1.0},
    # Remediation_Delta (2026-08-01, metric-gap remediation, phase B2): magnitude
    # companion to Self_Correction_Rate's yes/no judgment -- median(3 post-firing) -
    # median(3 pre-firing) history values per remediation attempt, sign-normalized so
    # improvement is always positive (agentica_core/remediation_delta.py). No single
    # skill remediates a compound cross-skill signal like this; route through
    # /insights (read-only) so an alarm has a real, harmless next action.
    # OBSERVATIONAL per the maturity ladder: warn/fail below are PROPOSED, not graded
    # -- graduates to graded only after two consecutive clean weekly measurements
    # (calibration gate), and this metric has zero weeks of real data yet.
    "Remediation_Delta":       {"skill": "insights",                     "command": "/insights",                            "dir": "higher", "warn": 0,     "fail": -0.01, "readonly": True, "auto_remediable": False, "maturity": "OBSERVE", "weight": 1.0},
    # Verifier_Failures consolidated into Governance_Pass_Rate (2026-07-08 audit):
    # same verifier source, two rows. The failing-platform drill-down rides on this
    # envelope (failure_platforms + doctor mitigation, set in build_pillars).
    "Governance_Pass_Rate":     {"skill": "runtime-refactor-hardening",   "command": "/runtime-refactor-hardening",          "dir": "higher", "warn": 85,    "fail": 70, "weight": 2.0},
    # Skill_Routing_Adherence: % of critical-work prompts routed through their
    # governing skill (skill_routing_adherence.py reducer, sword pillar). Target
    # >= 80% — a documented one-line-fix skip is legitimate, so the bar is 80 not
    # 100. Advisory/read-only: past-prompt routing can't be auto-remediated; low
    # adherence surfaces via /insights (session friction + routing analysis).
    "Skill_Routing_Adherence":  {"skill": "insights",                     "command": "/insights",                            "dir": "higher", "warn": 80,    "fail": 65, "readonly": True, "auto_remediable": False, "weight": 2.0},
    # Verifier_Falsifiability (2026-08-01, metric-gap remediation, phase C2): % of
    # Order Samurai/execution/verify_falsifiability.py's registered checks proven to
    # fail on a known-bad fixture AND pass on a known-clean one. A verifier that never
    # sees a bad input can silently CLEAN forever -- this closes that gap directly, so
    # no skill remediates it (the fix is writing more fixtures, a human/agent task, not
    # an auto-fire). OBSERVATIONAL: this launches with only 4 fixture pairs written
    # (~13%) by design -- warn/fail are set loose (10/5) so day-one coverage doesn't
    # read as an artificial CRITICAL; tighten as fixtures accumulate, same ratchet
    # pattern as Doc_Parity_Issues. Zero weeks of real calibration data yet.
    "Verifier_Falsifiability":  {"skill": "insights",                     "command": "/insights",                            "dir": "higher", "warn": 10,    "fail": 5, "readonly": True, "auto_remediable": False, "maturity": "OBSERVE", "weight": 2.0},
    # Sword — security
    # SWAP 2026-07-11 (C/D/F remediation plan step 3): Vulnerability_MTTR retired —
    # its name promised CVE mean-time-to-resolution but the reducer read kill-chain
    # events, and after the 2026-07-08 default-removal it was permanently SIMULATED
    # (no chains most weeks = no measurement). Open_CVEs is the honest replacement:
    # dependency_audit.json is live again (codebase_deps_audit.py, scheduled weekly)
    # and pip-safe-upgrade causally closes CVEs. Re-add a real MTTR only when a
    # first-seen→resolved CVE ledger exists.
    "Open_CVEs":                {"skill": "pip-safe-upgrade",             "command": "/pip-safe-upgrade",                    "dir": "lower",  "warn": 1,     "fail": 5, "weight": 2.0},
    "Boundary_Violations":      {"skill": "guard",                        "command": "/guard",                               "dir": "lower",  "warn": 1,     "fail": 3, "auto_remediable": False, "kind": "mis_route", "weight": 3.0},  # mis-route (DO-NOT-USE): guard is a preventive session toggle, not a remediator — can't fix existing violations; advisory only (real fix = a quarantine bin, not yet built)
    # warn:0 (main #59, 2026-07-26): a single detected secret must not grade a
    # perfect PASS — 1 was the "clean" floor, so exactly one secret scored the
    # same as zero.
    "Secrets_Detected":         {"skill": "security-audit",               "command": "/security-audit",                      "dir": "lower",  "warn": 0,     "fail": 1, "weight": 3.0, "maturity": "DRY-RUN-GRADED", "mechanism": {"script": "secret_scrub.py", "args": ["--json"], "read_only": True, "timeout_s": 120}},
    # Guardrail_Blocks RETIRED 2026-07-19 (dead emitter — no security_gate_log.jsonl writer on this host).
    # RETUNE 2026-07-08 audit: policy-enforcement-audit finds unenforced policy, it
    # doesn't stop violations (stuck 0/2) — advisory only. Window mismatch (S6) fixed
    # in aggregate.py: numerator now shares the payload window with Session_Count.
    "Rule_Violations":          {"skill": "policy-enforcement-audit",     "command": "/policy-enforcement-audit",            "dir": "lower",  "warn": 1,     "fail": 5,  "per": "session", "readonly": True, "auto_remediable": False, "weight": 2.0, "maturity": "DRY-RUN-GRADED", "mechanism": {"script": "policy_enforcement_audit.py", "args": [], "read_only": True, "timeout_s": 120}},
    # Canary_Failures RETIRED 2026-07-11 (C/D/F plan step 5): behavioral_canary.py
    # was never scheduled on this host — a permanently dark weight-3 metric is
    # registry noise, and the old mapping was a documented misroute. Re-add only
    # together with a scheduled canary run. (Gate_Canary_Fault is unaffected —
    # different source, still live.)
    "Gate_Canary_Fault":        {"skill": "canary-fault-diagnosis",       "command": "/canary-fault-diagnosis",              "readonly": True, "mechanism": {"script": "canary_fault_detect.py", "args": [], "read_only": True, "timeout_s": 120}},
    # Loop_Breaker_Fires RETIRED 2026-07-19 (metric-surface review Part E item 3):
    # ~/.claude/data/loop_breaker_state.json is never written on this host, so the
    # graded weight-2 metric was permanently dark (no envelope ever built) —
    # removal, never faking. Re-add only together with a live emitter.
    # Security_Scorecard RETIRED 2026-07-11: the Windows scripts-tier emitter is
    # gone and its content overlaps Guardrail_Blocks + Secrets_Detected +
    # Gate_Canary_Fault (audit already deprioritized it) — removal, never faking.
    # Skill_Safety_Findings RETIRED 2026-07-08 audit: no scanner exists on this
    # host and the mapped skill audited dep packages, not installed skills.
    # Re-introduce only together with a real skill scanner + quarantine bin.
    # REMAP 2026-07-08 audit: single remediation = pip-safe-upgrade (it causally
    # shrinks pip_outdated); the codebase-cleanup mechanism reflex was audit_only
    # and stuck 0/8 — killed. When dependency_audit.json returns (S3), grade the
    # CVE subset (Open_CVEs) rather than this raw outdated count.
    "Deprecated_Deps":          {"skill": "pip-safe-upgrade",             "command": "/pip-safe-upgrade",                    "dir": "lower",  "warn": 20,    "fail": 120, "weight": 1.0},
    "Governance_Review_Findings": {"skill": "governance-review",          "command": "/governance-review",                   "dir": "lower",  "warn": 3,     "fail": 8, "weight": 2.0},
    # Graded successor of Kill_Chains_Detected (2026-07-08 audit consolidation):
    # open exposure = detected − disrupted this week. Advisory — /guard reviews
    # chains; auto-firing it cannot disrupt one (same reasoning as Boundary_Violations).
    "Kill_Chains_Open":         {"skill": "guard",                        "command": "/guard",                               "dir": "lower",  "warn": 1,     "fail": 3, "auto_remediable": False, "kind": "mis_route", "weight": 2.0},
    # Governance_Work_Volume (backlog P1): critical-work detections this window,
    # routed or not — direction-neutral activity paired with Skill_Routing_Adherence
    # (high volume + low adherence = busy hand-rolled session, not a dead one).
    "Governance_Work_Volume":   {},
    # Secret_Scrubs RETIRED 2026-07-19 (metric-surface review Part E item 3):
    # ~/.claude/data/secret_scrubber.jsonl is absent on this host — the protective
    # counter never fired. Secrets_Detected (secret_scrub.py mechanism) is the live
    # secrets metric. Re-add only together with a real scrubber emitter.
    # Brush — architecture & efficiency
    "Total_Cost":               {"skill": "cost-breakdown-audit",         "command": "/cost-breakdown-audit",                                                              "readonly": True},
    # DEMOTE 2026-07-19 (metric-surface review Part C): token-optimizer is 0/4
    # lifetime (skill_efficacy.json) — an auto-fire that never moves the metric
    # only burns spawns. Advisory until the skill demonstrates efficacy.
    "Token_Spend":              {"skill": "token-optimizer",              "command": "/token-optimizer",                     "auto_remediable": False},
    "Cost_Per_Task":            {"skill": "cost-breakdown-audit",         "command": "/cost-breakdown-audit",                                                              "readonly": True},
    # RETUNE 2026-07-08 audit: the old 40k/80k ceiling sat 5-18x below the observed
    # distribution (206k-741k, median ~286k) — permanently CRITICAL, unreachable by
    # any remediation, and the source of 5 stuck reflex runs. Honest policy ceiling
    # from the real distribution; the causal lever is routing policy (llm_router
    # task-type routing), not a session-compaction skill run — advisory only.
    "Token_Execution_Density":  {"skill": "token-optimizer",              "command": "/token-optimizer",                     "dir": "lower",  "warn": 300000, "fail": 550000, "auto_remediable": False, "weight": 1.0},
    # REMAP 2026-07-08 audit: model-selector REGRESSED this metric in both recorded
    # attempts (8.5 → 0.0 x2). The causal lever is the router config
    # (~/.claude/scripts/llm_router.py task-type routing), not a skill invocation.
    "Local_Routing_Share":      {"skill": "model-selector",               "command": "/model-selector",                      "dir": "higher", "warn": 25,    "fail": 10,  "auto_remediable": False, "kind": "mis_route", "weight": 2.0},
    "Context_Cliff_Events":     {"skill": "token-optimizer",              "command": "/token-optimizer",                     "dir": "lower",  "warn": 25,    "fail": 50, "readonly": True, "auto_remediable": False, "calibrate": False, "weight": 1.0},  # PERCENT of scanned sessions since 2026-07-19 (was absolute count)
    # DEMOTE 2026-08-01 (metric-gap remediation, phase A2, frozen criterion >=8
    # attempts AND 0 improved): simplify is 0/31 lifetime improved across its
    # mapped metrics (remediation.efficacy() by_skill) — advisory, never auto-fire.
    "Revision_Ratio":           {"skill": "simplify",                     "command": "/simplify",                            "auto_remediable": False},
    # DEMOTED 2026-07-11 (metric grading pass, grade D): the benchmark design bug —
    # whole-session cost vs 3x solo median — makes an orchestrator session look
    # inefficient by construction, while the spawn grader shows 98.6% of spawn cost
    # justified. subagent-audit diagnoses but cannot causally move a cost ratio
    # (stuck 0/2). Advisory until the benchmark measures MARGINAL spawn cost
    # (per-spawn cost vs a per-task benchmark, or orchestrator-session tokens
    # excluded). The dry-run mechanism stays — it is the manual drill-down.
    # UNGRADED 2026-07-19 (metric-surface review Part B): the benchmark compares
    # whole-session cost to 3x a solo median, so an orchestrator session reads
    # FAIL by construction (5.0 at review time) while the spawn grader shows
    # 98.6% of spawn cost justified — a permanently-red metric nobody believes
    # trains alarm blindness. Value still shown + dry-run mechanism stays as the
    # manual drill-down; re-grade only with a MARGINAL-spawn-cost benchmark.
    "Subagent_Efficiency_Index": {"skill": "subagent-audit",              "command": "/subagent-audit",                      "auto_remediable": False, "maturity": "DRY-RUN-GRADED", "mechanism": {"script": "subagent_audit.py", "args": [], "read_only": True, "timeout_s": 120}},
    # RETUNE 2026-07-08 audit: warn 3 / fail 5 read as "don't orchestrate" for an
    # agent OS whose own sensei-cycle spawns 4 scouts (live median 4 = perpetual
    # WARN). Early-warning trend line, not an anti-orchestration gate.
    "Chain_Depth_Avg":          {"skill": "subagent-audit",               "command": "/subagent-audit",                      "dir": "lower",  "warn": 5,     "fail": 10, "weight": 1.0, "maturity": "DRY-RUN-GRADED", "mechanism": {"script": "chain_depth_audit.py", "args": ["--json"], "read_only": True, "timeout_s": 120}},
    # REMAP 2026-07-08 audit: "arch-hygiene" does not exist in the skill library —
    # dead reference. Both metrics derive from doctor verifier FAILs, so /doctor is
    # the surface that shows and re-checks them (METRIC_DOCS already said so).
    "Hardcoded_Path_Incidents": {"skill": "doctor",                       "command": "/doctor",                              "dir": "lower",  "warn": 1,     "fail": 5, "weight": 1.0},
    "Root_Hygiene_Issues":      {"skill": "doctor",                       "command": "/doctor",                              "dir": "lower",  "warn": 1,     "fail": 4, "weight": 1.0},
    "Architecture_Scorecard_Grade": {"skill": "runtime-refactor-hardening", "command": "/runtime-refactor-hardening",        "dir": "higher", "warn": 85,    "fail": 70, "weight": 3.0},
    # Arts — craft & UX
    # Output-quality tool-use triad (llm-judged; values from bin/tool_quality_scout.py via
    # state/tool_quality.json). Advisory — a quality score isn't auto-remediated (you fix the
    # underlying behavior, not the number). dir higher = better; SIMULATED until the scout runs.
    "Tool_Selection_Accuracy":  {"skill": "insights", "command": "/insights", "dir": "higher", "warn": 70, "fail": 50, "readonly": True, "auto_remediable": False, "weight": 2.0},
    "Tool_Arg_Correctness":     {"skill": "insights", "command": "/insights", "dir": "higher", "warn": 70, "fail": 50, "readonly": True, "auto_remediable": False, "weight": 2.0},
    "Tool_Response_Utilization":{"skill": "insights", "command": "/insights", "dir": "higher", "warn": 70, "fail": 50, "readonly": True, "auto_remediable": False, "weight": 2.0},
    "Faithfulness_Score":       {"skill": "insights", "command": "/insights", "dir": "higher", "warn": 80, "fail": 60, "readonly": True, "auto_remediable": False, "weight": 3.0},
    "Refusal_Appropriateness":  {"skill": "insights", "command": "/insights", "dir": "higher", "warn": 70, "fail": 50, "readonly": True, "auto_remediable": False, "weight": 1.0},
    "Retrieval_Relevance":      {"skill": "wiki", "command": "/wiki", "dir": "higher", "warn": 70, "fail": 50, "readonly": True, "auto_remediable": False, "weight": 2.0},
    "Slop_Density":             {"skill": "humanizer",                    "command": "/humanizer",                           "dir": "lower",  "warn": 15,    "fail": 30, "weight": 3.0, "maturity": "DRY-RUN-GRADED", "mechanism": {"script": "slop_strip.py", "args": ["--json"], "read_only": True, "timeout_s": 120}},
    "Frustration_Signals":      {"skill": "insights",                     "command": "/insights",                            "dir": "lower",  "warn": 0.5,   "fail": 2,  "per": "session", "readonly": True, "auto_remediable": False, "weight": 2.0},
    "Rework_Loops":             {"skill": "insights",                     "command": "/insights",                            "dir": "lower",  "warn": 1,     "fail": 3,  "per": "session", "auto_remediable": False, "weight": 2.0},
    "Stop_Hook_Loops":          {"skill": "insights",                     "command": "/insights",                            "dir": "lower",  "warn": 1,     "fail": 2,  "per": "session", "readonly": True, "auto_remediable": False, "weight": 2.0},
    # Simplify_Runs consolidated into Simplify_Age (2026-07-08 audit): it counted
    # its own remediation's invocations and passed on a single run.
    # DEMOTE 2026-08-01 (metric-gap remediation, phase A2, frozen criterion >=8
    # attempts AND 0 improved): simplify is 0/31 lifetime improved — advisory.
    "Simplify_Age":             {"skill": "simplify",                     "command": "/simplify",                            "dir": "lower",  "warn": 7,     "fail": 21, "weight": 1.0, "auto_remediable": False},
    # RATCHET 2026-07-11: the 2026-07-08 baseline ratchet was set at warn 634 (the
    # then-live backlog); the parity backlog has since drained to 1, so 634/697 was
    # absurdly loose — any regression up to 633 would still read PASS. Tightened to
    # warn 5 / fail 25 and auto-remediation re-enabled: /wiki demonstrably clears
    # small drifts at this scale (11/13 efficacy on vault work). Ratchet rule stays:
    # tighten (never loosen) as clean weeks accumulate.
    # DEMOTE 2026-08-01 (metric-gap remediation, phase A2): the 2026-07-11 re-enable
    # above has not held — wiki is now 0/24 lifetime improved across every metric it
    # maps to (frozen criterion >=8 attempts AND 0 improved) — advisory, never auto-fire.
    "Doc_Parity_Issues":        {"skill": "wiki",                         "command": "/wiki",                                "dir": "lower",  "warn": 5,     "fail": 25, "weight": 2.0, "auto_remediable": False},
    # Skills_Optimized + Skill_Promotions RETIRED 2026-07-19 (metric-surface review
    # Part E item 3): their sources (skill_improve_after_use_log.jsonl /
    # skill_promotion_log.jsonl) are never written on this host — both counters were
    # permanently dark. Re-add only together with live emitters.
    "Skill_Conflicts":          {"skill": "skill-consolidator",           "command": "/skill-consolidator",                  "dir": "lower",  "warn": 1,     "fail": 5, "weight": 1.0, "maturity": "DRY-RUN-GRADED", "mechanism": {"script": "skill_conflict_audit.py", "args": ["--json"], "read_only": True, "timeout_s": 120}},
    "MCP_Smoke_Fails":          {"skill": "mcp-setup",                    "command": "/mcp-setup",                           "dir": "lower",  "warn": 1,     "fail": 3, "weight": 1.0},
    # Knowledge vault health (arts/Knowledge group — cross-component integration)
    "Wiki_Health_Score":        {"skill": "wiki",                         "command": "/wiki"},
    "Wiki_Article_Count":       {},  # informational — volume, not a failure signal
    # warn 1->5 / fail 5->15 ratified 2026-07-12 (Wargame 01 Move 6 row 8): the librarian
    # nightly (01:30) FEEDS Knowledge/vault/raw/ by design, so warn=1 guaranteed a nightly
    # re-breach the morning after every distillation run.
    # DEMOTE 2026-08-01 (metric-gap remediation, phase A2, frozen criterion >=8
    # attempts AND 0 improved): wiki is 0/24 lifetime improved across its mapped
    # metrics — advisory, never auto-fire.
    "Raw_Pending":              {"skill": "wiki",                         "command": "/wiki",                                "dir": "lower",  "warn": 5,     "fail": 15, "weight": 1.0, "maturity": "DRY-RUN-GRADED", "mechanism": {"script": "wiki_compile.py", "args": [], "read_only": True, "timeout_s": 120}, "auto_remediable": False},
    "Wiki_Orphans":             {"skill": "wiki",                         "command": "/wiki",                                "dir": "lower",  "warn": 2,     "fail": 10, "weight": 1.0, "maturity": "DRY-RUN-GRADED", "mechanism": {"script": "wiki_link.py", "args": ["--json"], "read_only": True, "timeout_s": 120}, "auto_remediable": False},
    # OKF / <BRAND>³ knowledge health (scouts.knowledge_signals -> arts/Knowledge group).
    # Thresholds mirror verify_knowledge.py constants — change both together.
    "OKF_Conformance":          {"skill": "wiki",                         "command": "python3 Knowledge/okf/okf_tools.py validate Knowledge/vault --list 20", "dir": "higher", "warn": 95, "fail": 80, "auto_remediable": False, "weight": 1.0},  # validate lists offenders; fixing frontmatter is editorial
    "Orphan_Concepts":          {},  # informational until a baseline exists — threshold once history accumulates
    # DEMOTE 2026-07-19 (Part C): consolidate-memory is 0/12 lifetime — advisory
    # for both metrics it maps to until it demonstrates efficacy.
    "Archive_Ratio":            {"skill": "consolidate-memory",           "command": "/consolidate-memory",                  "dir": "lower",  "warn": 75,    "fail": 90, "weight": 0.5, "auto_remediable": False},
    "Index_Drift":              {"skill": "wiki",                         "command": "python3 Knowledge/okf/okf_tools.py index Knowledge/vault/me --root", "dir": "lower", "warn": 1, "fail": 10, "weight": 1.0},  # command regenerates the index -> actually fixes drift
    "Knowledge_Staleness_Days": {"skill": "consolidate-memory",           "command": "/consolidate-memory",                  "dir": "lower",  "warn": 60,    "fail": 180, "weight": 1.0, "auto_remediable": False},  # DEMOTE 2026-07-19: see Archive_Ratio
    # Meta — informational, not scored (no dir). Shows pillar instrumentation depth.
    "Instrumentation_Coverage": {"skill": "audit-mechanisms",             "command": "/audit-mechanisms"},
}

# Direction-only overrides for the 24h summary clause — used ONLY for improved/worsened labels.
# These metrics deliberately lack warn/fail thresholds (protective activities or informational
# counters) so they cannot go in METRIC_CONFIG.dir without breaking scoring. Separate lookup.
_24H_DIRECTION: dict[str, str] = {
    "Complexity_Weighted_Throughput": "higher",   # more throughput = more work done = good
    "Total_Cost":                     "lower",    # lower spend is better
    "Token_Spend":                    "lower",    # lower is better
    "Cost_Per_Task":                  "lower",    # lower is better
}

# Cumulative volume totals: their 24h rise is driven by how much work happened, not
# by quality — so the 24h clause reports their movement WITHOUT an improved/worsened
# verdict (calling a Total_Cost rise "worsened" reads as a regression when it's just
# more work). Efficiency judgments stay on the per-task / rate metrics.
_CUMULATIVE_24H_METRICS: set[str] = {
    "Total_Cost", "Token_Spend", "Tool_Calls", "Session_Count",
    "Complexity_Weighted_Throughput",
}

# Metrics whose 24h delta reads as dollars.
_MONEY_24H_METRICS: set[str] = {"Total_Cost", "Cost_Per_Task", "Estimated_Cost_Savings"}


def _fmt_24h_magnitude(metric: str, mag: float) -> str:
    """Human-readable unsigned magnitude for the 24h clause: dollars for cost metrics,
    M/K for large counts, plain otherwise."""
    if metric in _MONEY_24H_METRICS:
        return f"${mag:,.2f}"
    if mag >= 1_000_000:
        return f"{mag / 1e6:,.1f}M"
    if mag >= 10_000:
        return f"{mag / 1e3:,.1f}K"
    return f"{mag:,.0f}" if float(mag) == int(mag) else f"{mag:,.1f}"


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
    for k, v in METRIC_CONFIG.items()
    if "dir" in v and v.get("maturity") != "OBSERVE"
}
REMEDIATION: dict[str, dict] = {
    k: {"skill": v["skill"], "command": v["command"]}
    for k, v in METRIC_CONFIG.items() if "skill" in v
}

# Skills that operate on the *live* session's context — meaningless headless (there is
# nothing to compact/optimize). A reflex whose command maps to one of these is routed
# as session_hygiene: never spawned by the engine, the dashboard shows a "run it in your
# active session" hint instead of a headless button.
SESSION_HYGIENE_SKILLS = {"context-optimization", "compact"}


def remediation_kind(command, *, readonly, auto_remediable, explicit_kind=None):
    """Single source of truth: classify a reflex's manual remediation into one of four kinds.

      auto_fix        — a code-modifying skill that can move the metric; runs through the
                        staging → maker-checker → pytest pipeline (today's behavior).
      advisory        — a diagnostic/read-only skill; it runs but its value is the report it
                        prints, not a code change (finalStatus stays no_change).
      session_hygiene — a live-session skill (SESSION_HYGIENE_SKILLS); headless is a no-op.
      mis_route       — the configured skill structurally can't move the metric (the four
                        DO-NOT-USE METRIC_CONFIG entries); express this AT the metric via an
                        explicit ``kind`` so it stays DRY.

    ``explicit_kind`` (METRIC_CONFIG's ``kind`` field) always wins — that is how mis_route is
    declared. Otherwise: session_hygiene by skill, then advisory for readonly / non-auto-
    remediable, then auto_fix. Pure function; no I/O, no caller yet (Step 2 wires it in).
    """
    if explicit_kind:
        return explicit_kind
    skill = (command or "").lstrip("/").split()[0] if command else ""
    if skill in SESSION_HYGIENE_SKILLS:
        return "session_hygiene"
    if readonly or auto_remediable is False:
        return "advisory"
    return "auto_fix"


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

# Pillar placement of every graded metric — mirrors aggregate.py's REGISTRY rows
# and build_pillars injection sites (a metric injected into two pillars lists
# both). Instrumentation_Coverage's denominator is the FULL graded registry
# (audit S4 retune): an absent-source metric never gets an envelope, so the old
# envelopes-present denominator reported 100% coverage on every pillar while a
# third of the graded registry was dark — the one metric designed to catch data
# gaps could not see this gap class. A drift test asserts this map stays equal
# to METRIC_RULES' key set.
_GRADED_METRIC_PILLARS: dict[str, tuple[str, ...]] = {
    "Error_Rate": ("bow",),
    "Latency_P95": ("bow",),
    "Avg_Session_Turns": ("bow",),
    "Mechanism_Orphans": ("bow",),
    "Governance_Pass_Rate": ("bow",),
    "MCP_Smoke_Fails": ("bow",),
    "Open_CVEs": ("sword",),
    "Boundary_Violations": ("sword",),
    "Secrets_Detected": ("sword",),
    "Rule_Violations": ("sword",),
    "Skill_Routing_Adherence": ("sword",),
    "Deprecated_Deps": ("sword",),
    "Governance_Review_Findings": ("sword",),
    "Kill_Chains_Open": ("sword",),
    "Token_Execution_Density": ("brush",),
    "Local_Routing_Share": ("brush",),
    "Context_Cliff_Events": ("brush",),
    # Subagent_Efficiency_Index ungraded 2026-07-19 — removed from the graded map
    # with its dir rule (drift test asserts map == METRIC_RULES keys).
    "Chain_Depth_Avg": ("brush",),
    "Hardcoded_Path_Incidents": ("brush",),
    "Root_Hygiene_Issues": ("brush",),
    "Architecture_Scorecard_Grade": ("brush",),
    "Slop_Density": ("arts",),
    "Tool_Selection_Accuracy": ("arts",),
    "Tool_Arg_Correctness": ("arts",),
    "Tool_Response_Utilization": ("arts",),
    "Faithfulness_Score": ("arts",),
    "Refusal_Appropriateness": ("arts",),
    "Retrieval_Relevance": ("arts",),
    "Frustration_Signals": ("arts",),
    "Rework_Loops": ("arts",),
    "Stop_Hook_Loops": ("arts",),
    "Simplify_Age": ("arts",),
    "Doc_Parity_Issues": ("arts",),
    "Skill_Conflicts": ("arts",),
    "Raw_Pending": ("arts",),
    "Wiki_Orphans": ("arts",),
    "OKF_Conformance": ("arts",),
    "Archive_Ratio": ("arts",),
    "Index_Drift": ("arts",),
    "Knowledge_Staleness_Days": ("arts",),
}


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
                    # Remediation routing kind (same source of truth as the reflex cards) so the
                    # metric detail views (PillarPage, MetricModal) gate their run button the same
                    # way the reflex cards do — mis_route/session_hygiene get no headless button.
                    env["mitigation_kind"] = remediation_kind(
                        cfg["command"],
                        readonly=cfg.get("readonly", False),
                        auto_remediable=cfg.get("auto_remediable"),
                        explicit_kind=cfg.get("kind"),
                    )
                if "mechanism" in cfg and not env.get("is_simulated"):
                    env["mitigation_mechanism"] = cfg["mechanism"]
                if "dir" not in cfg:
                    continue
                total_gradeable += 1
                if env.get("is_simulated"):
                    continue
                v = _num(env.get("val"))
                if v is None:
                    # Unparseable/absent val on a graded, non-simulated metric:
                    # _health(None) returns 100.0, which would grade a broken
                    # value as a perfect PASS. Skip it like a simulated metric
                    # instead — no status, no contribution to the rollup.
                    continue
                if cfg.get("per") == "session":
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
        # Audit S4: the coverage denominator is the FULL graded registry for this
        # pillar, so an absent-source metric (no envelope at all) counts as
        # not-live instead of silently shrinking the denominator. max() keeps any
        # graded envelope not (yet) in the pillar map counted, so coverage can
        # never exceed 100%.
        registry_gradeable = sum(1 for pks in _GRADED_METRIC_PILLARS.values() if pk in pks)
        total_gradeable = max(registry_gradeable, total_gradeable)
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


def _env_of(pillars: dict, pk: str, key: str) -> dict:
    """Full envelope for a metric (empty dict when absent) — lets summaries honor
    the calibrated/data_gap honesty flags instead of asserting raw values as fact
    (the hero card already gates on these; prose must not leak what the card refuses
    to headline)."""
    for g in pillars.get(pk, {}).values():
        if key in g:
            return g[key]
    return {}


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
            # First time this metric was ever snapshotted — note it as newly tracked.
            # Keep the config-cased key; display casing is applied at render below.
            new_metrics.append(mk)
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
        # "moved up/down by" already carries the sign, so show the unsigned magnitude
        # formatted (was raw: "+100359411.0" instead of "100.4M" / "$10,499.31").
        mag = _fmt_24h_magnitude(m["name"], abs(m["delta"]))
        # Two classes get NO improved/worsened verdict on their raw 24h delta:
        #  - cumulative volume totals (spend, tokens, counts) rise as work happens;
        #  - per-session-judged metrics (cfg "per":"session"), whose RAW-total delta
        #    is confounded by session volume (more sessions -> higher total even when
        #    the judged per-session rate is flat). Calling either "worsened" misleads;
        #    efficiency verdicts stay on true rate metrics (Cost_Per_Task, density).
        per_session_metric = METRIC_CONFIG.get(m["name"], {}).get("per") == "session"
        outcome = None
        if (m["good"] is not None and m["name"] not in _CUMULATIVE_24H_METRICS
                and not per_session_metric):
            outcome = "improved" if m["good"] else "worsened"
        suffix = f" ({outcome})" if outcome is not None else ""
        parts.append(f"{label} moved {direction} by {mag}{suffix}")

    # Newly tracked metrics (no pre-24h baseline): surface the most important ones.
    # Priority: graded metrics (have a rule) first, then alphabetical. Cap at 2 to avoid noise.
    if new_metrics:
        # mk is the config-cased key as stored above ("Keep the config-cased key");
        # a prior version here lower-cased graded_keys but compared mk unchanged,
        # so the membership test always failed the case check and graded metrics
        # were silently dropped from the "now tracking" summary (2026 sweep, PR #79).
        graded_new = [mk for mk in new_metrics if METRIC_CONFIG.get(mk, {}).get("dir")]
        notable = (graded_new or new_metrics)[:2]
        if notable:
            labels = ", ".join(mk.replace("_", " ").lower() for mk in notable)
            parts.append(f"now tracking (no prior baseline): {labels}")

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
    """Plain-language pillar narratives. Sentences whose metrics are missing ("—")
    are dropped instead of interpolating the placeholder into prose
    ("recovering via fallbacks —% of the time"); dollar figures are rounded."""
    store = store or default_history_path()

    def v(pk: str, k: str):
        raw = _val(pillars, pk, k)
        return None if raw in ("—", "", None) else raw

    def money(x) -> str | None:
        n = _num(x)
        return f"{n:,.2f}" if n is not None else None

    def count(x) -> str | None:
        if x is None:
            return None
        n = _num(x)
        if n is None:
            return str(x)
        if abs(n) >= 1_000_000:  # 185,146,296 tokens reads as noise; 185.1M doesn't
            return f"{n / 1e6:,.1f}M"
        return f"{n:,.0f}" if float(n) == int(n) else f"{n:,.1f}"

    def is_zero(x) -> bool:
        n = _num(x)
        return n is not None and n == 0

    def plur(x, singular: str) -> str:
        """Regular +s plural agreeing with a count: '1 vector' / '3 vectors'."""
        n = _num(x)
        return singular if n == 1 else singular + "s"

    def sent(template: str, *vals) -> str | None:
        return None if any(x is None for x in vals) else template.format(*vals)

    def join(parts: list) -> str:
        return " ".join(p for p in parts if p).strip()

    # Per-session-graded metrics (cfg "per": "session") are JUDGED as val/sessions
    # against their warn/fail bar (annotate() line ~406). The narrative must report
    # that same per-session RATE, not the raw cumulative total — "938 rule violations"
    # across 1026 sessions is <1/session and passing, but the bare total reads alarming.
    sessions = _session_count(pillars)

    def psr(pk: str, k: str) -> tuple:
        """(per-session rate str, total str) for a per-session metric, else (None, None)."""
        tot = _num(v(pk, k))
        if tot is None or sessions <= 0:
            return None, None
        return f"{tot / sessions:.1f}", count(tot)

    t = lambda pk: _trend_clause(pillars, pk, scores)
    rec = lambda pk: _recommendations(pk, scores)
    def h24(pk: str) -> str:
        c = _24h_clause(pk, store)
        return (c + " ") if c else ""

    # bow ──────────────────────────────────────────────────────────────────────
    ts_saved = v("bow", "Estimated_Agent_Time_Saved")
    ts_env = _env_of(pillars, "bow", "Estimated_Agent_Time_Saved")
    if ts_saved is not None and is_zero(ts_saved):
        # a 0.0-hours lead reads as failure; the truth is the estimate isn't calibrated yet
        bow_lead = ("This pillar has no automated time savings recorded yet — "
                    "meditation calibration samples are still accruing.")
    elif ts_saved is not None and ts_env.get("calibrated", True) is False:
        # Honesty parity with the hero card, which shows the INTERIM fallback for
        # exactly this state: an uncalibrated estimate is labeled, never asserted
        # as measurement. detail carries the reducer's sample-progress receipt.
        note = ts_env.get("detail") or "estimate — calibration samples still accruing"
        bow_lead = (f"This pillar tracked an estimated {ts_saved} hours of agent "
                    f"execution time saved by automated task runs ({note}).")
    else:
        bow_lead = sent("This pillar tracked {} hours of agent execution time saved by automated task runs.",
                        ts_saved) \
            or "This pillar tracked agent operations across this window."
    bow_tasks = sent("The agent completed work scoring {} complexity-weighted points across {} work sessions, passing verification {}% of the time with a {}% error rate.",
                     count(v("bow", "Complexity_Weighted_Throughput")), v("bow", "Session_Count"),
                     v("bow", "Governance_Pass_Rate"), v("bow", "Error_Rate")) \
        or sent("The agent completed work scoring {} complexity-weighted points across {} work sessions.",
                count(v("bow", "Complexity_Weighted_Throughput")), v("bow", "Session_Count"))
    bow_tools = sent("It reached for its tools {} times.", count(v("bow", "Tool_Calls")))
    bow_turns = sent("Most sessions took about {} back-and-forth turns.", v("bow", "Avg_Session_Turns"))

    # sword ────────────────────────────────────────────────────────────────────
    kcd = v("sword", "Kill_Chains_Disrupted")
    vec = plur(kcd, "vector")
    sword_lead = sent(f"This pillar tracked {{}} distinct threat {vec} intercepted and disrupted (with {{}} pending proposals).",
                      kcd, v("sword", "Pending_Chain_Proposals")) \
        or sent(f"This pillar tracked {{}} distinct threat {vec} intercepted and disrupted.", kcd) \
        or "This pillar tracked the security posture of the agent across this window."
    sword_vuln = sent("Right now, there are {} open CVEs in the dependency tree and {} leaked passwords or keys.",
                      v("sword", "Open_CVEs"), v("sword", "Secrets_Detected")) \
        or sent("Right now, there are {} open CVEs in the dependency tree.", v("sword", "Open_CVEs")) \
        or sent("There are {} leaked passwords or keys.", v("sword", "Secrets_Detected"))
    rv_rate, rv_tot = psr("sword", "Rule_Violations")  # judged per session, not by the raw total
    conduct = [c for c in (
        sent("stepped out of bounds {} times", v("sword", "Boundary_Violations")),
        sent("averaged {} house-rule violations per session ({} total)", rv_rate, rv_tot),
        # blocks are the guard WORKING, not the agent misbehaving — phrase accordingly
    ) if c]
    sword_conduct = ("The agent " + (", ".join(conduct[:-1]) + ", and " + conduct[-1]
                                     if len(conduct) > 1 else conduct[0]) + ".") if conduct else None

    # brush ────────────────────────────────────────────────────────────────────
    raw_savings = v("brush", "Estimated_Cost_Savings")
    sv_env = _env_of(pillars, "brush", "Estimated_Cost_Savings")
    grade = v("brush", "Architecture_Scorecard_Grade")
    local_share = v("brush", "Local_Routing_Share")
    if raw_savings is not None and is_zero(raw_savings):
        # $0 here = no ADDITIONAL week-over-week efficiency gain, NOT "saved nothing".
        # Ongoing savings from local routing are real and steady, so a flat delta
        # reads as a broken metric unless we surface the local-routing share (the
        # actual cost-avoidance lever, e.g. caveman/quota-driven local models).
        if local_share is not None and not is_zero(local_share):
            brush_lead = sent("Cost-per-task held about flat vs last week, so there's no additional week-over-week saving to bank — but {}% of work already runs on local models, avoiding cloud spend every week (code-tidiness grade {}).",
                              local_share, grade) \
                or sent("Cost-per-task held about flat vs last week; {}% of work already runs on local models, avoiding cloud spend.", local_share)
        else:
            brush_lead = sent("Cost-per-task held about flat vs last week — no additional efficiency gain to bank (code-tidiness grade {}).", grade) \
                or "This pillar tracked no additional cost-per-task efficiency gain vs last week."
    elif raw_savings is not None and (sv_env.get("calibrated", True) is False or sv_env.get("data_gap")):
        # same honesty rule as bow_lead: no calibrated baseline -> labeled estimate
        brush_lead = sent("This pillar tracked an estimated ${} saved vs last week — no calibrated cost baseline yet (code-tidiness grade {}).",
                          money(raw_savings), grade) \
            or "This pillar tracked spend efficiency across this window."
    else:
        brush_lead = sent("This pillar tracked ${} saved from cost-per-task improvement vs last week at this week's task volume (with a code-tidiness grade of {}).",
                          money(raw_savings), grade) \
            or sent("This pillar tracked a code-tidiness grade of {}.", grade) \
            or "This pillar tracked spend efficiency across this window."
    brush_spend = sent("The agent spent ${} in total, about ${} per task, using {} tokens.",
                       money(v("brush", "Total_Cost")), money(v("brush", "Cost_Per_Task")),
                       count(v("brush", "Token_Spend")))
    brush_hygiene = sent("It left {} hard-coded paths and {} messy-folder issues to clean up.",
                         v("brush", "Hardcoded_Path_Incidents"), v("brush", "Root_Hygiene_Issues"))

    # arts ─────────────────────────────────────────────────────────────────────
    craft = v("arts", "Craft_Improvements")
    cr_env = _env_of(pillars, "arts", "Craft_Improvements")
    if craft is not None and is_zero(craft):
        arts_lead = ("This pillar logged no craft improvements yet this week "
                     "(skill promotions and completed arts deliverables count here"
                     + ("; the skill-promotions source is currently dark" if cr_env.get("data_gap") else "")
                     + ").")
    elif craft is not None and cr_env.get("data_gap"):
        # promotions numerator is dark (data_gap) -> the count is a floor, not a total
        arts_lead = sent("This pillar logged at least {} craft improvement(s) this week "
                         "(completed arts deliverables; the skill-promotions source is currently dark).",
                         craft) \
            or "This pillar logged the craft quality of the agent's output across this window."
    else:
        arts_lead = sent("This pillar logged {} craft improvements this week (skill promotions plus completed arts deliverables).",
                         craft) \
            or "This pillar logged the craft quality of the agent's output across this window."
    arts_slop = sent("The agent's writing carried {} bits of filler per 1,000 words.", v("arts", "Slop_Density"))
    fs_rate, fs_tot = psr("arts", "Frustration_Signals")  # both judged per session, not by raw totals
    rl_rate, rl_tot = psr("arts", "Rework_Loops")
    arts_friction = join([
        sent("The user showed frustration about {} times per session ({} total) and asked for redos about {} times per session ({} total).",
             fs_rate, fs_tot, rl_rate, rl_tot),
        sent("The cleanup pass last ran {} days ago and {} docs are out of date.",
             v("arts", "Simplify_Age"), v("arts", "Doc_Parity_Issues")),
    ]) or None
    arts_vault = sent("The knowledge vault holds {} curated articles at a health score of {}/100.",
                      v("arts", "Wiki_Article_Count"), v("arts", "Wiki_Health_Score"))

    return {
        "bow": join([bow_lead, bow_tasks, bow_tools, bow_turns, h24("bow") + t("bow"), rec("bow")]),
        "sword": join([sword_lead, sword_vuln, sword_conduct, h24("sword") + t("sword"), rec("sword")]),
        "brush": join([brush_lead, brush_spend, brush_hygiene, h24("brush") + t("brush"), rec("brush")]),
        "arts": join([arts_lead, arts_slop, arts_friction, arts_vault, h24("arts") + t("arts"), rec("arts")]),
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
    """Append one live history row — locked and deduped.

    The old bare `open(.., "a")` let concurrent refreshers write byte-identical
    duplicate rows, which biased calibrate() percentiles toward whatever was
    happening at refresh time (2026-07-26 audit). Rows now carry the same
    `week` key backfill_history writes, so the file has one schema, not two."""
    from datetime import datetime as _dt

    store.parent.mkdir(parents=True, exist_ok=True)
    try:
        week = _dt.fromisoformat(timestamp.replace("Z", "+00:00")).strftime("%G-W%V")
    except (ValueError, AttributeError):
        week = None
    # kind:"live" distinguishes this 30-day-window snapshot row from backfill's
    # single-ISO-week rows — same key set, different population; consumers that
    # need a weekly baseline (e.g. _get_prior_week_val) must skip live rows.
    row: dict = {"ts": timestamp, "week": week, "kind": "live", "values": current}
    try:
        import fcntl
    except ImportError:  # non-POSIX host: fall back to unlocked append
        fcntl = None
    with store.open("a+", encoding="utf-8") as fh:
        locked = False
        if fcntl is not None:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX)
                locked = True
            except OSError:  # filesystem without POSIX locks (SMB/network mount)
                pass
        try:
            fh.seek(0)
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
            if lines:
                try:
                    last = json.loads(lines[-1])
                    if last.get("ts") == timestamp and last.get("values") == current:
                        return  # identical concurrent write — drop the duplicate
                except json.JSONDecodeError:
                    pass
            fh.seek(0, os.SEEK_END)
            fh.write(json.dumps(row) + "\n")
            # Flush + fsync BEFORE releasing the lock — the TextIOWrapper buffer
            # otherwise lands after LOCK_UN and the lock protects nothing
            # (pre-push adversarial review 2026-07-26).
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            if locked and fcntl is not None:
                fcntl.flock(fh, fcntl.LOCK_UN)
