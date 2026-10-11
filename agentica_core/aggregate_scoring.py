"""Scoring helpers for the aggregator: environment envelopes, verifier-metric derivation, the pillar-assembly steps of build_pillars and per-project score buckets (split from aggregate.py)."""
from __future__ import annotations

import re
from collections import Counter

from . import insights
from .telemetry import validate_metric
from .aggregate_metrics import build_tier_mix


def _env(val, tier, *, is_percent=False, is_count=False, simulated=False):
    calibrated = True
    delta = "0"
    data_gap = False
    detail = None
    estimate_by_design = False
    state = None

    if isinstance(val, dict):
        calibrated = val.get("calibrated", True)
        delta = str(val.get("week_delta", "0"))
        data_gap = val.get("data_gap", False)
        detail = val.get("detail")
        estimate_by_design = val.get("estimate_by_design", False)
        state = val.get("state")
        if val.get("error"):
            simulated = True
            val = None
        else:
            val = val.get("val")

    trend = "neutral"
    if delta != "0":
        try:
            d_val = float(delta)
            if d_val > 0:
                trend = "up"
            elif d_val < 0:
                trend = "down"
        except ValueError:
            pass

    env = {
        "val": ("—" if val is None else str(val)),
        "delta": delta,
        "trend": trend,
        "history": [],
        "is_percent": is_percent,
        "is_count": is_count,
        "is_simulated": simulated,
        "tier": tier,
        "timestamp": "",
        "calibrated": calibrated,
    }
    if data_gap:
        env["data_gap"] = True
    if detail:
        env["detail"] = detail
    if state:
        env["state"] = state
    if estimate_by_design:
        # permanent honest "est." badge (real counts x asserted coefficients,
        # no sample source) — distinct from calibrated=False "awaiting samples"
        env["estimate_by_design"] = True
    return env


def _set(pillars, pillar, group, key, env):
    validate_metric(env)
    pillars[pillar].setdefault(group, {})[key] = env


def derive_verifier_metrics(results: list[dict]) -> dict:
    """Real, AUTO metrics from a platform's verifier results (label substring mapping)."""
    total = len(results)
    ok = sum(1 for r in results if r["status"] == "OK")

    def fails(*subs):
        return sum(1 for r in results if r["status"] == "FAIL"
                   and any(s in r["label"].lower() for s in subs))

    return {
        "Governance_Pass_Rate": round(100 * ok / total, 1) if total else None,
        "Verifier_Failures": sum(1 for r in results if r["status"] == "FAIL"),
        "Boundary_Violations": fails("boundary", "archive"),
        "Hardcoded_Path_Incidents": fails("path-authority", "hardcoded"),
        "Root_Hygiene_Issues": sum(1 for r in results if r["status"] != "OK" and "hygiene" in r["label"].lower()),
        "Config_Drift": fails("drift", "anti_drift"),
    }


