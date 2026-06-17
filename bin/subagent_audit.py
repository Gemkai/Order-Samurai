#!/usr/bin/env python3
"""Deterministic subagent-audit mechanism.

The mechanical core of the /subagent-audit skill, extracted as a deterministic,
testable mechanism (RONIN-MECHANISM-ROUTE-DRAFT.md). The skill's judgement-free
core — parse session/transcript data, classify each Agent/Task spawn as justified
or wasteful by rule, compute token-waste estimates — is pure rule logic, so it
runs faster and ships with a real eval (tests/test_subagent_audit.py) instead of
a 0%-success LLM remediation.

What stays LLM: genuinely ambiguous spawns where no rule fires (verdict "unknown").
Those surface in the report for a human or the /subagent-audit skill to judge.

Metric served: metric:brush:Subagent_Efficiency_Index, metric:brush:Chain_Depth_Avg

Usage:
    python bin/subagent_audit.py [--sessions N] [--json]

Default is read-only: scans session fingerprints + transcripts, classifies spawns,
prints the report.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


# ---------------------------------------------------------------------------
# Classification rules (pure)
# ---------------------------------------------------------------------------

ISOLATION_KEYWORDS = frozenset({
    "review", "security", "adversarial", "gate", "rival",
    "independent", "verify", "check security", "code review",
    "security audit", "adversarial audit",
})

FANOUT_KEYWORDS = frozenset({
    "all files", "bulk", "sweep", "migration", "parallel", "batch",
    "across", "every", "full", "comprehensive",
})

TRIVIAL_KEYWORDS = frozenset({
    "read", "check if", "find", "look at", "summarize", "what is",
    "list", "show me", "explain", "describe",
})

# Recoverable tokens per wasteful spawn: ~15k subagent overhead - ~2k inline = 13k.
TOKEN_PREMIUM_K = 13


def classify_spawn(description: str, prompt: str, turn_spawn_count: int) -> tuple[str, str]:
    """Classify one Agent/Task spawn. Returns (verdict, reason).

    verdict ∈ {justified_parallel, justified_isolation, justified_fanout,
               wasteful_trivial, wasteful_serial, unknown}

    Rules are evaluated in priority order; the first match wins.
    """
    desc_low = description.lower()
    prompt_low = prompt.lower()
    combined = desc_low + " " + prompt_low[:500]

    # justified_parallel requires non-trivial descriptions — three trivial lookups
    # delegated in one turn are still wasteful (false-negative prevention: SA-01).
    if turn_spawn_count >= 3 and not any(kw in desc_low for kw in TRIVIAL_KEYWORDS):
        return ("justified_parallel", f"{turn_spawn_count} concurrent spawns in one turn")

    if any(kw in combined for kw in ISOLATION_KEYWORDS):
        matched = next(kw for kw in ISOLATION_KEYWORDS if kw in combined)
        return ("justified_isolation", f"isolation keyword: '{matched}'")

    if len(prompt) > 800 or any(kw in combined for kw in FANOUT_KEYWORDS):
        reason = (
            "large prompt (fan-out scope)"
            if len(prompt) > 800
            else "fan-out keyword in description"
        )
        return ("justified_fanout", reason)

    if any(kw in desc_low for kw in TRIVIAL_KEYWORDS):
        matched = next(kw for kw in TRIVIAL_KEYWORDS if kw in desc_low)
        return ("wasteful_trivial", f"trivial keyword in description: '{matched}'")

    if turn_spawn_count <= 2:
        return ("wasteful_serial", "single/paired spawn with no justified pattern")

    return ("unknown", "no rule matched")


# ---------------------------------------------------------------------------
# Real I/O (called only by main() — never in tests)
# ---------------------------------------------------------------------------

def _real_session_files() -> list[dict]:
    """Return the most-recent session fingerprint dicts from ~/.claude/.tmp/sessions/."""
    import glob
    import os

    pattern = os.path.expanduser("~/.claude/.tmp/sessions/*.json")
    paths = sorted(glob.glob(pattern), key=os.path.getmtime)
    result = []
    for p in paths:
        try:
            result.append(json.loads(Path(p).read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return result


def _real_transcript_spawns() -> list[dict]:
    """Scan project JSONL transcripts for Agent/Task tool_use blocks."""
    import glob

    pattern = Path.home() / ".claude" / "projects" / "**" / "*.jsonl"
    spawns: list[dict] = []
    for p in glob.glob(str(pattern), recursive=True):
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    rec = json.loads(line)
                    if rec.get("type") != "assistant":
                        continue
                    session_id = rec.get("sessionId", "")
                    ts = rec.get("timestamp", "")
                    content = rec.get("message", {}).get("content", [])
                    if not isinstance(content, list):
                        continue
                    tool_calls = [
                        b for b in content
                        if isinstance(b, dict)
                        and b.get("type") == "tool_use"
                        and b.get("name") in ("Agent", "Task")
                    ]
                    if not tool_calls:
                        continue
                    for block in tool_calls:
                        inp = block.get("input", {})
                        spawns.append({
                            "session_id": session_id,
                            "timestamp": ts,
                            "tool_name": block.get("name", "Agent"),
                            "description": inp.get("description", ""),
                            "prompt": inp.get("prompt", ""),
                            "turn_spawn_count": len(tool_calls),
                        })
        except (OSError, ValueError):
            pass
    return spawns


# ---------------------------------------------------------------------------
# Orchestration (testable via injected fns — no real I/O in tests)
# ---------------------------------------------------------------------------

def run_audit(
    *,
    n_sessions: int = 30,
    session_files_fn: Callable[[], list[dict]] = _real_session_files,
    transcript_spawns_fn: Callable[[], list[dict]] = _real_transcript_spawns,
    now_fn: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"),
) -> dict:
    """Build the deterministic spawn-classification report.

    Pure given its injected fns — unit tests pass fixtures so no real files are read.
    `n_sessions` caps how many session fingerprints are included in the session count
    (most-recent N); spawn classification always covers all transcript data provided
    by transcript_spawns_fn.

    Returns a structured, JSON-serialisable report.
    """
    sessions = session_files_fn()[-n_sessions:] if n_sessions > 0 else session_files_fn()
    spawns = transcript_spawns_fn()

    justified: list[dict] = []
    wasteful: list[dict] = []
    unknown: list[dict] = []

    for spawn in spawns:
        verdict, reason = classify_spawn(
            description=spawn.get("description", ""),
            prompt=spawn.get("prompt", ""),
            turn_spawn_count=spawn.get("turn_spawn_count", 1),
        )
        row: dict = {
            "session_id": spawn.get("session_id", ""),
            "tool_name": spawn.get("tool_name", "Agent"),
            "description": spawn.get("description", ""),
            "verdict": verdict,
            "reason": reason,
        }
        if verdict.startswith("justified_"):
            justified.append(row)
        elif verdict.startswith("wasteful_"):
            row["token_premium_k"] = TOKEN_PREMIUM_K
            wasteful.append(row)
        else:
            unknown.append(row)

    total_recoverable_k = sum(w["token_premium_k"] for w in wasteful)

    # Most common wasteful verdict, or "none"
    wasteful_verdicts = [w["verdict"] for w in wasteful]
    top_wasteful_pattern = (
        Counter(wasteful_verdicts).most_common(1)[0][0]
        if wasteful_verdicts
        else "none"
    )

    return {
        "generated_at": now_fn(),
        "sessions_analyzed": len(sessions),
        "spawns_analyzed": len(spawns),
        "justified": justified,
        "wasteful": wasteful,
        "unknown": unknown,
        "counts": {
            "sessions": len(sessions),
            "spawns": len(spawns),
            "justified": len(justified),
            "wasteful": len(wasteful),
            "unknown": len(unknown),
        },
        "total_recoverable_k": total_recoverable_k,
        "top_wasteful_pattern": top_wasteful_pattern,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _format_report(report: dict) -> str:
    lines = [
        f"Subagent Audit — {report['generated_at']}",
        f"Sessions: {report['sessions_analyzed']}  Spawns: {report['spawns_analyzed']}",
    ]

    if report["wasteful"]:
        lines.append(f"\nWASTEFUL ({len(report['wasteful'])}) — "
                     f"~{report['total_recoverable_k']}k recoverable tokens:")
        for w in report["wasteful"]:
            desc = w["description"][:60] or "(no description)"
            lines.append(f"  [{w['verdict']}] {desc!r}  — {w['reason']}")

    if report["justified"]:
        lines.append(f"\nJUSTIFIED ({len(report['justified'])}):")
        for j in report["justified"]:
            desc = j["description"][:60] or "(no description)"
            lines.append(f"  [{j['verdict']}] {desc!r}  — {j['reason']}")

    if report["unknown"]:
        lines.append(f"\nUNKNOWN ({len(report['unknown'])}) — needs human review:")
        for u in report["unknown"]:
            desc = u["description"][:60] or "(no description)"
            lines.append(f"  {desc!r}  — {u['reason']}")

    c = report["counts"]
    lines.append(
        f"\nTotal: {c['spawns']} spawns · {c['justified']} justified · "
        f"{c['wasteful']} wasteful · {c['unknown']} unknown  "
        f"| top wasteful pattern: {report['top_wasteful_pattern']}"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic subagent-audit mechanism")
    parser.add_argument(
        "--sessions", type=int, default=30,
        help="number of most-recent session fingerprints to include in the count (default: 30)",
    )
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    report = run_audit(n_sessions=args.sessions)
    print(json.dumps(report, indent=2) if args.json else _format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
