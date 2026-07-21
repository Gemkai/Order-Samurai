#!/usr/bin/env python3
"""Deterministic model-selector mechanism.

The mechanical core of the /model-selector skill (43% success as an LLM
remediation, state/skill_efficacy.json), extracted as a deterministic, testable
mechanism (RONIN-DETERMINIZATION-PLAN.md, candidate #7 — "rule-based routing
table"). Picking haiku/sonnet/opus from session signals is pure arithmetic on a
fixed scoring table — there is no judgement, so it runs faster and ships with a
real eval (tests/test_model_selector.py) instead of a coin-flip LLM call.

The scoring weights mirror the existing ~/.claude/scripts/adaptive_model_selector.py
(turn count 0-40, error rate 0-30, tool diversity 0-30) and the documented bands
(0-30 haiku, 30-70 sonnet, 70-100 opus) so the determinized mechanism is faithful
to the rule it replaces, not a new heuristic.

Usage:
    python bin/model_selector.py [--session PATH] [--json]

Reads a session snapshot (turn_count, stop_reasons, tools_used) and prints the
recommended model with its complexity score and reason. No side effects.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Default session snapshot location (written by the session tracker).
DEFAULT_SESSION_PATH = Path.home() / ".claude" / ".tmp" / "current_session.json"

# Band boundaries on the 0-100 complexity score (inclusive lower, exclusive upper):
#   score <  HAIKU_CEIL          -> haiku
#   HAIKU_CEIL <= score < OPUS_FLOOR -> sonnet
#   score >= OPUS_FLOOR          -> opus
HAIKU_CEIL = 30
OPUS_FLOOR = 70

# Static model profiles keyed by model id (speed/cost are descriptive, not scored).
MODEL_PROFILES = {
    "haiku": {"reason": "Simple task detected. Haiku is fast and cost-effective.",
              "speed": "Very fast", "cost": "Lowest"},
    "sonnet": {"reason": "Medium complexity. Sonnet offers best balance.",
               "speed": "Fast", "cost": "Moderate"},
    "opus": {"reason": "Complex task detected. Opus provides deep reasoning.",
             "speed": "Slower", "cost": "Higher"},
}


# ---------------------------------------------------------------------------
# Scoring (pure)
# ---------------------------------------------------------------------------

def _turn_points(turns: int) -> int:
    """Turn-count contribution (0-40): more turns -> more complex."""
    if turns < 5:
        return 5
    if turns < 15:
        return 20
    if turns < 30:
        return 35
    return 40


def _error_points(errors: int, turns: int) -> int:
    """Error-rate contribution (0-30): a higher error rate needs a stronger model."""
    error_rate = errors / max(turns, 1)
    if error_rate < 0.1:
        return 0
    if error_rate < 0.3:
        return 10
    if error_rate < 0.5:
        return 20
    return 30


def _tool_points(tools: int) -> int:
    """Tool-diversity contribution (0-30): more distinct tools -> more complex."""
    if tools <= 2:
        return 0
    if tools <= 4:
        return 10
    if tools <= 6:
        return 20
    return 30


def score_complexity(turns: int, errors: int, tools: int) -> int:
    """Total task-complexity score in 0-100 from the three signal contributions.

    Pure and total: any non-negative integer inputs map to a clamped 0-100 score,
    so the band lookup downstream is always defined.
    """
    raw = _turn_points(turns) + _error_points(errors, turns) + _tool_points(tools)
    return min(100, raw)


def recommend_model(score: int) -> str:
    """Map a complexity score to a model id using the fixed bands.

    The boundaries are deliberate: exactly 30 is the first sonnet score, exactly
    70 is the first opus score (closed-below band edges).
    """
    if score < HAIKU_CEIL:
        return "haiku"
    if score < OPUS_FLOOR:
        return "sonnet"
    return "opus"


# ---------------------------------------------------------------------------
# Session extraction (pure)
# ---------------------------------------------------------------------------

def extract_signals(session: dict) -> tuple[int, int, int]:
    """Pull (turns, errors, tools) from a session snapshot, defaulting to 0.

    Mirrors adaptive_model_selector.analyze_task_complexity's reads so a real
    session snapshot scores identically through either path.
    """
    turns = int(session.get("turn_count", 0) or 0)
    errors = sum(
        1 for r in (session.get("stop_reasons") or {}).values() if "error" in str(r).lower()
    )
    tools = len(session.get("tools_used", {}) or {})
    return turns, errors, tools


def select(session: dict) -> dict:
    """Full deterministic selection for a session snapshot.

    Returns a JSON-serialisable report: the score, the recommended model, the
    signal breakdown, and the static profile for the chosen model.
    """
    turns, errors, tools = extract_signals(session)
    score = score_complexity(turns, errors, tools)
    model = recommend_model(score)
    profile = MODEL_PROFILES[model]
    return {
        "complexity": score,
        "model": model,
        "reason": profile["reason"],
        "speed": profile["speed"],
        "cost": profile["cost"],
        "signals": {"turns": turns, "errors": errors, "tools": tools},
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _format_report(report: dict) -> str:
    s = report["signals"]
    return (
        f"Task Complexity: {report['complexity']}/100\n"
        f"Recommended: {report['model'].upper()}\n"
        f"Reason: {report['reason']}\n"
        f"Speed: {report['speed']}\n"
        f"Cost: {report['cost']}\n"
        f"Signals: {s['turns']} turns · {s['errors']} errors · {s['tools']} tools"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic model-selector mechanism")
    parser.add_argument("--session", type=Path, default=DEFAULT_SESSION_PATH,
                        help="path to the session snapshot JSON")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    try:
        session = json.loads(args.session.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"model-selector: cannot read session {args.session}: {exc}", file=sys.stderr)
        return 1

    report = select(session)
    print(json.dumps(report, indent=2) if args.json else _format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
