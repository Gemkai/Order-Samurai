"""Remediation_Delta — HOW MUCH a remediation attempt moved its target metric, not
just whether a judged event called it "improved".

remediation.efficacy() answers yes/no per judged before/after event, and its honest
headline (improvement_rate) still collapses every attempt into a single win/loss bit.
This module scores magnitude: for each exec_log attempt tied to a specific metric
(reflex_id "metric:pillar:MetricName" or "trajectory:pillar:MetricName"), take the
metric's real historical values from Data/telemetry/metrics_history.jsonl and compute

    delta = median(3 post-firing values) - median(3 pre-firing values)

sign-normalized by the metric's `dir` (insights.METRIC_RULES) so a positive delta
always means improvement, whichever direction the raw metric moves.

Attempts that cannot be scored are counted in three HONEST buckets (2026-08-08 seq 9d) —
one undifferentiated "pending" read as a drainable queue when ~51 of its 55 entries could
never drain:

  pending      post-firing side has <3 history values. Resolves on its own as history
               accrues — genuinely "not yet".
  unscoreable  pre-firing side has <3 history values. PERMANENT: history before a past
               firing cannot grow. (Most of these are fallout from the backfill bug that
               truncated live-only series to a single point.)
  unrated      the attempt names a metric with no insights.METRIC_RULES entry, so there
               is no direction to sign-normalize by. Previously dropped in silence.

`overall` is the median of deltas expressed as a PERCENT of each metric's own pre-firing
median, not of raw deltas: a cross-skill median over mixed units is dominated by whichever
metric has the largest scale. `overall_raw` keeps the old raw-unit median, and `by_skill`
stays in each skill's raw metric units.

Observational only (2026-08-01 metric-gap remediation, phase B1): no reflex-engine
consumer reads this, and no grading threshold exists yet — thresholds are set from
the first week of real data (calibration gate), not guessed.
"""
from __future__ import annotations

import statistics
from pathlib import Path

from . import insights
from .reflex_id import parse_reflex_id
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


def _score_status(fired_at, timeline: list[tuple]) -> str:
    """Why an attempt is unscorable: "unscoreable" (too few PRE values — permanent,
    the past cannot grow more history), "pending" (too few POST values — resolves as
    history accrues) or "ok". Pre wins when both sides are short: no amount of waiting
    fixes a missing pre-firing baseline."""
    pre = [v for dt, v in timeline if dt <= fired_at]
    post = [v for dt, v in timeline if dt > fired_at]
    if len(pre) < 3:
        return "unscoreable"
    if len(post) < 3:
        return "pending"
    return "ok"


def _as_percent(delta: float, pre_med: float) -> float:
    """Delta as a percent of the metric's own pre-firing median, so the cross-skill
    median is not dominated by whichever metric carries the biggest raw units (a
    5-point Error_Rate drop and a 5-second latency drop are not the same achievement).
    Sign-only fallback when the pre-median is 0 and a percentage is undefined."""
    if pre_med == 0:
        return 0.0 if delta == 0 else (100.0 if delta > 0 else -100.0)
    return 100.0 * delta / abs(pre_med)


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
            # Per-session metrics (rule["per"] == "session") are normalized by that same
            # snapshot's Session_Count before scoring -- the same normalization
            # remediation.efficacy() applies (SENSEI-6). Without it, a session-volume
            # change between the pre/post windows can flip the reported sign: a raw
            # count that rose because sessions rose faster reads as a regression even
            # when the real per-session rate improved.
            rule = insights.METRIC_RULES.get(metric) or {}
            per_session = rule.get("per") == "session"
            tl = []
            for dt, vals in snaps:
                sessions = None
                if per_session:
                    sessions = next(
                        (float(v2) for k2, v2 in vals.items()
                         if k2.split("/")[-1] == "Session_Count"
                         and isinstance(v2, (int, float)) and v2 > 0),
                        None,
                    )
                    if sessions is None:
                        continue  # can't normalize this snapshot -> can't honestly score it
                for k, v in vals.items():
                    if k.split("/")[-1] == metric and isinstance(v, (int, float)):
                        tl.append((dt, float(v) / sessions if sessions else float(v)))
            tl.sort()
            timelines[metric] = tl
        return timelines[metric]

    per_skill: dict[str, list[float]] = {}
    pct_deltas: list[float] = []
    events: list[dict] = []
    pending = 0        # post side short — will resolve as history accrues
    unscoreable = 0    # pre side short — PERMANENT, the past cannot grow history
    unrated = 0        # no METRIC_RULES entry — no direction to sign-normalize by

    for row in exec_rows:
        skill = row.get("skill", "")
        fired_at = parse_ts(row.get("timestamp"))
        reflex_id = str(row.get("reflex_id", ""))
        metric = parse_reflex_id(reflex_id).metric
        if not skill or not fired_at or metric is None:
            continue
        rule = insights.METRIC_RULES.get(metric)
        if not rule:
            unrated += 1  # counted, not dropped: a silent skip hid 8 attempts
            continue

        status = _score_status(fired_at, _timeline(metric))
        if status != "ok":
            if status == "unscoreable":
                unscoreable += 1
            else:
                pending += 1
            continue
        pre_med, post_med = _pre_post_medians(fired_at, _timeline(metric))

        delta = post_med - pre_med
        if rule["dir"] == "lower":
            delta = -delta  # a drop in a lower-is-better metric is improvement -> positive
        pct = _as_percent(delta, pre_med)
        per_skill.setdefault(skill, []).append(delta)
        pct_deltas.append(pct)
        events.append({
            "metric": metric, "skill": skill, "delta": round(delta, 3),
            "delta_pct": round(pct, 3),
            "pre_median": round(pre_med, 3), "post_median": round(post_med, 3),
            "fired_at": fired_at.isoformat(),
        })

    by_skill = {s: round(statistics.median(ds), 3) for s, ds in per_skill.items()}
    all_deltas = [d for ds in per_skill.values() for d in ds]

    return {
        # Normalized (percent-of-pre-median) so mixed-unit metrics don't skew the
        # cross-skill median; overall_raw keeps the old raw-unit figure.
        "overall": round(statistics.median(pct_deltas), 3) if pct_deltas else None,
        "overall_raw": round(statistics.median(all_deltas), 3) if all_deltas else None,
        "by_skill": by_skill,
        "pending": pending,
        "unscoreable": unscoreable,
        "unrated": unrated,
        "events": sorted(events, key=lambda e: e["fired_at"])[-20:],
        "note": ("delta = median(3 post-firing values) - median(3 pre-firing values), "
                 "sign-normalized by the metric's dir so improvement is always positive; "
                 "overall is the median of those deltas as a PERCENT of each metric's own "
                 "pre-firing median (overall_raw = raw-unit median; by_skill stays in raw "
                 "metric units); unscorable attempts are split three ways — pending "
                 "(<3 post values, resolves with time), unscoreable (<3 pre values, "
                 "permanent) and unrated (no METRIC_RULES direction)"),
    }
