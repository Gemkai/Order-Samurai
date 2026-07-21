"""Cross-metric compound correlation rules.

Rules fire synthetic reflexes when multiple metrics degrade simultaneously,
signaling systemic failure modes that per-metric reflexes would miss.

Called by refresh_dashboard.py after build_pillars() returns the pillar dict.
Returns a list of reflex-shaped dicts suitable for injection into wid_payload['reflexes'].

Design notes:
- Rules are additive: a single payload can contain multiple correlation reflexes
- Dedup by `id`: if the same rule fires on consecutive refreshes, only one reflex appears
- Correlation reflexes have source="correlation_engine" — easily filtered in the dashboard
- Only LIVE (non-simulated) metric values are evaluated; SIMULATED values skip condition checks
"""
from __future__ import annotations

from typing import Any

from . import insights


# ---------------------------------------------------------------------------
# Rule schema
# ---------------------------------------------------------------------------
#
# CORRELATION_RULES: list of rule dicts, each with:
#   id        — unique reflex id (stable across refreshes)
#   label     — human description
#   conditions— list of {pillar, group, metric, op, threshold}; ALL must be true to fire
#   command   — skill command to auto-remediate (must match a key in insights.py METRIC_CONFIG)
#   message   — shown on the reflex card in the dashboard
#   tier      — reflex severity: CRITICAL | HIGH | MEDIUM
#
# Conditions:
#   pillar  — bow | sword | brush | arts
#   group   — must match the exact group string in REGISTRY (e.g. "Activity", "Vulnerability")
#   metric  — must match the exact key string (e.g. "Error_Rate", "Open_CVEs")
#   op      — "gt" | "lt" | "eq"
#   threshold — numeric; compared against the metric's "val" field (parsed as float)
#
# ---------------------------------------------------------------------------

CORRELATION_RULES: list[dict[str, Any]] = [
    {
        "id": "correlation:ops_and_security_degraded",
        "label": "Ops + Security co-degraded",
        "conditions": [
            {"pillar": "bow",   "group": "Activity",     "metric": "Error_Rate",  "op": "gt", "threshold": 0.1},
            {"pillar": "sword", "group": "Vulnerability", "metric": "Vulnerability_MTTR", "op": "gt", "threshold": 2.0},
        ],
        "command": "/investigate",
        "message": "Error rate and vulnerability MTTR elevated simultaneously — investigate for systemic failure",
        "tier": "CRITICAL",
    },
    {
        "id": "correlation:cost_and_quality_tradeoff",
        "label": "Cost rising + Quality degrading",
        "conditions": [
            {"pillar": "brush", "group": "Token Efficiency", "metric": "Total_Cost",         "op": "gt", "threshold": 50},
            {"pillar": "arts",  "group": "Interaction",      "metric": "Frustration_Signals", "op": "gt", "threshold": 2},
        ],
        # SENSEI-6: was /model-selector — ~10 consecutive improved:false firings
        # (18.8% lifetime efficacy) with the gate itself mis-evaluating. Cost
        # visibility routes through the readonly diagnostic channel instead.
        "command": "/cost-breakdown-audit",
        "message": "High token cost concurrent with rising frustration signals — run the cost breakdown to find the driver",
        "tier": "HIGH",
    },
    {
        "id": "correlation:governance_and_security_gap",
        "label": "Governance failures + Security boundary violations",
        "conditions": [
            {"pillar": "bow",   "group": "Governance",    "metric": "Governance_Pass_Rate",   "op": "lt", "threshold": 80},
            {"pillar": "sword", "group": "Code Security", "metric": "Boundary_Violations",    "op": "gt", "threshold": 0},
        ],
        "command": "/verifier-repair",
        "message": "Low governance pass rate with active boundary violations — verifier repair needed",
        "tier": "HIGH",
    },
    {
        "id": "correlation:slop_and_docs_co_degraded",
        "label": "Output quality + documentation both degrading",
        "conditions": [
            {"pillar": "arts", "group": "Output Quality", "metric": "Slop_Density",       "op": "gt", "threshold": 15},
            {"pillar": "arts", "group": "Docs",           "metric": "Doc_Parity_Issues",  "op": "gt", "threshold": 5},
        ],
        "command": "/humanizer",
        "message": "Slop density elevated while documentation is falling behind — output quality and doc parity both need attention",
        "tier": "HIGH",
    },
    {
        "id": "correlation:vault_health_crisis",
        "label": "Knowledge vault health degrading with backlog accumulating",
        "conditions": [
            {"pillar": "arts", "group": "Knowledge", "metric": "Wiki_Health_Score", "op": "lt", "threshold": 80},
            {"pillar": "arts", "group": "Knowledge", "metric": "Raw_Pending",       "op": "gt", "threshold": 5},
        ],
        "command": "/wiki",
        "message": "Vault health score dropping while raw notes accumulate uncompiled — run wiki compile",
        "tier": "HIGH",
    },
]


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def _get_val(pillars: dict, pillar: str, group: str, metric: str) -> float | None:
    """Extract a numeric metric value from the pillar dict. Returns None if absent or SIMULATED.

    Per-session metrics (METRIC_RULES per == "session") are normalized by Session_Count —
    the same normalization annotate() grades with. SENSEI-6 found this gate comparing
    Frustration_Signals' raw cumulative value (208) against a per-session threshold (2),
    firing the correlation while the true reading (~0.1/session) failed its own gate."""
    try:
        env = pillars[pillar][group][metric]
        if env.get("is_simulated"):
            return None  # Never evaluate simulated values — they're fabricated
        raw = env.get("val", "—")
        if raw == "—":
            return None
        val = float(str(raw).replace("%", "").replace(",", "").strip())
        if (insights.METRIC_RULES.get(metric) or {}).get("per") == "session":
            sessions = _get_raw_num(pillars, "bow", "Activity", "Session_Count")
            if not sessions:
                return None  # can't normalize -> can't honestly evaluate the condition
            val = val / sessions
        return val
    except (KeyError, TypeError, ValueError):
        return None


def _get_raw_num(pillars: dict, pillar: str, group: str, metric: str) -> float | None:
    """Raw (un-normalized) numeric read used for the Session_Count denominator."""
    try:
        env = pillars[pillar][group][metric]
        if env.get("is_simulated"):
            return None
        return float(str(env.get("val", "—")).replace(",", "").strip())
    except (KeyError, TypeError, ValueError):
        return None


def _check_condition(pillars: dict, cond: dict) -> bool:
    val = _get_val(pillars, cond["pillar"], cond["group"], cond["metric"])
    if val is None:
        return False
    op, thr = cond["op"], float(cond["threshold"])
    if op == "gt":
        return val > thr
    if op == "lt":
        return val < thr
    if op == "eq":
        return val == thr
    return False


def evaluate(pillars: dict) -> list[dict[str, Any]]:
    """Return synthetic reflex entries for any rules whose conditions all match.

    Caller is responsible for deduplicating by `id` before writing to wid_payload.
    """
    fired: list[dict[str, Any]] = []
    for rule in CORRELATION_RULES:
        if all(_check_condition(pillars, c) for c in rule["conditions"]):
            fired.append({
                "id": rule["id"],
                "tier": rule["tier"],
                "command": rule["command"],
                "status": "active",
                "source": "correlation_engine",
                "message": rule["message"],
                "category": "correlation",
            })
    return fired
