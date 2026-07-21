#!/usr/bin/env python3
"""Deterministic Raw_Pending triage mechanism (detect->verify; clone of error_triage.py).

The mechanical core of the /wiki fallback for the Raw_Pending metric, extracted as a
deterministic, read-only, testable mechanism (metrics_path_to_10.md). It does the two things an
LLM loop cannot do reliably, then hands the judgment step back:

  1. DETECT  — re-run the same vault check the dashboard grades (vault_health.check_raw_pending)
               and name the raw notes still awaiting compilation, with an exemplar.
  2. VERIFY  — re-measure Raw_Pending (the count of uncompiled raw notes, IDENTICAL to the
               dashboard's len(check_raw_pending())) and set breach_confirmed when the vault is
               reachable AND the count is at/above the LIVE FAIL threshold.

This mechanism is read-only: the actual compile step (raw note -> wiki article) is irreducibly an
LLM judgment task (Knowledge/vault/CLAUDE.md "compile step"), so it stays the /wiki skill fallback
on a confirmed breach. The mechanism's job is the deterministic detect + the honest re-measure.

Faithful clone of bin/error_triage.py: pure core (count / classify — inject pending in tests) +
thin real-I/O (reuses agentica_core's resolved vault_health path so it grades exactly what
aggregate.py computes) + main() that writes state/wiki_compile.json.

Metric served: metric:arts:Raw_Pending

Usage:
    python bin/wiki_compile.py [--fail-threshold N] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

_STATE_PATH = Path(__file__).resolve().parents[1] / "state" / "wiki_compile.json"


# ---------------------------------------------------------------------------
# Pure core (testable via injected pending list — no real I/O in tests)
# ---------------------------------------------------------------------------

def audit(
    pending: list[str],
    *,
    fail_threshold: float,
    calibrated: bool = True,
    now_fn: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"),
) -> dict:
    """Build the deterministic Raw_Pending triage report from an already-scanned pending list.

    Pure given its inputs. `calibrated` is False when the vault/health script is unreachable (the
    kernel emits SIMULATED in that case), so an absent vault never reads as a false "0 pending,
    breach cleared". breach_confirmed is the verify gate: True only when calibrated AND the count
    is at/above FAIL.
    """
    count = len(pending)
    breach_confirmed = calibrated and count >= fail_threshold
    # Total deterministic order for the detect list (already sorted by check_raw_pending, but a
    # clone over an unordered source must not let input order flip the exemplar).
    items = sorted(str(p) for p in pending)

    if not calibrated:
        verdict = "uncalibrated"
    elif breach_confirmed:
        verdict = "breach_confirmed"
    else:
        verdict = "below_threshold"

    return {
        "generated_at": now_fn(),
        "metric": "metric:arts:Raw_Pending",
        "raw_pending_count": count,
        "calibrated": calibrated,
        "fail_threshold": fail_threshold,
        "breach_confirmed": breach_confirmed,
        "verdict": verdict,
        "top_pending": items[0] if items else None,
        "pending": items,
    }


# ---------------------------------------------------------------------------
# Real I/O (called only by main() — never in tests)
# ---------------------------------------------------------------------------

def _governance_root() -> Path:
    governance_root = Path(__file__).resolve().parents[2]
    if str(governance_root) not in sys.path:
        sys.path.insert(0, str(governance_root))
    return governance_root


def _real_pending() -> tuple[list[str], bool]:
    """Run vault_health.check_raw_pending() at the SAME path the kernel resolves, returning
    (note-names, calibrated). calibrated is False when the vault-health script is missing or
    errors — mirroring aggregate._vault_health_metrics returning None (-> SIMULATED). Reuses the
    kernel's _VAULT_HEALTH_SCRIPT so the path can't drift from what the dashboard grades."""
    _governance_root()
    import importlib.util
    from agentica_core.aggregate import _VAULT_HEALTH_SCRIPT  # noqa: E402

    if not _VAULT_HEALTH_SCRIPT.exists():
        return [], False
    try:
        spec = importlib.util.spec_from_file_location("agentica_vault_health_wc", _VAULT_HEALTH_SCRIPT)
        if spec is None or spec.loader is None:
            return [], False
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        pending = mod.check_raw_pending()
    except Exception:
        return [], False
    return [Path(p).name for p in pending], True


def _live_fail_threshold() -> float:
    """The effective Raw_Pending FAIL the dashboard grades on: METRIC_CONFIG value AFTER
    insights._apply_calibration (clamped so calibration can only tighten). Read live, not baked
    (error_triage.py caveat). The drift guard pins this to the kernel value."""
    _governance_root()
    from agentica_core.insights import METRIC_CONFIG  # noqa: E402
    return float(METRIC_CONFIG["Raw_Pending"]["fail"])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _format_report(report: dict) -> str:
    count = report["raw_pending_count"]
    count_s = str(count) if report["calibrated"] else "uncalibrated (vault unreachable)"
    lines = [
        f"Raw-Pending Triage - {report['generated_at']}",
        f"Pending raw notes: {count_s}  verdict: {report['verdict'].upper()}  "
        f"(fail >= {report['fail_threshold']})",
    ]
    for name in report["pending"][:10]:
        lines.append(f"  - {name}")
    if report["breach_confirmed"]:
        lines.append("(run /wiki to compile the pending notes into the wiki)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    # Windows stdout defaults to cp1252 and can't encode non-ASCII in note filenames; the
    # resulting UnicodeEncodeError would exit non-zero and fall through to the slow LLM skill.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(description="Deterministic Raw_Pending triage mechanism")
    parser.add_argument("--fail-threshold", type=float, default=None,
                        help="pending-note count at/above which a breach is confirmed "
                             "(default: the live calibrated METRIC_CONFIG value)")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    fail_threshold = args.fail_threshold if args.fail_threshold is not None else _live_fail_threshold()
    pending, calibrated = _real_pending()
    report = audit(pending, fail_threshold=fail_threshold, calibrated=calibrated)

    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    except OSError:
        pass

    print(json.dumps(report, indent=2) if args.json else _format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