_PROJECT_ALIASES: dict[str, list[str]] = {
    "Jarvis-Intelligence-Hub": ["HUB", "HUD"],
    # "History and read list app" was the old folder name before it was renamed
    # to "Dendrite app". All Codex sessions from May 2026 used the old name.
    "Dendrite app": ["History and read list app"],
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _score_project_bucket(recs: list[dict], plats: Counter) -> dict:
    """Build the has-data project-entry dict (scores/metrics/tier_mix) for a list of
    records. Shared by real project folders and the `_meta` unmatched-tag bucket in
    `build_project_scores` so both stay identical in shape."""
    from . import aggregate as _agg
    # Git-hook telemetry (platform="git", see _GIT_TELEMETRY above) carries presence +
    # commit stats but no session metrics — it must feed `records`/`tier_mix` for
    # presence detection only, never build_pillars(), or its zero-valued, no-real-
    # session-id records get counted as individual "sessions" and inflate Session_Count
    # while diluting Token_Execution_Density / Avg_Session_Turns.
    telemetry_recs = [r for r in recs if r.get("platform") != "git"]
    pillars = _agg.build_pillars(telemetry_recs)
    scores = insights.annotate(pillars)
    metrics: dict[str, float] = {}
    for groups in pillars.values():
        for group in groups.values():
            for mk, env in group.items():
                if env.get("is_simulated"):
                    continue
                n = insights._num(env.get("val"))
                if n is not None:
                    metrics[mk] = n
    return {
        "platform": plats.most_common(1)[0][0] if plats else "",
        "records": len(recs),
        "has_data": True,
        # Pass rate (100 × passing/graded), not a weighted mean — decomposable
        # count ratio per the de-aggregation doctrine (score field removed 2026-07-19).
        "scores": {
            k: round(100 * v["rollup"]["passing"] / v["rollup"]["graded"], 1)
            if v["rollup"]["graded"] else 0
            for k, v in scores.items()
        },
        "metrics": metrics,
        "tier_mix": build_tier_mix(recs),
    }


def _registry_pillars(records: list[dict], pillar_names: tuple, registry: list) -> dict:
    """Run every REGISTRY reducer over `records` and seed the per-pillar envelope tree."""
    pillars: dict[str, dict] = {p: {} for p in pillar_names}
    for pillar, group, key, fn, live_tier, is_pct, is_cnt in registry:
        val = fn(records) if fn else None
        # Tier honesty for dict reducers (audit S1): unwrap BEFORE the simulated
        # check. A dead-source reducer returning {"val": None} without an "error"
        # key must grade SIMULATED — never as a live metric, where _health(None)
        # would inject a perfect 100 into the pillar score.
        inner = val.get("val") if isinstance(val, dict) else val
        # A reported state (Mechanism_Liveness idle_by_design) is an observation with no
        # grade, not a dead source: keep its live tier and let annotate skip it.
        simulated = inner is None and not (isinstance(val, dict) and val.get("state"))
        env = _env(val, "SIMULATED" if simulated else live_tier,
                   is_percent=is_pct, is_count=is_cnt, simulated=simulated)
        validate_metric(env)  # tier-honesty contract
        pillars[pillar].setdefault(group, {})[key] = env
    return pillars


def _apply_measurement_signals(pillars: dict, measurement_signals: dict, knowledge_metrics) -> None:
    for key, value in measurement_signals.items():
        if key != "Cache_Hit_Rate" and key not in knowledge_metrics.RETRIEVAL_KEYS:
            continue
        missing = value.get("val") is None
        group = "Token Efficiency" if key == "Cache_Hit_Rate" else "Knowledge Retrieval"
        _set(pillars, "brush", group, key, _env(
            value, "SIMULATED" if missing else "AUTO", simulated=missing,
            is_percent=key.endswith("Hit_Rate"),
            is_count=key in {"Embedding_Cache_Hits", "Embedding_Cache_Misses",
                            "Embedding_Cache_Errors", "Retrieval_Observations",
                            "Retrieval_Search_Failures"}))


def _annotate_failing_platforms(pillars: dict, verifier_results: list[dict]) -> None:
    """Verifier_Failures consolidated into Governance_Pass_Rate (2026-07-08 audit):
    both rows read the same verifier results (rate = OK/total); the failing-
    platform drill-down now rides on the survivor's envelope."""
    failing_platforms = sorted({
        str(r.get("platform")) for r in verifier_results
        if r.get("status") == "FAIL" and r.get("platform")
    })
    if failing_platforms:
        gpr = pillars["bow"]["Governance"]["Governance_Pass_Rate"]
        gpr["failure_platforms"] = failing_platforms
        gpr["mitigation_command"] = f"python -m agentica_core.doctor {failing_platforms[0]}"
        gpr["mitigation_skill"] = "doctor"


# knowledge-layer scouts (platform-independent, AUTO) — arts/Knowledge group.
# (signal key, metric name, _env flags); applied in this order.
_KNOWLEDGE_ROWS = (
    ("okf_conformance_pct", "OKF_Conformance", {"is_percent": True}),
    ("orphan_concepts", "Orphan_Concepts", {"is_count": True}),
    ("archive_ratio_pct", "Archive_Ratio", {"is_percent": True}),
    ("index_drift", "Index_Drift", {"is_count": True}),
    ("knowledge_staleness_days", "Knowledge_Staleness_Days", {}),
    # SOJI: vault link-integrity counts (Execution/soji_scan.py -> Data/soji/memory.findings.json)
    ("soji_broken_links", "Soji_Broken_Links", {"is_count": True}),
    ("soji_orphan_notes", "Soji_Orphan_Notes", {"is_count": True}),
)


def _apply_knowledge_signals(pillars: dict, k: dict) -> None:
    for signal, name, flags in _KNOWLEDGE_ROWS:
        if signal in k:
            _set(pillars, "arts", "Knowledge", name, _env(k[signal], "AUTO", **flags))


_COUNT = {"is_count": True}
# Security telemetry the hooks already emit (read from <runtime>/data); converts SIMULATED -> AUTO.
# (signal key, pillar, group, metric name, _env flags, scan-incomplete detail or None); applied in
# this order. Rule_Violations is DERIVED from per-session telemetry (see REGISTRY), so it has no
# row here: per-session source enables the tier/project breakdown.
# Retired rows (removal, never faking):
#   - Canary_Failures + Security_Scorecard 2026-07-11 (C/D/F plan step 5): dead emitters, covered by
#     Gate_Canary_Fault, Guardrail_Blocks and Secrets_Detected.
#   - Loop_Breaker_Fires 2026-07-19 (metric-surface review Part E item 3): loop_breaker_state.json is
#     never written on this host, so both injections (sword/Reliability, bow/Reliability) were dead.
#   - Skills_Optimized, Skill_Promotions, Secret_Scrubs, guardrail_blocks 2026-07-19: JSONL sources
#     are never written or absent on this host; Secrets_Detected is the live secrets metric.
#   - Kill_Chain_Candidates 2026-07-08 audit: consolidated into Pending_Chain_Proposals (the discovery
#     scout still runs via scouts.security_signals; only the duplicate metric row is gone).
# Scheduled_Job_Failures asks "is the fleet actually succeeding?", which no other metric asked:
# Mechanism_Orphans covers wiring, Mechanism_Liveness counts runs, and the launchd_stale check uses
# log mtime — blind to a job that runs on time and fails. Static_Wiring_Orphans (find_orphans.py's
# static dataflow audit) is informational only: its live baseline is double digits (unadjudicated),
# so grading it before triage would read permanently red.
_SECURITY_ROWS = (
    ("gate_canary_fault", "sword", "Audit Trail", "Gate_Canary_Fault", _COUNT, None),
    ("mechanism_orphans", "bow", "Autonomic", "Mechanism_Orphans", _COUNT, None),
    ("scheduled_job_failures", "bow", "Autonomic", "Scheduled_Job_Failures", _COUNT, None),
    ("static_wiring_orphans", "bow", "Autonomic", "Static_Wiring_Orphans", _COUNT, None),
    ("doc_parity_issues", "arts", "Docs", "Doc_Parity_Issues", _COUNT, None),
    ("scorecard_grade", "brush", "Architecture", "Architecture_Scorecard_Grade", {}, None),
    ("open_cves", "sword", "Vulnerability", "Open_CVEs", _COUNT,
     "Dependency vulnerability scan incomplete; zero is not established."),
    ("deprecated_deps", "sword", "Supply Chain", "Deprecated_Deps", _COUNT,
     "Dependency outdated scan incomplete; zero is not established."),
    ("skill_conflicts", "arts", "Craft", "Skill_Conflicts", _COUNT, None),
    # AUTO-016: Knowledge Prompted — memory_recall autonomic events (knowledge/lessons/
    # context docs surfaced this run), read from Data/telemetry/autonomic_events.jsonl.
    ("knowledge_prompted", "arts", "Knowledge", "Knowledge_Prompted", _COUNT, None),
    ("mcp_smoke_fails", "bow", "Activity", "MCP_Smoke_Fails", _COUNT, None),
    # GOVERNANCE-001: adversarial governance code review findings (CRITICAL+HIGH)
    ("governance_findings_total_ch", "sword", "Governance", "Governance_Review_Findings", _COUNT, None),
    # AUTO-001: Config Drift Rate — weekly count of config-file changes
    ("config_drift_rate", "bow", "Governance", "Config_Drift_Rate", _COUNT, None),
    # AUTO-007: Vulnerability Window — days the system has been exposed to known CVEs
    ("vulnerability_window_days", "sword", "Vulnerability", "Vulnerability_Window_Days", {"is_count": False}, None),
)


def _apply_security_signals(pillars: dict, s: dict) -> None:
    for signal, pillar, group, name, flags, scan_gap in _SECURITY_ROWS:
        if signal in s:
            _set(pillars, pillar, group, name, _env(s[signal], "AUTO", **flags))
        elif scan_gap and s.get("dependency_scanner_failures", 0):
            _set(pillars, pillar, group, name, _env(
                {"val": None, "data_gap": True, "detail": scan_gap},
                "SIMULATED", is_count=True, simulated=True))


def _apply_vault_health(pillars: dict, vault: dict | None) -> None:
    """Knowledge vault health — cross-component integration (Knowledge → arts pillar)."""
    if vault:
        _set(pillars, "arts", "Knowledge", "Wiki_Health_Score",
             _env(vault["Wiki_Health_Score"], "AUTO"))
        _set(pillars, "arts", "Knowledge", "Wiki_Article_Count",
             _env(vault["Wiki_Article_Count"], "AUTO", is_count=True))
        _set(pillars, "arts", "Knowledge", "Raw_Pending",
             _env(vault["Raw_Pending"], "AUTO", is_count=True))
        _set(pillars, "arts", "Knowledge", "Wiki_Orphans",
             _env(vault["Wiki_Orphans"], "AUTO", is_count=True))
    else:
        for key in ("Wiki_Health_Score", "Wiki_Article_Count", "Raw_Pending", "Wiki_Orphans"):
            _set(pillars, "arts", "Knowledge", key, _env(None, "SIMULATED", is_count=(key != "Wiki_Health_Score"), simulated=True))
