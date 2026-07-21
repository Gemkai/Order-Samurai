"""Data-derived metric thresholds. Instead of hand-set warn/fail, compute them from the
observed weekly distribution (metrics_history.jsonl) so grading reflects this system's real
behaviour. Writes agentica_core/thresholds.json, which insights.py overlays onto METRIC_RULES.

Only calibrates plain telemetry metrics with enough history (>= MIN_POINTS) that are NOT
per-session-rated and NOT point-in-time security/governance signals (those have no weekly
distribution to learn from and keep their reviewed manual thresholds).

  python -m agentica_core.calibrate
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path

from . import insights

_THIS = Path(__file__).resolve()
THRESHOLDS_PATH = _THIS.parent / "thresholds.json"
MIN_POINTS = 8  # p95 of fewer than 8 weeks equals max(vals) — threshold becomes unreachable


def _pctile(vals: list[float], p: float) -> float:
    s = sorted(vals)
    k = (len(s) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return round(s[f] + (s[c] - s[f]) * (k - f), 2)


def calibrate(store: Path | None = None) -> dict:
    store = store or insights.default_history_path()
    series: dict[str, list[float]] = defaultdict(list)
    if store.exists():
        for ln in store.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                row = json.loads(ln)
            except ValueError:
                continue
            for key, v in (row.get("values") or {}).items():
                if isinstance(v, (int, float)):
                    series[key.split("/")[-1]].append(float(v))  # key = pillar/group/metric

    out: dict[str, dict] = {}
    for metric, rule in insights.METRIC_RULES.items():
        if rule.get("per"):          # per-session metrics: history is cumulative, wrong units
            continue
        # Config opt-out (2026-07-19): metrics whose history is bimodal-by-workload
        # (e.g. Avg_Session_Turns mixes autonomous and interactive sessions) get
        # percentile thresholds that punish normal use — warn=p75 of a bad sample set
        # warn=1.5 turns. calibrate:False keeps the reviewed manual thresholds.
        if insights.METRIC_CONFIG.get(metric, {}).get("calibrate") is False:
            continue
        vals = series.get(metric, [])
        if len(vals) < MIN_POINTS or (max(vals) - min(vals)) == 0:
            continue                 # not enough signal / no spread -> keep manual default
        if rule["dir"] == "lower":   # bigger = worse
            warn, fail = _pctile(vals, 75), _pctile(vals, 95)
        else:                        # bigger = better
            warn, fail = _pctile(vals, 40), _pctile(vals, 20)
        if warn == fail:
            continue                 # degenerate band -> skip
        out[metric] = {"warn": warn, "fail": fail, "n": len(vals)}

    THRESHOLDS_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def main() -> int:
    out = calibrate()
    print(f"calibrate: {len(out)} metrics calibrated -> {THRESHOLDS_PATH}")
    for m, t in out.items():
        print(f"  {m}: warn={t['warn']} fail={t['fail']} (n={t['n']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
