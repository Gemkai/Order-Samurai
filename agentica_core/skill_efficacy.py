"""Compute per-skill efficacy from exec_log.jsonl and write skill_efficacy.json.

Called non-fatally by refresh_dashboard.py on every refresh. Feeds the dynamic
cooldown multiplier in ReflexEngine: skills that consistently fail get longer
cooldowns, reducing runaway retry noise and surfacing systemic skill issues.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

_WINDOW = 20            # consider only the last N runs per skill
_WARMUP_RUNS = 3        # fewer than this → not enough data, retry aggressively
_WARMUP_MULTIPLIER = 0.25   # 0.25× COOLDOWN_MS = 7.5 min (optimistic until proven)
_LOW_THRESHOLD = 0.30   # below 30% success rate → apply penalty multiplier
_MULTIPLIER = 3         # 3× normal COOLDOWN_MS (30 min → 90 min)


def compute(log_path: Path, out_path: Path) -> dict:
    """Parse exec_log.jsonl and write skill_efficacy.json.

    Returns the efficacy dict (skill → {total_runs, success_count, success_rate,
    cooldown_multiplier}).  Reads up to _WINDOW runs per skill from the tail of
    the log (newest-first traversal) so recent failures weigh more than old ones.

    Success is determined by (in priority order):
      1. The explicit ``improved`` boolean field (written by ReflexEngine after
         comparing pre/post metric state) — reflects real metric improvement.
      2. Fallback: ``status == "done"`` for legacy entries that pre-date the
         improved field (exit-code proxy only; less accurate).
    """
    runs: dict[str, list[bool]] = defaultdict(list)  # skill → list of booleans (True=improved)
    if log_path.exists():
        try:
            lines = log_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for ln in reversed(lines):  # newest first
            try:
                r = json.loads(ln)
                # Use "skill" field if present; fall back to the bare skill name from
                # the command string — same normalization as reflex_eureka, so both
                # modules (and ReflexEngine's efficacy lookup) bucket runs identically.
                skill = r.get("skill")
                if not skill:
                    parts = (r.get("command") or "unknown").strip().lstrip("/").split()
                    skill = parts[0] if parts else "unknown"
                # Read-only mechanisms (detect scripts) never move their own metric, so
                # 'improved' is always false. Grade them by a clean run (exit-0) under a
                # separate "<skill>::mechanism" key, so the mechanism's honest record isn't
                # blended with the retired LLM skill's improved-based failures (the maturity
                # ladder reads this file; blending demotes a working mechanism to OBSERVE).
                is_ro_mech = r.get("kind") == "mechanism" and r.get("read_only") is True
                key = f"{skill}::mechanism" if is_ro_mech else skill
                if len(runs[key]) < _WINDOW:
                    if is_ro_mech:
                        runs[key].append(r.get("status") == "done")
                    else:
                        # Prefer explicit improved field; fall back to exit-code proxy
                        improved = r.get("improved")
                        if improved is not None:
                            runs[key].append(bool(improved))
                        else:
                            runs[key].append(r.get("status") == "done")
            except (json.JSONDecodeError, TypeError):
                pass

    efficacy: dict[str, dict] = {}
    for skill, successes_list in runs.items():
        total = len(successes_list)
        successes = sum(1 for x in successes_list if x)
        rate = round(successes / total, 3) if total else None
        if total < _WARMUP_RUNS:
            # Insufficient history — retry aggressively until we have signal
            multiplier = _WARMUP_MULTIPLIER
        elif rate is not None and rate < _LOW_THRESHOLD:
            # Proven consistently failing — back off hard
            multiplier = _MULTIPLIER
        else:
            multiplier = 1

        efficacy[skill] = {
            "total_runs": total,
            "success_count": successes,
            "success_rate": rate,
            "cooldown_multiplier": multiplier,
        }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(efficacy, indent=2), encoding="utf-8")
    return efficacy
