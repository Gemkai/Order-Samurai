#!/usr/bin/env python3
"""skill_routing_adherence.py — Order Samurai metric reducer (sword pillar).

Skill_Routing_Adherence = of the critical-work prompts where a skill SHOULD have
been used, the fraction where it actually was. This is the "honor-system, ignored"
signal made measurable: the exact governance failure of hand-rolling work a skill
packages (no rubric, no adversarial-verify, no telemetry) becomes a graded number.

  numerator   = detections whose routed skill was invoked in the same session
  denominator = all detections (router-hook firings)
  value       = 100 * numerator / denominator   (higher = better adherence)

Sources (written by the two hooks):
  ~/.claude/data/skill_routing.jsonl      — intent detections
  ~/.claude/data/skill_invocations.jsonl  — Skill-tool invocations

Emits the metric envelope shape Order Samurai's aggregate expects. Run standalone
to print the value, or import compute_adherence() from the pillar reducer.
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

CLAUDE_HOME = Path(os.environ.get("CLAUDE_HOME") or Path.home() / ".claude")
DETECT = CLAUDE_HOME / "data" / "skill_routing.jsonl"
INVOKE = CLAUDE_HOME / "data" / "skill_invocations.jsonl"


def _load(path: Path) -> list[dict]:
    out = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    except FileNotFoundError:
        pass
    return out


def compute_adherence() -> dict:
    detections = _load(DETECT)
    invocations = _load(INVOKE)
    # skills invoked per session (leading slug, no leading slash)
    invoked_by_session: dict[str, set] = defaultdict(set)
    for r in invocations:
        slug = str(r.get("skill", "")).lstrip("/").split()[0]
        if slug:
            invoked_by_session[str(r.get("session_id", ""))].add(slug)

    total = 0
    routed = 0
    unrouted_by_cat: dict[str, int] = defaultdict(int)
    for d in detections:
        sid = str(d.get("session_id", ""))
        cats = d.get("categories") or []
        skills = d.get("skills") or []
        for cat, skill in zip(cats, skills):
            slug = str(skill).lstrip("/").split()[0]
            if not slug:
                continue
            total += 1
            if slug in invoked_by_session.get(sid, set()):
                routed += 1
            else:
                unrouted_by_cat[cat] += 1

    value = round(100.0 * routed / total, 1) if total else None
    worst = sorted(unrouted_by_cat.items(), key=lambda kv: -kv[1])[:3]
    return {
        "metric": "Skill_Routing_Adherence",
        "pillar": "sword",
        "val": value,                       # % (None until any detection exists)
        "is_percent": True,
        "is_simulated": total == 0,         # no data yet -> SIMULATED, not a real 0
        "sample_size": total,
        "routed": routed,
        "detail": (
            f"{routed}/{total} critical-work prompts routed through their skill"
            + (f"; top unrouted: {', '.join(f'{c} ({n})' for c, n in worst)}" if worst else "")
            if total else "no critical-work prompts detected yet"
        ),
    }


def compute_work_volume(window_days: int = 30) -> dict:
    """Governance_Work_Volume — how much critical work was DETECTED, routed or not.

    The paired VOLUME signal for Skill_Routing_Adherence (backlog P1, 2026-07-19):
    adherence is a ratio, so a busy hand-rolled session reads ~0 — indistinguishable
    from a dead session. This counts every (category, skill) detection the router
    hook logged, regardless of whether the skill fired, so the pair reads as
    "high volume, low adherence" instead of vanishing. Same per-pair counting unit
    as adherence's denominator so the two numbers are directly comparable.

    window_days matches the canonical 30d payload window (reducers don't receive
    the window; adherence has the same limitation — both documented).
    Returns val=None -> SIMULATED until the router log has any in-window record;
    never a fabricated 0.
    """
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    total = 0
    by_cat: dict[str, int] = defaultdict(int)
    for d in _load(DETECT):
        try:
            ts = datetime.fromisoformat(str(d.get("ts", "")).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if ts < cutoff:
            continue
        cats = d.get("categories") or []
        skills = d.get("skills") or []
        for cat, skill in zip(cats, skills):
            if str(skill).lstrip("/").split()[0:1]:
                total += 1
                by_cat[cat] += 1
    top = sorted(by_cat.items(), key=lambda kv: -kv[1])[:3]
    return {
        "metric": "Governance_Work_Volume",
        "pillar": "sword",
        "val": total if total else None,    # count (None until any detection exists)
        "is_percent": False,
        "is_simulated": total == 0,         # no data yet -> SIMULATED, not a real 0
        "window_days": window_days,
        "detail": (
            f"{total} critical-work detections in {window_days}d"
            + (f"; top: {', '.join(f'{c} ({n})' for c, n in top)}" if top else "")
            if total else "no critical-work prompts detected yet"
        ),
    }


if __name__ == "__main__":
    result = compute_adherence()
    print(json.dumps(result, indent=2))
    print(json.dumps(compute_work_volume(), indent=2))
    # exit 0 always; this is a measurement, not a gate
    sys.exit(0)
