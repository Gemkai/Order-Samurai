"""Remediation_Delta — HOW MUCH a remediation attempt moved its target metric, not
just whether a judged event called it "improved".

remediation.efficacy() answers yes/no per judged before/after event, and its honest
headline (improvement_rate) still collapses every attempt into a single win/loss bit.
This module scores magnitude: for each exec_log attempt tied to a specific metric
(reflex_id "metric:pillar:MetricName" or "trajectory:pillar:MetricName"), take the
metric's real historical values from Data/telemetry/metrics_history.jsonl and compute

    delta = median(3 post-firing values) - median(3 pre-firing values)

sign-normalized by the metric's `dir` (insights.METRIC_RULES) so a positive delta
always means improvement, whichever direction the raw metric moves. Attempts with
fewer than 3 real history values on either side are `pending` — not enough evidence
yet, never silently scored as 0 or dropped from the count.

Observational only (2026-08-01 metric-gap remediation, phase B1): no reflex-engine
consumer reads this, and no grading threshold exists yet — thresholds are set from
the first week of real data (calibration gate), not guessed.
"""
from __future__ import annotations

import statistics
from pathlib import Path

from . import insights
from .remediation import _load_exec_rows, _load_history
from .telemetry import parse_ts


def _pre_post_medians(fired_at, timeline: list[tuple]) -> tuple[float | None, float | None]:
    """Up to 3 real history values strictly before/after `fired_at`. None on either
    side when fewer than 3 values exist there — not enough evidence to score."""
    pre = [v for dt, v in timeline if dt <= fired_at]
    post = [v for dt, v in timeline if dt > fired_at]
    if len(pre) < 3 or len(post) < 3:
        return None, None
    return statistics.median(pre[-3:]), statistics.median(post[:3])


def compute(history_path: Path | None = None, exec_log_path: Path | None = None) -> dict:
    """Per-skill + overall Remediation_Delta over this window's exec_log attempts.

    Unlike remediation.efficacy(), this needs no `records` (telemetry) parameter —
    the delta computation is exec_log + metrics_history only (see module docstring)."""
    history_path = history_path or insights.default_history_path()
    snaps = _load_history(history_path)
    exec_rows = _load_exec_rows(exec_log_path)

    timelines: dict[str, list[tuple]] = {}

    def _timeline(metric: str) -> list[tuple]:
        if metric not in timelines:
            tl = [(dt, float(v)) for dt, vals in snaps for k, v in vals.items()
                  if k.split("/")[-1] == metric and isinstance(v, (int, float))]
            tl.sort()
            timelines[metric] = tl
        return timelines[metric]

    per_skill: dict[str, list[float]] = {}
    events: list[dict] = []
    pending = 0

    for row in exec_rows:
        skill = row.get("skill", "")
        fired_at = parse_ts(row.get("timestamp"))
        reflex_id = str(row.get("reflex_id", ""))
        parts = reflex_id.split(":")
        if not skill or not fired_at or len(parts) < 3 or parts[0] not in ("metric", "trajectory"):
            continue
        metric = ":".join(parts[2:])
        rule = insights.METRIC_RULES.get(metric)
        if not rule:
            continue  # need a direction to sign-normalize

        pre_med, post_med = _pre_post_medians(fired_at, _timeline(metric))
        if pre_med is None or post_med is None:
            pending += 1
            continue

        delta = post_med - pre_med
        if rule["dir"] == "lower":
            delta = -delta  # a drop in a lower-is-better metric is improvement -> positive
        per_skill.setdefault(skill, []).append(delta)
        events.append({
            "metric": metric, "skill": skill, "delta": round(delta, 3),
            "pre_median": round(pre_med, 3), "post_median": round(post_med, 3),
            "fired_at": fired_at.isoformat(),
        })

    by_skill = {s: round(statistics.median(ds), 3) for s, ds in per_skill.items()}
    all_deltas = [d for ds in per_skill.values() for d in ds]
    overall = round(statistics.median(all_deltas), 3) if all_deltas else None

    return {
        "overall": overall,
        "by_skill": by_skill,
        "pending": pending,
        "events": sorted(events, key=lambda e: e["fired_at"])[-20:],
        "note": ("delta = median(3 post-firing values) - median(3 pre-firing values), "
                 "sign-normalized by the metric's dir so improvement is always positive; "
                 "attempts with <3 real history values on either side are pending, not scored"),
    }
