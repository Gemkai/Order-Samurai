#!/usr/bin/env python3
"""Deterministic Slop_Density triage mechanism (detect->verify; clone of error_triage.py).

The mechanical core of the /humanizer fallback for the Slop_Density metric, extracted as a
deterministic, read-only, testable mechanism (metrics_path_to_10.md):

  1. DETECT  — re-read the same trailing-window telemetry the dashboard grades and name the
               sessions with the highest slop density (slop markers per 1k output words), so the
               sloppiest output is named with an exemplar for /humanizer to target.
  2. VERIFY  — re-measure Slop_Density (sum(slop_markers)/sum(output_words)*1000, IDENTICAL to
               aggregate.r_slop_density) and set breach_confirmed when the window is calibrated
               (output words present) AND the density is at/above the LIVE FAIL threshold.

Read-only: rewriting slop out of the prose is an LLM judgment task, so /humanizer stays the
fallback on a confirmed breach. The mechanism does the deterministic detect + the honest re-measure.

DESIGN NOTE — the path-to-10 "re-derive markers from text (not the emitter)" utility lever is
moot: the emitter (~/.claude/scripts/agentica_emit.py) ALREADY derives slop_markers from the
assistant text via a deterministic `_SLOP` lexicon (it is not a self-reported/gameable field), so
re-deriving would reproduce the same value. The remaining lever, "strip code fences from the
output_words denominator", would DIVERGE from the kernel reducer (which counts all words) and is a
separate corrected-measure, not a faithful re-measure. This mechanism therefore grades the metric
exactly as the dashboard computes it (the drift guard enforces parity).

Metric served: metric:arts:Slop_Density

Usage:
    python bin/slop_strip.py [--window-days N] [--fail-threshold F] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

# Kept in lockstep with agentica_core.aggregate — test_slop_strip_drift.py asserts parity.
SLOP_MARKERS_FIELD = "slop_markers"
OUTPUT_WORDS_FIELD = "output_words"
# Minimum output words for a record to be RANKED in the detect list — a 1-marker, 8-word reply
# has density 125 but isn't "the sloppiest session". Detect-only; the aggregate verdict counts all.
_DETECT_WORD_FLOOR = 50

_STATE_PATH = Path(__file__).resolve().parents[1] / "state" / "slop_strip.json"


def _int_vals(records: list[dict], field: str) -> list[int]:
    """Integer values of `field` across records (bools/non-numerics dropped). Mirrors
    agentica_core.aggregate._int_vals so the bin sums the same values as the kernel reducer."""
    return [int(r[field]) for r in records
            if isinstance(r.get(field), (int, float)) and not isinstance(r.get(field), bool)]


def slop_density_stats(records: list[dict]) -> tuple[float | None, int, int]:
    """(density | None, slop_markers_sum, output_words_sum). density is slop markers per 1k output
    words, rounded to 2 — IDENTICAL to aggregate.r_slop_density. None (uncalibrated) when there are
    no output words (ow == 0), exactly as the kernel reducer returns None on a zero denominator."""
    sw = sum(_int_vals(records, SLOP_MARKERS_FIELD))
    ow = sum(_int_vals(records, OUTPUT_WORDS_FIELD))
    density = round(sw / ow * 1000, 2) if ow else None
    return density, sw, ow


def _record_density(rec: dict) -> float | None:
    """Per-record slop density (markers per 1k words), or None when the record has no words."""
    sw_list = _int_vals([rec], SLOP_MARKERS_FIELD)
    ow_list = _int_vals([rec], OUTPUT_WORDS_FIELD)
    ow = ow_list[0] if ow_list else 0
    sw = sw_list[0] if sw_list else 0
    return round(sw / ow * 1000, 2) if ow else None


# ---------------------------------------------------------------------------
# Pure core (testable via injected records — no real I/O in tests)
# ---------------------------------------------------------------------------

def triage(
    records: list[dict],
    *,
    fail_threshold: float,
    now_fn: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"),
    window_days: int | None = None,
) -> dict:
    """Build the deterministic Slop_Density triage report from already-loaded records.

    Pure given its inputs. breach_confirmed is the verify gate: True only when the window is
    calibrated (output words present) AND the density is at/above FAIL.
    """
    density, sw, ow = slop_density_stats(records)
    calibrated = density is not None
    breach_confirmed = calibrated and density >= fail_threshold

    # DETECT: the sloppiest sessions, ranked by per-record density desc then session_id for a
    # total, input-order-independent ordering. Records with no output words are not rankable.
    rankable = []
    for r in records:
        ow_list = _int_vals([r], OUTPUT_WORDS_FIELD)
        if not ow_list or ow_list[0] < _DETECT_WORD_FLOOR:
            continue  # too little output to rank meaningfully (still counted in the aggregate above)
        d = _record_density(r)
        if d is not None:
            rankable.append((d, str(r.get("session_id") or ""), r))
    rankable.sort(key=lambda t: (-t[0], t[1]))
    top_sessions = [
        {
            "session_id": str(r.get("session_id") or ""),
            "platform": str(r.get("platform") or "unknown"),
            "slop_density": d,
            "slop_markers": _int_vals([r], SLOP_MARKERS_FIELD)[0] if _int_vals([r], SLOP_MARKERS_FIELD) else 0,
        }
        for d, _sid, r in rankable[:5]
    ]

    if not calibrated:
        verdict = "uncalibrated"
    elif breach_confirmed:
        verdict = "breach_confirmed"
    else:
        verdict = "below_threshold"

    return {
        "generated_at": now_fn(),
        "metric": "metric:arts:Slop_Density",
        "window_days": window_days,
        "slop_markers": sw,
        "output_words": ow,
        "slop_density": density,
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
    governance_root = Path(__file__).resolve().parents[2]
    if str(governance_root) not in sys.path:
        sys.path.insert(0, str(governance_root))
    return governance_root


def _real_records(window_days: int) -> list[dict]:
    """Load the same trailing-window telemetry the dashboard grades. Reuses agentica_core so the
    bin re-measures exactly what aggregate.py computes (no drift)."""
    _governance_root()
    from agentica_core.aggregate import load_records, list_platforms, _within_days  # noqa: E402

    out: list[dict] = []
    for platform in list_platforms():
        for rec in load_records(platform):
            if _within_days(rec.get("timestamp", ""), window_days):
                out.append(dict(rec, platform=rec.get("platform", platform)))
    return out


def _live_fail_threshold() -> float:
    """The effective Slop_Density FAIL the dashboard grades on: METRIC_CONFIG value AFTER
    insights._apply_calibration (clamped so calibration can only tighten). Read live, not baked
    (error_triage.py caveat). The drift guard pins this to the kernel value."""
    _governance_root()
    from agentica_core.insights import METRIC_CONFIG  # noqa: E402
    return float(METRIC_CONFIG["Slop_Density"]["fail"])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _format_report(report: dict) -> str:
    density = report["slop_density"]
    density_s = f"{density}" if density is not None else "uncalibrated"
    lines = [
        f"Slop-Density Triage - {report['generated_at']}  (window {report['window_days']}d)",
        f"Slop density: {density_s} per 1k words  "
        f"({report['slop_markers']} markers / {report['output_words']} words)  "
        f"verdict: {report['verdict'].upper()}  (fail >= {report['fail_threshold']})",
    ]
    for s in report["top_sessions"][:5]:
        lines.append(f"  [{s['slop_density']}] {s['platform']}  session={s['session_id'] or '(none)'}"
                     f"  ({s['slop_markers']} markers)")
    if report["breach_confirmed"]:
        lines.append("(run /humanizer on the sloppiest output above)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    # Windows stdout defaults to cp1252 and can't encode non-ASCII in session ids; the resulting
    # UnicodeEncodeError would exit non-zero and fall through to the slow LLM skill.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(description="Deterministic Slop_Density triage mechanism")
    parser.add_argument("--window-days", type=int, default=30,
                        help="trailing window to grade (default: 30, matches aggregate.py)")
    parser.add_argument("--fail-threshold", type=float, default=None,
                        help="slop density at/above which a breach is confirmed "
                             "(default: the live calibrated METRIC_CONFIG value)")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    fail_threshold = args.fail_threshold if args.fail_threshold is not None else _live_fail_threshold()
    report = triage(
        _real_records(args.window_days),
        fail_threshold=fail_threshold,
        window_days=args.window_days,
    )
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    except OSError:
        pass

    print(json.dumps(report, indent=2) if args.json else _format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
