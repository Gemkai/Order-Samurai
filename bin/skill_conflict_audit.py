#!/usr/bin/env python3
"""Deterministic Skill_Conflicts triage mechanism (detect->verify; clone of error_triage.py).

The mechanical core of the /skill-consolidator fallback for the Skill_Conflicts metric, extracted
as a deterministic, read-only, testable mechanism (metrics_path_to_10.md):

  1. DETECT  — re-read the same skill_conflicts.json the dashboard grades and name each conflict
               group (clone family) with its member skills, ranked by size, so the merge target
               is concrete.
  2. VERIFY  — re-measure Skill_Conflicts (the count of conflict groups, IDENTICAL to the
               dashboard's len(skill_conflicts.json["groups"]) in scouts.security_signals) and set
               breach_confirmed when the source is present AND the count is at/above the LIVE FAIL.

Read-only: deciding which near-duplicate skills are TRULY redundant and merging them is the
judgment tail (the skill-consolidator "when in doubt, retain" rule), so /skill-consolidator stays
the fallback on a confirmed breach. The mechanism does the deterministic detect + the honest
re-measure of exactly what the dashboard grades.

DESIGN NOTE — why this re-reads the artifact rather than re-running skill_consolidator.py: the
dashboard value is len(groups) in ~/.claude/data/skill_conflicts.json (produced upstream by
refresh_skill_conflicts.py via Ollama embeddings). skill_consolidator.py re-derives its OWN
clone-family groups from a --vectors embeddings export (env-dependent, and a different count), so
wiring it would NOT grade what the dashboard grades. Re-reading the artifact the scout reads is
the faithful re-measure (the drift guard pins it to scouts.security_signals' formula).

Metric served: metric:arts:Skill_Conflicts

Usage:
    python bin/skill_conflict_audit.py [--fail-threshold N] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

CONFLICTS_FILENAME = "skill_conflicts.json"

_STATE_PATH = Path(__file__).resolve().parents[1] / "state" / "skill_conflict_audit.json"


# ---------------------------------------------------------------------------
# Pure core (testable via injected groups — no real I/O in tests)
# ---------------------------------------------------------------------------

def conflict_count(groups) -> int:
    """Number of conflict groups — IDENTICAL to the dashboard's len(conf.get("groups") or [])
    in scouts.security_signals. `groups` is the dict {group_name: [skill, ...]} (or None/empty)."""
    return len(groups) if groups else 0


def audit(
    groups,
    *,
    fail_threshold: float,
    calibrated: bool = True,
    now_fn: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"),
) -> dict:
    """Build the deterministic Skill_Conflicts triage report from the already-read groups mapping.

    Pure given its inputs. `calibrated` is False when skill_conflicts.json is absent/unreadable
    (the metric is then SIMULATED, never a false "0 conflicts"). breach_confirmed is the verify
    gate: True only when calibrated AND the group count is at/above FAIL.
    """
    count = conflict_count(groups)
    breach_confirmed = calibrated and count >= fail_threshold

    detail = []
    for name, members in (groups or {}).items():
        skills = sorted(str(m) for m in members) if isinstance(members, (list, tuple, set)) else []
        detail.append({"group": str(name), "skills": skills, "skill_count": len(skills)})
    # Total deterministic order: most members first, then group name.
    detail.sort(key=lambda g: (-g["skill_count"], g["group"]))

    if not calibrated:
        verdict = "uncalibrated"
    elif breach_confirmed:
        verdict = "breach_confirmed"
    else:
        verdict = "below_threshold"

    return {
        "generated_at": now_fn(),
        "metric": "metric:arts:Skill_Conflicts",
        "conflict_groups": count,
        "calibrated": calibrated,
        "fail_threshold": fail_threshold,
        "breach_confirmed": breach_confirmed,
        "verdict": verdict,
        "top_group": detail[0] if detail else None,
        "groups": detail,
    }


# ---------------------------------------------------------------------------
# Real I/O (called only by main() — never in tests)
# ---------------------------------------------------------------------------

def _governance_root() -> Path:
    governance_root = Path(__file__).resolve().parents[2]
    if str(governance_root) not in sys.path:
        sys.path.insert(0, str(governance_root))
    return governance_root


def _conflicts_path() -> Path | None:
    """Resolve <claude runtime_root>/data/skill_conflicts.json — the SAME path
    scouts.security_signals reads (data = runtime_root / "data"). Resolved via the adapter (no
    hardcoded absolute path). Returns None if the platform can't be resolved."""
    _governance_root()
    try:
        from agentica_core.adapter import resolve_platform  # noqa: E402
        return resolve_platform("claude").runtime_root / "data" / CONFLICTS_FILENAME
    except Exception:
        return None


def _real_groups() -> tuple[object, bool]:
    """Read skill_conflicts.json's `groups` mapping, returning (groups, calibrated). calibrated is
    False when the file is missing/unreadable/not-a-dict — mirroring the scout simply omitting the
    metric (-> SIMULATED), never a false 0."""
    path = _conflicts_path()
    if path is None or not path.exists():
        return None, False
    try:
        conf = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, False
    if not isinstance(conf, dict):
        return None, False
    return conf.get("groups") or {}, True


def _live_fail_threshold() -> float:
    """The effective Skill_Conflicts FAIL the dashboard grades on: METRIC_CONFIG value AFTER
    insights._apply_calibration (clamped so calibration can only tighten). Read live, not baked
    (error_triage.py caveat). The drift guard pins this to the kernel value."""
    _governance_root()
    from agentica_core.insights import METRIC_CONFIG  # noqa: E402
    return float(METRIC_CONFIG["Skill_Conflicts"]["fail"])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _format_report(report: dict) -> str:
    count = report["conflict_groups"]
    count_s = str(count) if report["calibrated"] else "uncalibrated (no skill_conflicts.json)"
    lines = [
        f"Skill-Conflicts Triage - {report['generated_at']}",
        f"Conflict groups: {count_s}  verdict: {report['verdict'].upper()}  "
        f"(fail >= {report['fail_threshold']})",
    ]
    for g in report["groups"][:10]:
        lines.append(f"  [{g['skill_count']}] {g['group']}: {', '.join(g['skills'][:8])}")
    if report["breach_confirmed"]:
        lines.append("(run /skill-consolidator to merge the clone families above)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    # Windows stdout defaults to cp1252 and can't encode non-ASCII in skill names; the resulting
    # UnicodeEncodeError would exit non-zero and fall through to the slow LLM skill.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(description="Deterministic Skill_Conflicts triage mechanism")
    parser.add_argument("--fail-threshold", type=float, default=None,
                        help="conflict-group count at/above which a breach is confirmed "
                             "(default: the live calibrated METRIC_CONFIG value)")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    fail_threshold = args.fail_threshold if args.fail_threshold is not None else _live_fail_threshold()
    groups, calibrated = _real_groups()
    report = audit(groups, fail_threshold=fail_threshold, calibrated=calibrated)

    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    except OSError:
        pass

    print(json.dumps(report, indent=2) if args.json else _format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
