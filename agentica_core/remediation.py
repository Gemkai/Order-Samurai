"""Remediation efficacy — did running a metric's remediation skill (in response to a flag)
actually move the metric the right way?

Two evidence tiers (2026-07-19 metric surface review §A1):

1. FIRE-TIME MEASUREMENT (preferred): reflex-engine.ts records the metric's live value
   before and after each autonomous run (metric_before / metric_after on the exec_log
   row). Those rows become direct events — no snapshot bracketing needed.
2. CORRELATION, NOT CAUSATION (fallback for runs without fire-time values, e.g. human /
   telemetry uses): metric M was flagged at snapshot t_a, its remediation skill S was
   used at t_b > t_a, and the next snapshot t_c > t_b shows M moved toward healthy.
   Confounds exist (other work, noise); this measures association, not proof.

Separately, EVERY exec_log run counts as an ATTEMPT (attempted/completed), including
no_change/error/timeout — an engine that tries 49 times and improves nothing must show
up as exactly that, not as silence.

Sources (no new logging): state/exec_log.jsonl + metrics_history.jsonl (M over time)
+ telemetry skills_used+timestamp + insights.METRIC_RULES (grade any value)
+ insights.REMEDIATION (metric -> skill).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import insights
from .adapter import list_platforms
from .telemetry import parse_ts

import os

_THIS = Path(__file__).resolve()
_local_root = _THIS.parents[1]
if (_local_root / "config").exists() and not (_local_root / "Order Samurai").exists():
    _default_root = _local_root
else:
    _default_root = _local_root / "Order Samurai"
_OS_ROOT = Path(os.environ.get("ORDER_SAMURAI_ROOT", str(_default_root)))
_EXEC_LOG = _OS_ROOT / "state" / "exec_log.jsonl"


def _load_history(path: Path) -> list[tuple[datetime, dict]]:
    out = []
    if path.exists():
        for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                row = json.loads(ln)
            except ValueError:
                continue
            dt = parse_ts(row.get("ts"))
            if dt:
                out.append((dt, row.get("values", {})))
    out.sort(key=lambda x: x[0])
    return out


def _load_exec_rows(exec_log_path=None) -> list[dict]:
    """All parseable rows of the exec log (best-effort; [] when absent). Tests inject
    `exec_log_path` for isolation — the default is the LIVE engine log, which grows
    between runs and must never leak into fixture-based assertions."""
    rows: list[dict] = []
    _EXEC_LOG_PATH = Path(exec_log_path) if exec_log_path is not None else _EXEC_LOG
    if _EXEC_LOG_PATH.exists():
        for ln in _EXEC_LOG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                row = json.loads(ln)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _has_fire_time_measurement(row: dict) -> bool:
    """True when the reflex engine recorded real before/after metric values on this
    row at fire time (metric surface review §A1). Such rows become direct efficacy
    events and must NOT also feed the snapshot-correlation path — that would count
    one physical run twice."""
    return (
        isinstance(row.get("metric_before"), (int, float))
        and isinstance(row.get("metric_after"), (int, float))
        and not isinstance(row.get("metric_before"), bool)
        and not isinstance(row.get("metric_after"), bool)
    )


def _skill_uses(records: list[dict]) -> dict[str, list[tuple[datetime, str]]]:
    """skill name -> sorted (timestamp, actor) pairs.

    Actor is "ronin" when the action was triggered by the reflex engine
    automatically, "human" for dashboard button clicks or AI session use.

    Two sources:
      1. Telemetry records with a ``skills_used`` list (emitted by AI sessions).
      2. ``state/exec_log.jsonl`` — written by the API server whenever a skill
         runs from the dashboard.  source=="reflex_engine" marks automated runs.
    """
    uses: dict[str, list[tuple[datetime, str]]] = {}

    # Source 1: telemetry records (Claude Code sessions — human-initiated)
    for r in records:
        dt = parse_ts(r.get("timestamp"))
        if not dt:
            continue
        for s in (r.get("skills_used") or []):
            uses.setdefault(s, []).append((dt, "human"))

    # Source 2: dashboard exec_log (best-effort; absent on first run). Rows carrying a
    # fire-time before/after measurement are excluded — efficacy() turns those into
    # direct events, so feeding them into the snapshot correlation would double-count.
    for row in _load_exec_rows():
        if row.get("status") != "done":
            continue
        if _has_fire_time_measurement(row):
            continue
        dt = parse_ts(row.get("timestamp"))
        skill = row.get("skill", "")
        if dt and skill:
            actor = "ronin" if row.get("source") == "reflex_engine" else "human"
            uses.setdefault(skill, []).append((dt, actor))

    for s in uses:
        # Sort by timestamp; break ties so exec_log (ronin/human) entries sort before
        # telemetry entries — exec_log has ground-truth actor attribution and should
        # win deduplication when Ronin fires a skill that also appears in telemetry.
        uses[s].sort(key=lambda x: (x[0], 0 if x[1] == "ronin" else 1))
    return uses


def efficacy(history_path: Path | None = None, records: list[dict] | None = None,
             exec_log_path: Path | None = None) -> dict:
    history_path = history_path or insights.default_history_path()
    if records is None:
        # Use aggregate.load_records so entries are normalized + validated (same path as
        # aggregate.py). Raw JSONL reads skip normalize_entry, causing _skill_uses to miss
        # records whose timestamp / skills_used fields differ by platform naming convention.
        # Lazy import: aggregate imports remediation, so a top-level import would be circular.
        from .aggregate import load_records  # noqa: PLC0415
        records = []
        for p in list_platforms():
            records.extend(load_records(p))

    snaps = _load_history(history_path)
    uses = _skill_uses(records)
    exec_rows = _load_exec_rows(exec_log_path)
    events: list[dict] = []

    # ── Attempt counting (§A1): every exec_log run is an ATTEMPT, whatever its
    # status. Zero engine runs finishing "done" used to render as total silence on
    # the panel; "the engine tried N times and improved nothing" is the finding
    # that matters, so no_change/error/timeout rows count here.
    attempted = 0
    completed = 0
    attempts_by_skill: dict[str, int] = {}
    for row in exec_rows:
        skill = row.get("skill", "")
        if not skill or not parse_ts(row.get("timestamp")):
            continue
        attempted += 1
        completed += row.get("status") == "done"
        attempts_by_skill[skill] = attempts_by_skill.get(skill, 0) + 1

    # ── Direct events from fire-time measurements: the engine records the metric's
    # live value before and after each autonomous run (reflex-engine.ts §A1), so
    # these rows are judged on their own before/after instead of waiting for the
    # sparse metrics_history snapshots to bracket them.
    for row in exec_rows:
        if not _has_fire_time_measurement(row):
            continue
        rid = str(row.get("reflex_id", ""))
        parts = rid.split(":")
        if len(parts) < 3 or parts[0] not in ("metric", "trajectory"):
            continue
        metric = ":".join(parts[2:])
        rule = insights.METRIC_RULES.get(metric)
        if not rule:
            continue  # need a direction to judge improvement
        dt = parse_ts(row.get("timestamp"))
        skill = row.get("skill", "")
        if not dt or not skill:
            continue
        va, vc = float(row["metric_before"]), float(row["metric_after"])
        improved_row = (vc > va) if rule["dir"] == "higher" else (vc < va)
        worse = (vc < va) if rule["dir"] == "higher" else (vc > va)
        outcome = "improved" if improved_row else ("regressed" if worse else "flat")
        events.append({
            "metric": metric, "skill": skill, "command": row.get("command", ""),
            "before": round(va, 2), "after": round(vc, 2), "outcome": outcome,
            "used_at": dt.isoformat(),
            "actor": "ronin" if row.get("source") == "reflex_engine" else "human",
        })

    for metric, rem in insights.REMEDIATION.items():
        rule = insights.METRIC_RULES.get(metric)
        if not rule:
            continue  # need a direction to judge improvement
        skill = rem["skill"]
        if skill not in uses:
            continue
        # metric timeline: (dt, numeric value) from snapshots whose key ends in /<metric>
        tl = []
        for dt, vals in snaps:
            for k, v in vals.items():
                if k.split("/")[-1] == metric and isinstance(v, (int, float)):
                    tl.append((dt, float(v)))
        tl.sort()
        if len(tl) < 2:
            continue
        # Deduplicate: track (actor, before_val, after_val) per metric to prevent multiple
        # telemetry sessions logging the same skill use in the same snapshot window from
        # inflating counts. Actor is included so ronin and human runs are deduplicated
        # separately — a ronin run should not hide a human run or vice versa.
        seen_pairs: set[tuple[str, float, float]] = set()
        for ub, actor in uses[skill]:
            before = [(dt, v) for dt, v in tl if dt <= ub and insights._health(v, rule) < 40]
            after = [(dt, v) for dt, v in tl if dt > ub]
            if not before or not after:
                continue  # skill not used while flagged, or no post-use snapshot
            va, vc = before[-1][1], after[0][1]
            pair = (actor, round(va, 2), round(vc, 2))
            if pair in seen_pairs:
                continue  # same actor+window already recorded; skip duplicate
            seen_pairs.add(pair)
            improved = (vc > va) if rule["dir"] == "higher" else (vc < va)
            worse = (vc < va) if rule["dir"] == "higher" else (vc > va)
            outcome = "improved" if improved else ("regressed" if worse else "flat")
            events.append({
                "metric": metric, "skill": skill, "command": rem["command"],
                "before": round(va, 2), "after": round(vc, 2), "outcome": outcome,
                "used_at": ub.isoformat(), "actor": actor,
            })

    applied = len(events)
    improved = sum(1 for e in events if e["outcome"] == "improved")
    regressed = sum(1 for e in events if e["outcome"] == "regressed")
    flat = applied - improved - regressed
    by_skill: dict[str, dict] = {}
    for skill, n in attempts_by_skill.items():
        by_skill[skill] = {"applied": 0, "improved": 0, "attempted": n}
    for e in events:
        b = by_skill.setdefault(e["skill"], {"applied": 0, "improved": 0, "attempted": 0})
        b["applied"] += 1
        b["improved"] += e["outcome"] == "improved"
    return {
        "applied": applied,
        "improved": improved,
        "regressed": regressed,
        "flat": flat,
        # Attempt counters (§A1) — extend, don't rename: `applied` stays "runs with a
        # judged before/after"; these count every exec_log run regardless of outcome.
        "attempted": attempted,
        "completed": completed,
        "success_rate": round(100 * improved / applied, 1) if applied else None,
        "by_skill": by_skill,
        "events": sorted(events, key=lambda e: e["used_at"])[-20:],  # most recent
        "note": ("fire-time before/after where recorded; else correlation not causation "
                 "(flag -> skill used -> next snapshot moved healthy); attempted counts "
                 "every engine run incl. no_change/error/timeout"),
    }


def main() -> int:
    r = efficacy()
    print(f"Remediation efficacy: {r['attempted']} attempted · {r['completed']} completed · "
          f"{r['applied']} applied · {r['improved']} improved · "
          f"{r['regressed']} regressed · {r['flat']} flat · success {r['success_rate']}%")
    for s, b in sorted(r["by_skill"].items()):
        print(f"  {s}: {b['improved']}/{b['applied']} improved")
    for e in r["events"][-8:]:
        print(f"  [{e['outcome']}] {e['metric']} {e['before']}->{e['after']} via {e['command']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
