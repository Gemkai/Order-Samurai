"""Reflexes — the dashboard's alert layer, built on the Nudge system. A reflex is the
Samurai's quick response to danger: a tier-rated alert raised either by a live metric
crossing its threshold (source="metric") or by a configured Nudge rule (source="nudge").
"""
from __future__ import annotations

import json
import os
import statistics
from pathlib import Path

from . import insights
from .maturity import resolve_maturity

SAMURAI_ROOT_ENV = os.environ.get("SAMURAI_ROOT")
if SAMURAI_ROOT_ENV:
    SAMURAI_ROOT_DIR = Path(SAMURAI_ROOT_ENV).expanduser()
else:
    samurai_default = Path.home() / ".samurai"
    claude_default = Path.home() / ".claude"
    if samurai_default.exists() or not claude_default.exists():
        SAMURAI_ROOT_DIR = samurai_default
    else:
        SAMURAI_ROOT_DIR = claude_default

_NUDGES_JSON = Path(os.environ.get("NUDGE_JSON_PATH", str(SAMURAI_ROOT_DIR / "nudges.json")))
_NUDGE_STATE = SAMURAI_ROOT_DIR / "nudge-state.json"

_THIS = Path(__file__).resolve()
_local_root = _THIS.parents[1]
if (_local_root / "config").exists() and not (_local_root / "Order Samurai").exists():
    _default_root = _local_root
else:
    _default_root = _local_root / "Order Samurai"
_REFLEX_ENGINE_STATE = Path(os.environ.get("ORDER_SAMURAI_ROOT", str(_default_root))) / "state" / "reflex_engine_state.json"

_TIER_BY_GRADE = {"F": "CRITICAL", "D": "HIGH", "C": "MEDIUM"}
_PILLAR_LABEL = {"bow": "Bow", "sword": "Sword", "brush": "Brush", "arts": "Arts"}
_TIER_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
# A reflex fires when the latest value deviates from its own history mean by >= 1σ in the
# harmful direction. 3σ = CRITICAL, 2σ = HIGH, 1σ = MEDIUM. Needs >= 4 prior points;
# below that we fall back to the fixed warn/fail thresholds (grade).
_MIN_HISTORY = 4
_SIGMA_TIERS = [(3.0, "CRITICAL"), (2.0, "HIGH"), (1.0, "MEDIUM")]


def _sigma_tier(env: dict, rule: dict) -> tuple[str | None, str]:
    """Tier + trigger text from how many σ the latest value sits from the history mean."""
    hist = env.get("history") or []
    if len(hist) < _MIN_HISTORY:
        return None, ""
    base = hist[:-1]
    mean = statistics.fmean(base)
    sd = statistics.stdev(base)  # sample std-dev (÷N-1) — pstdev underestimates by ~22% at N=3
    if sd <= 0:
        return None, ""
    z = (hist[-1] - mean) / sd
    harmful = z if rule["dir"] == "lower" else -z  # "lower is better" → above mean is bad
    for thr, tier in _SIGMA_TIERS:
        if harmful >= thr:
            direction = "above" if rule["dir"] == "lower" else "below"
            return tier, f"{harmful:.1f}σ {direction} the {len(base)}-run mean ({mean:.1f})"
    return None, ""


def _find_env(pillars: dict, pk: str, name: str) -> dict:
    for group in pillars.get(pk, {}).values():
        if name in group:
            return group[name]
    return {}


def _worst_project(by_project: dict, pk: str) -> str | None:
    """Project with the lowest score in this pillar — where running the skill helps most."""
    best, best_score = None, None
    for name, info in (by_project or {}).items():
        if not info.get("has_data"):
            continue
        s = info.get("scores", {}).get(pk)
        if s is None:
            continue
        if best_score is None or s < best_score:
            best, best_score = name, s
    return best

# Per-project-scopable metrics: telemetry-derived per-session/task behaviours where running the
# remediation against the worst-contributing project (rather than the whole governance tree) is
# meaningful. GLOBAL metrics (secrets, skill conflicts, deps, vault, repo-wide hygiene, security
# posture) are deliberately NOT here — their remediation is control-plane-wide, so a per-project
# scope would mislead. Drives the `scope` field threaded into the manual-run exec prompt.
_PROJECT_SCOPABLE = frozenset({
    "Error_Rate", "Latency_P50", "Latency_P95", "Avg_Session_Turns",
    "Chain_Depth_Avg", "Token_Execution_Density", "Slop_Density",
})



