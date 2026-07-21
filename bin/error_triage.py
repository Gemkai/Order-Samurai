#!/usr/bin/env python3
"""Deterministic Error_Rate triage mechanism (reference 10/10 remediation template).

The mechanical core of the /investigate fallback for the Error_Rate metric, extracted
as a deterministic, read-only, testable mechanism (metrics_path_to_10.md). It does the
three things an LLM /investigate loop cannot do reliably:

  1. DETECT  — re-read the same trailing-window telemetry the dashboard grades and group
               the error records by signature (platform, error message, exit code) so the
               top failing pattern is named, with an exemplar session.
  2. VERIFY  — re-measure Error_Rate with the min-sample guard and only set
               breach_confirmed when the window is calibrated AND the rate is at/above the
               FAIL threshold. This is the "prove the breach is real, not a denominator
               artifact" step that makes the remediation safe to fire on.
  3. (no mutation) — triage is read-only and idempotent; it writes a state report only.
               The LLM /investigate skill stays the judgment fallback on a confirmed breach.

This is the archetype every other detect->verify bin follows: pure core (triage / inject
records in tests) + thin real-I/O (reuses agentica_core.load_records so it grades exactly
what the dashboard does) + main() that writes state/error_triage.json.

Metric served: metric:bow:Error_Rate

Usage:
    python bin/error_triage.py [--window-days N] [--fail-threshold F] [--json]
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
# Canonical classification (kept in lockstep with agentica_core.aggregate —
# tests/test_error_triage.py::test_error_rate_classification_no_drift asserts equality)
# ---------------------------------------------------------------------------

ERROR_STATUSES = frozenset({"error"})
MIN_ERROR_SAMPLE = 10
# Mirrors METRIC_CONFIG["Error_Rate"]["fail"] in agentica_core/insights.py. Error_Rate is not
# calibration-overlaid today so this static default is correct. A clone for a calibration-eligible
# metric must pass the live threshold via the mechanism's `args` (e.g. --fail-threshold) rather than
# rely on this default, or it will grade against a stale value once thresholds.json overlays it.
DEFAULT_FAIL_THRESHOLD = 5.0

_STATE_PATH = Path(__file__).resolve().parents[1] / "state" / "error_triage.json"


def error_rate_stats(records: list[dict]) -> tuple[float | None, int, int]:
    """(rate_pct | None, error_count, total). rate is None (uncalibrated) when
    total < MIN_ERROR_SAMPLE — the min-sample guard that stops a false FAIL on noise.

    The rate is rounded to 1 decimal to MATCH the kernel reducer (aggregate.r_error_rate),
    so the bin's breach_confirmed gate and the dashboard grade on the identical value. A clone
    for a metric whose kernel reducer does NOT round must compare on the raw value instead."""
    total = len(records)
    errors = sum(1 for r in records if str(r.get("status", "")).lower() in ERROR_STATUSES)
    if total < MIN_ERROR_SAMPLE:
        return None, errors, total
    return round(100 * errors / total, 1), errors, total


def _signature(rec: dict) -> tuple[str, str, str]:
    """Group key for an error record: (platform, error-message head, exit code).
    The message is truncated to 80 chars — deliberately lossy: a clone whose discriminating
    detail lives past char 80 (or in a field other than `error`) must widen this key."""
    platform = str(rec.get("platform") or "unknown")
    msg = str(rec.get("error") or "").strip().splitlines()[0][:80] if rec.get("error") else "(no message)"
    exit_code = str(rec.get("exit_code")) if rec.get("exit_code") is not None else "?"
    return (platform, msg, exit_code)


# ---------------------------------------------------------------------------
# Pure core (testable via injected records — no real I/O in tests)
# ---------------------------------------------------------------------------

def triage(
    records: list[dict],
    *,
    fail_threshold: float = DEFAULT_FAIL_THRESHOLD,
    now_fn: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"),
    window_days: int | None = None,
) -> dict:
    """Build the deterministic Error_Rate triage report from already-loaded records.

    Pure given its inputs. breach_confirmed is the re-measure/verify gate: True only when
    the window is calibrated (>= MIN_ERROR_SAMPLE records) AND the rate is at/above FAIL.
    """
    rate, errors, total = error_rate_stats(records)
    calibrated = rate is not None
    breach_confirmed = calibrated and rate >= fail_threshold

    error_recs = [r for r in records if str(r.get("status", "")).lower() in ERROR_STATUSES]
    sig_counts = Counter(_signature(r) for r in error_recs)
    signatures = []
    # Total deterministic order: count desc, then the signature tuple. Using sorted() (not
    # Counter.most_common, which tie-breaks by insertion order) means a reordered input — or an
    # unordered source in a clone — can never flip top_signature on tied counts.
    for (platform, msg, exit_code), count in sorted(sig_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        exemplar = next(
            (r for r in error_recs if _signature(r) == (platform, msg, exit_code)), {}
        )
        signatures.append({
            "platform": platform,
            "message": msg,
            "exit_code": exit_code,
            "count": count,
            "exemplar_session": str(exemplar.get("session_id") or ""),
        })

    if not calibrated:
        verdict = "uncalibrated"
    elif breach_confirmed:
        verdict = "breach_confirmed"
    else:
        verdict = "below_threshold"

    return {
        "generated_at": now_fn(),
        "metric": "metric:bow:Error_Rate",
        "window_days": window_days,
        "total": total,
        "error_count": errors,
        "error_rate": rate,
        "calibrated": calibrated,
        "fail_threshold": fail_threshold,
        "breach_confirmed": breach_confirmed,
        "verdict": verdict,
        "top_signature": signatures[0] if signatures else None,
        "signatures": signatures,
    }


# ---------------------------------------------------------------------------
# Real I/O (called only by main() — never in tests)
# ---------------------------------------------------------------------------

def _real_records(window_days: int) -> list[dict]:
    """Load the same trailing-window telemetry the dashboard grades, across all platforms.

    Reuses agentica_core so the bin re-measures exactly what aggregate.py computes (no drift).

    IMPORTANT (template caveat): this `parents[2]` import seam assumes the mechanism is
    read_only:true, so the reflex engine runs it from the REAL tree (parents[2] == Governance,
    which holds agentica_core). A MUTATING clone (read_only:false) is run from a git worktree at
    .tmp/worktrees/<...>/bin/, where parents[2] has no agentica_core — such a clone must resolve
    the Governance root differently (e.g. an absolute ORDER_SAMURAI_ROOT-relative import)."""
    governance_root = Path(__file__).resolve().parents[2]
    if str(governance_root) not in sys.path:
        sys.path.insert(0, str(governance_root))
    from agentica_core.aggregate import load_records, list_platforms, _within_days  # noqa: E402

    out: list[dict] = []
    for platform in list_platforms():
        for rec in load_records(platform):
            if _within_days(rec.get("timestamp", ""), window_days):
                out.append(dict(rec, platform=rec.get("platform", platform)))
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _format_report(report: dict) -> str:
    rate = report["error_rate"]
    rate_s = f"{rate}%" if rate is not None else "uncalibrated"
    lines = [
        f"Error Triage — {report['generated_at']}  (window {report['window_days']}d)",
        f"Error rate: {rate_s}  ({report['error_count']}/{report['total']} records)  "
        f"verdict: {report['verdict'].upper()}",
    ]
    if report["top_signature"]:
        for s in report["signatures"][:5]:
            lines.append(
                f"  [{s['count']}x] {s['platform']} exit={s['exit_code']}  {s['message']!r}"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    # Windows stdout defaults to cp1252 and can't encode non-ASCII chars in error messages;
    # the resulting UnicodeEncodeError would exit non-zero and make the engine fall through
    # to the slow LLM skill. Force UTF-8 so the deterministic mechanism exits 0. (anti-pattern #13)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(description="Deterministic Error_Rate triage mechanism")
    parser.add_argument("--window-days", type=int, default=30,
                        help="trailing window to grade (default: 30, matches aggregate.py)")
    parser.add_argument("--fail-threshold", type=float, default=DEFAULT_FAIL_THRESHOLD,
                        help=f"error-rate %% at/above which a breach is confirmed (default: {DEFAULT_FAIL_THRESHOLD})")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    report = triage(
        _real_records(args.window_days),
        fail_threshold=args.fail_threshold,
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
