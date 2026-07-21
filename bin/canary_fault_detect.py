#!/usr/bin/env python3
"""Deterministic security-gate canary fault-detection mechanism.

The deterministic *detect* half of the /canary-fault-diagnosis skill (0% success
as an LLM remediation, state/skill_efficacy.json), extracted as a testable
mechanism (RONIN-DETERMINIZATION-PLAN.md, candidate #8 — "split deterministic
detect + LLM fix"). Reading the canary state and classifying *why* it faulted is
pure rule logic that mirrors aggregate.py:_canary_health; only the repair tail
(deciding whether the gate itself is broken) needs judgement and stays LLM.

The classification matches the skill's fault table exactly:
  - missing          : file absent — the gate has never run here
  - gate-not-working : gate_working is false — the gate failed its own self-test
  - corrupt          : no last_run timestamp (partial/corrupt write)
  - stale            : last_run older than max_age_days
  - healthy          : none of the above

Safety: gate-not-working is the one class that must NOT be auto-regenerated —
re-running /security-gate would overwrite the evidence that the gate is broken.
The report flags this via `safe_to_regenerate: false`.

Usage:
    python bin/canary_fault_detect.py [--canary PATH] [--json]

Read-only: it never writes the canary or runs the gate.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Default canary location (written by /security-gate).
DEFAULT_CANARY_PATH = Path.home() / ".claude" / "data" / "security_gate_canary.json"

# Fallback staleness window when the canary omits max_age_days (matches the skill snippet).
DEFAULT_MAX_AGE_DAYS = 7

# Classes that are safe to auto-regenerate via /security-gate. gate-not-working is
# deliberately excluded — it means the gate self-test failed and must be investigated.
_SAFE_TO_REGENERATE = frozenset({"missing", "corrupt", "stale"})

# Human-facing next action per fault class (mirrors the skill's response table).
_ACTIONS = {
    "missing": "Run /security-gate to create the first canary",
    "gate-not-working": "Do NOT regenerate — investigate why the gate self-test failed",
    "corrupt": "Safe to regenerate via /security-gate",
    "stale": "Run /security-gate; if staleness recurs, check the scheduled cadence",
    "healthy": "No action — canary is healthy",
}


def _parse_timestamp(raw: str) -> datetime | None:
    """Parse an ISO-8601 timestamp, treating a naive value as UTC. None if unparseable."""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def classify(state: dict | None, now: datetime, *,
             default_max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> dict:
    """Classify the canary fault from its state and the current time.

    `state` is the parsed canary JSON, or None when the file is missing. `now`
    is injected (timezone-aware) so the eval is deterministic without a clock.

    Returns a JSON-serialisable verdict: fault_class, fault (bool), fault_value
    (1.0/0.0 to match the Gate_Canary_Fault reducer), age_days (or None),
    safe_to_regenerate, and the recommended action.
    """
    fault_class = _classify_class(state, now, default_max_age_days)
    age_days = _age_days(state, now)
    return {
        "fault_class": fault_class,
        "fault": fault_class != "healthy",
        "fault_value": 0.0 if fault_class == "healthy" else 1.0,
        "age_days": age_days,
        "safe_to_regenerate": fault_class in _SAFE_TO_REGENERATE,
        "action": _ACTIONS[fault_class],
    }


def _classify_class(state: dict | None, now: datetime, default_max_age_days: int) -> str:
    """Resolve the fault class. Order matters: missing < gate-broken < corrupt < stale.

    The precedence mirrors the reducer/skill: a broken gate is reported even if the
    timestamp is also stale, because gate-not-working forbids auto-regeneration.
    """
    if state is None:
        return "missing"
    if not state.get("gate_working", False):
        return "gate-not-working"
    last_run = state.get("last_run")
    if not last_run:
        return "corrupt"
    parsed = _parse_timestamp(last_run)
    if parsed is None:
        return "corrupt"
    age = (now - parsed).days
    max_age = state.get("max_age_days", default_max_age_days)
    return "stale" if age > max_age else "healthy"


def _age_days(state: dict | None, now: datetime) -> int | None:
    """Age of the canary in whole days, or None when there is no usable timestamp."""
    if not state:
        return None
    parsed = _parse_timestamp(state.get("last_run", ""))
    return (now - parsed).days if parsed is not None else None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_state(path: Path) -> dict | None:
    """Read the canary file; None if it is missing (the 'missing' fault class)."""
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8", errors="ignore"))


def _format_report(report: dict) -> str:
    age = report["age_days"]
    age_str = f"{age}d" if age is not None else "n/a"
    return (
        f"Fault class: {report['fault_class']}\n"
        f"Faulting: {'yes' if report['fault'] else 'no'} (metric {report['fault_value']})\n"
        f"Age: {age_str}\n"
        f"Safe to auto-regenerate: {'yes' if report['safe_to_regenerate'] else 'no'}\n"
        f"Action: {report['action']}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic canary fault-detection mechanism")
    parser.add_argument("--canary", type=Path, default=DEFAULT_CANARY_PATH,
                        help="path to security_gate_canary.json")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    try:
        state = _load_state(args.canary)
    except ValueError:
        # File exists but is not valid JSON — a corrupt write, not a missing canary.
        state = {"gate_working": True, "last_run": None}

    report = classify(state, datetime.now(timezone.utc))
    print(json.dumps(report, indent=2) if args.json else _format_report(report))
    # Exit non-zero on a fault so the reflex runner can fall back to the LLM skill
    # for the gate-not-working tail.
    return 1 if report["fault"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