def _reflex(pk: str, mk: str, env: dict, tier: str, trigger: str, target: str) -> dict:
    name = mk.replace("_", " ")
    cmd = env.get("mitigation_command")
    grant = resolve_maturity(mk)
    return {
        "id": f"metric:{pk}:{mk}",
        "tier": tier,
        "category": f"{_PILLAR_LABEL.get(pk, pk)} pillar",
        "source": "metric",
        "trigger": trigger,
        "target": target,
        "message": (f"{name} is at {env.get('val')}. Run {cmd} on {target}." if cmd
                    else f"{name} is at {env.get('val')}."),
        "command": cmd,
        "last_fired": None,
        "status": "active",
        "maturity": grant["maturity"],
        "reflex_ready": grant["reflex_ready"],
        "mechanism_status": grant["mechanism_status"],
        # Per-project scope for the MANUAL run: only behavioural metrics with a real worst project.
        # Threaded into the exec prompt so a click targets the noisiest project, not the whole tree
        # (the card message already reads "Run <cmd> on <target>"). Global metrics carry no scope.
        **({"scope": target}
           if mk in _PROJECT_SCOPABLE and target and target != "this repo" else {}),
        **({"mechanism": env["mitigation_mechanism"]} if "mitigation_mechanism" in env else {}),
        # Surface non-auto-remediable metrics so the dashboard can show them as advisory (no
        # run button) instead of offering a mis-routed/circular skill that won't move the metric.
        **({"auto_remediable": False}
           if insights.METRIC_CONFIG.get(mk, {}).get("auto_remediable") is False else {}),
    }


def _metric_reflexes(pillars: dict, category_scores: dict, by_project: dict) -> list[dict]:
    if category_scores.get("window", {}).get("records", 0) == 0:
        return []
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    targets = {pk: (_worst_project(by_project, pk) or "this repo") for pk in pillars}

    # 1) σ-anomaly reflexes — value far from its own history mean (primary threshold)
    for pk, groups in pillars.items():
        for group in groups.values():
            for mk, env in group.items():
                if env.get("is_simulated"):
                    continue
                # Non-auto-remediable metrics never generate metric reflexes (SENSEI-3/4:
                # a CRITICAL card whose only routed action can't move the metric is a
                # misrouted channel). The breach still surfaces via Needs Attention,
                # the pillar rollup, and the flagged metric card — channels without a
                # remediation call-to-action. Config is the source of truth; the
                # engine's non_remediable_metrics.json skip list is regenerated from it.
                if insights.METRIC_CONFIG.get(mk, {}).get("auto_remediable") is False:
                    continue
                rule = insights.METRIC_RULES.get(mk)
                if not rule:
                    continue
                tier, trig = _sigma_tier(env, rule)
                if tier:
                    target = ", ".join(env.get("failure_platforms", [])) or targets[pk]
                    out.append(_reflex(pk, mk, env, tier, trig, target))
                    seen.add((pk, mk))

    # 2) fixed-threshold fallback for flagged metrics that lack enough history for σ
    for pk, sc in category_scores.items():
        for f in sc.get("flags", []):
            if (pk, f["name"]) in seen:
                continue
            if insights.METRIC_CONFIG.get(f["name"], {}).get("auto_remediable") is False:
                continue  # same SENSEI-3/4 filter as the σ path
            env = _find_env(pillars, pk, f["name"])
            rule = insights.METRIC_RULES.get(f["name"], {})
            limit = rule.get("warn", "threshold")
            target = ", ".join(env.get("failure_platforms", [])) or targets[pk]
            out.append(_reflex(pk, f["name"], env, _TIER_BY_GRADE.get(f["grade"], "MEDIUM"),
                               f"past the {limit} limit (needs {_MIN_HISTORY}+ runs for σ)", target))
    return out


def _nudge_command(category: str, message: str) -> str:
    """Every nudge maps to a runnable skill — pick by keyword, never leave it actionless."""
    s = f"{category} {message}".lower()
    # Tier 1 — active threat
    if "secret" in s or ".env" in s or "credential" in s or "leak" in s:
        return "/security-audit"
    # Tier 2 — boundary / access
    if "security" in s or "boundary" in s or "violation" in s:
        return "/security-audit"
    if "commit" in s or "git" in s or "uncommitted" in s or "config drift" in s:
        return "/guard"
    # Tier 3 — diagnostics (new skills)
    if "canary" in s or "gate canary" in s:
        return "/canary-fault-diagnosis"
    if "cost" in s or "spend" in s or "expensive" in s or "token cost" in s:
        return "/cost-breakdown-audit"
    if "subagent" in s or "spawn" in s or "delegation" in s or "agent calls" in s:
        return "/subagent-audit"
    if "context" in s or "compact" in s:
        return "/context-optimization"
    # Tier 4 — repair (new skills)
    if "verifier" in s or "governance pass" in s or "check fail" in s:
        return "/verifier-repair"
    if "tool diversity" in s or "tool overuse" in s or "wrong tool" in s:
        return "/tool-diversity-audit"
    if "scope" in s or "hook" in s or "mechanism" in s or "orphan" in s:
        return "/audit-mechanisms"
    if "mcp" in s or "smoke" in s:
        return "/mcp-setup"
    # Tier 5 — quality
    if "review" in s or "files" in s or "dilut" in s or "simplif" in s:
        return "/simplify"
    return "/status"  # safe default — inspect health before acting


