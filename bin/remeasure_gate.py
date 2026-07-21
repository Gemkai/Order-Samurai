#!/usr/bin/env python3
"""Deterministic real-time verify-gate for the reflex fire path.

Purpose (fail-open gap closure). The always-on reflex loop fires a code-modifying
`claude --dangerously-skip-permissions` skill the moment a pillar metric breaches its
threshold in wid_payload.json. For the metrics that carry NEITHER a deterministic
remediation mechanism NOR a fresh `rival` verdict, that snapshot is the only evidence —
a stale/phantom breach spends a full skill spawn on a metric that is actually fine. This
gate re-measures the metric LIVE (the same computation refresh_dashboard.py runs) right
before the spawn and tells the engine whether the breach is still real.

DRY: it does NOT reimplement any reducer or threshold. It calls agentica_core.aggregate()
in-memory (write_history=False → no wid_payload write, no history mutation) and asks the
SAME reflex-builder the dashboard uses whether the metric STILL yields an active
CRITICAL/HIGH `metric:<pillar>:<Name>` reflex. If it does not, the snapshot was stale and
the skill is suppressed; if it does, the breach is real and the engine proceeds.

Exit-code contract (consumed by reflex-engine.ts as a pre-skill gate):
    0  within threshold  → PHANTOM/recovered → SUPPRESS the skill spawn.
    1  still breaching    → REAL              → PROCEED to the skill.
    2  could-not-measure  → ERROR             → PROCEED (fail-open: a broken gate must
                                                never silence autonomy — behaves exactly
                                                as today, where no gate existed).

Note the asymmetry that makes this safe: the gate can only SUPPRESS on a positive,
deterministic "the metric is within threshold right now" signal (exit 0). Every other
outcome — real breach, aggregate error, unknown metric — proceeds to the skill.

Usage:
    python bin/remeasure_gate.py --metric Doc_Parity_Issues [--window-days 30] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Exit codes — see module docstring.
EXIT_SUPPRESS = 0   # phantom / recovered
EXIT_PROCEED = 1    # still breaching (real)
EXIT_ERROR = 2      # could not measure → fail-open (proceed)


# ---------------------------------------------------------------------------
# Pure core (testable via injected reflexes — no aggregate() call in tests)
# ---------------------------------------------------------------------------

def metric_value(payload: dict, metric: str) -> float | None:
    """Live numeric value of a graded metric from a freshly-aggregated payload
    (pillars.<pillar>.<group>.<Metric>.val). None when absent or non-numeric.

    Consumed by reflex-engine.ts, which records this value as metric_before /
    metric_after on exec_log rows so remediation efficacy is computed from real
    per-run readings instead of sparse metrics_history snapshots (2026-07-19
    metric surface review §A1)."""
    pillars = payload.get("pillars")
    if not isinstance(pillars, dict):
        return None
    for pobj in pillars.values():
        if not isinstance(pobj, dict):
            continue
        for group in pobj.values():
            if not isinstance(group, dict) or metric not in group:
                continue
            leaf = group[metric]
            val = leaf.get("val") if isinstance(leaf, dict) else None
            if isinstance(val, bool):
                return None
            if isinstance(val, (int, float)):
                return float(val)
            try:
                return float(str(val).replace(",", ""))
            except (TypeError, ValueError):
                return None
    return None


def still_breaching(reflexes: list[dict], metric: str) -> bool:
    """True when a freshly-built reflex list STILL carries an active CRITICAL/HIGH
    `metric:<pillar>:<metric>` reflex — i.e. the engine would fire it again right now.

    Tier is restricted to CRITICAL/HIGH to mirror ReflexEngine._isEligible's own tier
    gate: a metric that recovered to MEDIUM would not fire either, so it is a phantom
    from the fire path's perspective and must be suppressed. Absence of the reflex
    (metric within threshold) is the phantom/recovered case.
    """
    for r in reflexes:
        rid = r.get("id", "")
        if (
            rid.startswith("metric:")
            and rid.split(":")[-1] == metric
            and r.get("status") == "active"
            and r.get("tier") in ("CRITICAL", "HIGH")
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# Real I/O (called only by main() — never in tests)
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    """Resolve the repo root (holds agentica_core) and put it on sys.path.

    parents[1] == the pack root from bin/. The gate is read-only (it never writes), so it
    is safe to run either from the live ORDER_SAMURAI_ROOT (the engine runs it there,
    before any staging worktree exists) or from a worktree — both resolve the same
    agentica_core and read seconds-fresh telemetry."""
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    return repo_root


def _live_payload(window_days: int) -> dict:
    """Recompute the whole payload live. This is the exact computation
    refresh_dashboard.py performs (aggregate re-runs verifiers, secret scan,
    architecture score, knowledge signals over near-live telemetry); write_history=False
    keeps it side-effect-free (no wid_payload write, no history append)."""
    _repo_root()
    from agentica_core.aggregate import aggregate  # noqa: E402

    payload = aggregate(window_days=window_days, write_history=False)
    return payload if isinstance(payload, dict) else {}


def _known_metric(metric: str) -> bool:
    _repo_root()
    from agentica_core.insights import METRIC_CONFIG  # noqa: E402

    return metric in METRIC_CONFIG


def main(argv: list[str] | None = None) -> int:
    # Windows stdout defaults to cp1252 and can't encode non-ASCII in session ids/messages;
    # the resulting UnicodeEncodeError would exit non-zero and be read as PROCEED. That is
    # still fail-open (safe), but reconfiguring keeps the log clean.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description="Deterministic reflex fire-path verify-gate")
    parser.add_argument("--metric", required=True,
                        help="METRIC_CONFIG key to re-measure (e.g. Doc_Parity_Issues)")
    parser.add_argument("--window-days", type=int, default=30,
                        help="trailing window to grade (default: 30, matches aggregate.py)")
    parser.add_argument("--json", action="store_true", help="emit the verdict as JSON")
    args = parser.parse_args(argv)

    metric = args.metric

    try:
        # A metric name arriving from the reflex id must be a real METRIC_CONFIG key; an
        # unknown one is a wiring bug, not a phantom — fail-open (proceed) rather than
        # silently suppress a spawn we cannot vouch for.
        if not _known_metric(metric):
            _emit(args, metric, verdict="unknown_metric", exit_code=EXIT_ERROR,
                  detail=f"{metric!r} is not a METRIC_CONFIG key")
            return EXIT_ERROR

        payload = _live_payload(args.window_days)
        reflexes = payload.get("reflexes", [])
        reflexes = reflexes if isinstance(reflexes, list) else []
        value = metric_value(payload, metric)
        breaching = still_breaching(reflexes, metric)
        if breaching:
            _emit(args, metric, verdict="still_breaching", exit_code=EXIT_PROCEED,
                  detail="live re-measure still yields an active CRITICAL/HIGH reflex",
                  value=value)
            return EXIT_PROCEED
        _emit(args, metric, verdict="phantom", exit_code=EXIT_SUPPRESS,
              detail="live re-measure is within threshold — snapshot was stale",
              value=value)
        return EXIT_SUPPRESS
    except Exception as exc:  # noqa: BLE001 — any failure fails OPEN (proceed to skill)
        _emit(args, metric, verdict="error", exit_code=EXIT_ERROR,
              detail=f"{type(exc).__name__}: {exc}")
        return EXIT_ERROR


def _emit(args, metric: str, *, verdict: str, exit_code: int, detail: str,
          value: float | None = None) -> None:
    report = {
        "metric": metric,
        "verdict": verdict,
        "exit_code": exit_code,
        "action": "suppress" if exit_code == EXIT_SUPPRESS else "proceed",
        "detail": detail,
        # Live value of the metric at measurement time (None when not measurable).
        # reflex-engine.ts parses this for exec_log metric_before / metric_after.
        "value": value,
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"[verify-gate] {metric}: {verdict.upper()} → {report['action']} — {detail}")


if __name__ == "__main__":
    raise SystemExit(main())
