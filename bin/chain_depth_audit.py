#!/usr/bin/env python3
"""Deterministic Chain_Depth_Avg triage mechanism (detect->verify; clone of error_triage.py).

The mechanical core of the /subagent-audit fallback for the Chain_Depth_Avg metric, extracted
as a deterministic, read-only, testable mechanism (metrics_path_to_10.md). It does the three
things an LLM loop cannot do reliably:

  1. DETECT  — re-read the same trailing-window telemetry the dashboard grades and name the
               top over-orchestrating sessions (the records with the highest chain_depth) with
               an exemplar session, so the breach has a concrete owner.
  2. VERIFY  — re-measure Chain_Depth_Avg (median of the per-session chain_depth field, IDENTICAL
               to aggregate.r_chain_depth_avg) and only set breach_confirmed when the window is
               calibrated AND the median is at/above the LIVE FAIL threshold. This is the
               "prove the breach is real, not a denominator artifact" step.
  3. (no mutation) — triage is read-only and idempotent; it writes a state report only. The LLM
               /subagent-audit skill stays the judgment fallback on a confirmed breach.

IMPORTANT — what chain_depth actually measures. The kernel's `chain_depth` field is orchestration
FAN-OUT: the count of Agent + Task tool-use calls per session (emitted by
~/.claude/scripts/agentica_emit.py). It is NOT parent->child nesting depth. True nesting depth is
structurally unobservable from Claude Code transcripts (verified 2026-06-28 over 1,143 transcripts:
isSidechain is true on zero records, each subagent collapses to a single toolUseResult on the main
thread, and toolStats carries no agent/task counter, so a sub-subagent spawn is invisible). The
telemetry.py / METRICS.md docs that call this "Master->Orchestrator->Child depth" are stale; this
mechanism grades the metric as the kernel reducer actually computes it (median fan-out).

This is a faithful clone of bin/error_triage.py: pure core (median / classify — inject records in
tests) + thin real-I/O (reuses agentica_core.load_records so it grades exactly what the dashboard
does) + main() that writes state/chain_depth_audit.json.

Metric served: metric:brush:Chain_Depth_Avg

Usage:
    python bin/chain_depth_audit.py [--window-days N] [--fail-threshold F] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

# ---------------------------------------------------------------------------
# Canonical computation (kept in lockstep with agentica_core.aggregate —
# tests/test_chain_depth_audit_drift.py::test_chain_depth_matches_kernel_reducer asserts equality)
# ---------------------------------------------------------------------------

CHAIN_DEPTH_FIELD = "chain_depth"

_STATE_PATH = Path(__file__).resolve().parents[1] / "state" / "chain_depth_audit.json"


def _int_vals(records: list[dict], field: str) -> list[int]:
    """Integer values of `field` across records (bools/non-numerics dropped).

    Mirrors agentica_core.aggregate._int_vals exactly so the bin and the kernel reducer
    select the same records; the drift guard asserts the resulting median matches."""
    return [int(r[field]) for r in records
            if isinstance(r.get(field), (int, float)) and not isinstance(r.get(field), bool)]


def _pctile(vals: list[int], p: float) -> float | None:
    """Linear-interpolated percentile rounded to 1 decimal. Mirrors aggregate._pctile so the
    bin's median equals the kernel reducer's value on the identical input (drift guard)."""
    if not vals:
        return None
    s = sorted(vals)
    k = (len(s) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return round(s[f] + (s[c] - s[f]) * (k - f), 1)


def chain_depth_stats(records: list[dict]) -> tuple[float | None, int, int]:
    """(median | None, n_with_field, total). median is None (uncalibrated) when no record carries
    chain_depth — IDENTICAL to aggregate.r_chain_depth_avg, which is the median (_pctile(...,50)) of
    the per-session chain_depth field. No min-sample floor: the kernel reducer has none, and the
    bin must grade exactly what the dashboard grades (the drift guard enforces this)."""
    vals = _int_vals(records, CHAIN_DEPTH_FIELD)
    median = _pctile(vals, 50) if vals else None
    return median, len(vals), len(records)


def chain_depth_median(records: list[dict]) -> float | None:
    """Median of the per-session chain_depth field, or None when uncalibrated. The exact value
    aggregate.r_chain_depth_avg returns (asserted by the kernel<->bin drift guard)."""
    return chain_depth_stats(records)[0]


# ---------------------------------------------------------------------------
# Pure core (testable via injected records — no real I/O in tests)
# ---------------------------------------------------------------------------

def audit(
    records: list[dict],
    *,
    fail_threshold: float,
    now_fn: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"),
    window_days: int | None = None,
) -> dict:
    """Build the deterministic Chain_Depth_Avg triage report from already-loaded records.

    Pure given its inputs. breach_confirmed is the re-measure/verify gate: True only when the
    window is calibrated (>= 1 record carries chain_depth) AND the median is at/above FAIL.
    `fail_threshold` is injected (main() resolves the LIVE calibrated value) so the gate matches
    the dashboard grade rather than a stale static default.
    """
    median, n_with_field, total = chain_depth_stats(records)
    calibrated = median is not None
    breach_confirmed = calibrated and median >= fail_threshold

    # DETECT: the heaviest-fan-out sessions, ordered count desc then session_id for a total,
    # input-order-independent ranking (no insertion-order tie-break leak on equal counts).
    with_depth = [
        r for r in records
        if isinstance(r.get(CHAIN_DEPTH_FIELD), (int, float))
        and not isinstance(r.get(CHAIN_DEPTH_FIELD), bool)
    ]
    ranked = sorted(
        with_depth,
        key=lambda r: (-int(r[CHAIN_DEPTH_FIELD]), str(r.get("session_id") or "")),
    )
    top_sessions = [
        {
            "session_id": str(r.get("session_id") or ""),
            "platform": str(r.get("platform") or "unknown"),
            "chain_depth": int(r[CHAIN_DEPTH_FIELD]),
        }
        for r in ranked[:5]
    ]

    if not calibrated:
        verdict = "uncalibrated"
    elif breach_confirmed:
        verdict = "breach_confirmed"
    else:
        verdict = "below_threshold"

    return {
        "generated_at": now_fn(),
        "metric": "metric:brush:Chain_Depth_Avg",
        "measures": "fan-out (Agent+Task calls per session), not parent->child nesting depth",
        "window_days": window_days,
        "total": total,
        "sessions_with_depth": n_with_field,
        "chain_depth_median": median,
        "calibrated": calibrated,
        "fail_threshold": fail_threshold,
        "breach_confirmed": breach_confirmed,
        "verdict": verdict,
        "top_session": top_sessions[0] if top_sessions else None,
        "top_sessions": top_sessions,
    }


# ---------------------------------------------------------------------------
# Real I/O (called only by main() — never in tests)
# ---------------------------------------------------------------------------

def _governance_root() -> Path:
    """Resolve the Governance root (holds agentica_core) and put it on sys.path.

    Template caveat (same as error_triage._real_records): this parents[2] seam assumes the
    mechanism is read_only:true so the reflex engine runs it from the REAL tree
    (parents[2] == Governance). A MUTATING clone run from a git worktree must resolve the root
    differently."""
    governance_root = Path(__file__).resolve().parents[2]
    if str(governance_root) not in sys.path:
        sys.path.insert(0, str(governance_root))
    return governance_root


def _real_records(window_days: int) -> list[dict]:
    """Load the same trailing-window telemetry the dashboard grades, across all platforms.
    Reuses agentica_core so the bin re-measures exactly what aggregate.py computes (no drift)."""
    _governance_root()
    from agentica_core.aggregate import load_records, list_platforms, _within_days  # noqa: E402

    out: list[dict] = []
    for platform in list_platforms():
        for rec in load_records(platform):
            if _within_days(rec.get("timestamp", ""), window_days):
                out.append(dict(rec, platform=rec.get("platform", platform)))
    return out


def _live_fail_threshold() -> float:
    """The effective Chain_Depth_Avg FAIL the dashboard grades on: METRIC_CONFIG['Chain_Depth_Avg']
    ['fail'] AFTER insights._apply_calibration(), which CLAMPS calibration so it can only TIGHTEN
    the manual policy value, never loosen it. Chain_Depth_Avg is calibration-eligible, so we read
    the live value at runtime rather than bake a static default (error_triage.py:44-48 caveat).
    The drift guard pins this to the kernel's METRIC_CONFIG value."""
    _governance_root()
    from agentica_core.insights import METRIC_CONFIG  # noqa: E402
    return float(METRIC_CONFIG["Chain_Depth_Avg"]["fail"])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _format_report(report: dict) -> str:
    median = report["chain_depth_median"]
    median_s = f"{median}" if median is not None else "uncalibrated"
    lines = [
        f"Chain-Depth Triage — {report['generated_at']}  (window {report['window_days']}d)",
        f"Median chain depth (fan-out): {median_s}  "
        f"({report['sessions_with_depth']}/{report['total']} records carry it)  "
        f"verdict: {report['verdict'].upper()}  (fail >= {report['fail_threshold']})",
    ]
    if report["top_sessions"]:
        lines.append("Top over-orchestrating sessions:")
        for s in report["top_sessions"]:
            lines.append(
                f"  [{s['chain_depth']}x] {s['platform']}  session={s['session_id'] or '(none)'}"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    # Windows stdout defaults to cp1252 and can't encode non-ASCII chars in session ids/messages;
    # the resulting UnicodeEncodeError would exit non-zero and make the engine fall through to the
    # slow LLM skill. Force UTF-8 so the deterministic mechanism exits 0. (anti-pattern #13)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(description="Deterministic Chain_Depth_Avg triage mechanism")
    parser.add_argument("--window-days", type=int, default=30,
                        help="trailing window to grade (default: 30, matches aggregate.py)")
    parser.add_argument("--fail-threshold", type=float, default=None,
                        help="median chain depth at/above which a breach is confirmed "
                             "(default: the live calibrated METRIC_CONFIG value)")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    fail_threshold = args.fail_threshold if args.fail_threshold is not None else _live_fail_threshold()
    report = audit(
        _real_records(args.window_days),
        fail_threshold=fail_threshold,
        window_days=args.window_days,
    )
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    except OSError:
        pass  # report still printed; state write is best-effort

    print(json.dumps(report, indent=2) if args.json else _format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