def _nudge_reflexes(nudges_path: Path, state_path: Path) -> list[dict]:
    try:
        catalog = json.loads(nudges_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    last: dict = {}
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if isinstance(state, dict):
            last = state.get("last_fired", {}) or {}
    except (OSError, ValueError):
        pass
    out: list[dict] = []
    for n in catalog.get("nudges", []):
        trig = n.get("trigger", {}) or {}
        lf = last.get(n["id"])
        cat = str(n.get("category", "")).replace("_", " ")
        # Nudge catalog copy is external; strip its chatbot tells (em dashes) for the UI.
        msg = str(n.get("message", "")).replace("nudge: ", "").replace(" — ", ", ").replace("—", ", ").strip()
        out.append({
            "id": f"nudge:{n['id']}",
            "tier": n.get("tier", "MEDIUM"),
            "category": cat,
            "source": "nudge",
            "trigger": trig.get("condition") or trig.get("event", ""),
            "message": msg,
            "command": _nudge_command(cat, msg),
            "last_fired": lf,
            "status": "fired" if lf else "armed",
            # Nudges are catalog-driven (not METRIC_CONFIG-backed) — preserve legacy
            # behavior under REFLEX_REQUIRE_GRANT by emitting an APPLY/ready grant.
            "maturity": "APPLY",
            "reflex_ready": True,
            "mechanism_status": "no_mechanism",
        })
    return out


def _trajectory_reflexes(pillars: dict) -> list[dict]:
    """Early-warning reflexes for metrics on track to breach their fail threshold.

    Reads trajectory_breach_days from each metric env (set by insights.populate_history).
    HIGH = breach in ≤3 days · MEDIUM = breach in ≤7 days.  SIMULATED metrics are skipped.
    Only fires when the metric is NOT already past the fail threshold — these are pre-emptive
    warnings, not redundant copies of an already-active metric reflex.
    """
    out: list[dict] = []
    for pk, groups in pillars.items():
        for _group_name, metrics in groups.items():
            for mk, env in metrics.items():
                days = env.get("trajectory_breach_days")
                if days is None or env.get("is_simulated"):
                    continue
                tier = "HIGH" if days <= 3 else "MEDIUM" if days <= 7 else None
                if tier is None:
                    continue
                cmd = env.get("mitigation_command")
                name = mk.replace("_", " ")
                msg = (
                    f"{name} is on track to breach the fail threshold in ~{days} days. Run {cmd}."
                    if cmd else
                    f"{name} is on track to breach the fail threshold in ~{days} days."
                )
                grant = resolve_maturity(mk)
                out.append({
                    "id": f"trajectory:{pk}:{mk}",
                    "tier": tier,
                    "category": f"Trajectory — {_PILLAR_LABEL.get(pk, pk)}",
                    "source": "trajectory_engine",
                    "trigger": f"Projected breach in {days}d",
                    "target": "all",
                    "message": msg,
                    "command": cmd,
                    "last_fired": None,
                    "status": "active",
                    "maturity": grant["maturity"],
                    "reflex_ready": grant["reflex_ready"],
                    "mechanism_status": grant["mechanism_status"],
                })
    return out


def _load_stuck_reflexes() -> set[str]:
    """Return reflex IDs marked stuck in the reflex engine state file.

    State keys have the form ``metric:pk:mk::command``; the reflex id is the
    prefix before ``::``.  Returns an empty set if the file is missing or unreadable.
    """
    try:
        data = json.loads(_REFLEX_ENGINE_STATE.read_text(encoding="utf-8"))
        ni = data.get("noImprovement", {})
        return {
            key.split("::")[0]
            for key, val in ni.items()
            if isinstance(val, dict) and val.get("stuck")
        }
    except Exception:
        return set()


def build_reflexes(pillars: dict, category_scores: dict, by_project: dict | None = None,
                   nudges_path: Path = _NUDGES_JSON, state_path: Path = _NUDGE_STATE) -> list[dict]:
    """Combine live metric reflexes (danger now), trajectory early-warnings, and Nudge catalog."""
    out = (
        _metric_reflexes(pillars, category_scores, by_project or {})
        + _trajectory_reflexes(pillars)
        + _nudge_reflexes(nudges_path, state_path)
    )
    out.sort(key=lambda r: (_TIER_ORDER.get(r["tier"], 4), r["source"] != "metric"))
    stuck = _load_stuck_reflexes()
    for r in out:
        if r["id"] in stuck:
            r["stuck"] = True
    return out
