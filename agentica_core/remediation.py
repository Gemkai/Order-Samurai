"""Remediation efficacy — did running a metric's remediation skill (in response to a flag)
actually move the metric the right way?

Three evidence tiers (2026-07-19 metric surface review §A1; tier 2 added 2026-08-08 seq 9b):

1. FIRE-TIME MEASUREMENT (preferred): reflex-engine.ts records the metric's live value
   before and after each autonomous run (metric_before / metric_after on the exec_log
   row). Those rows become direct events — no snapshot bracketing needed.
2. ENGINE VERDICT (fallback when no numeric pair): the engine's own boolean `improved`
   field on a reflex_engine row — its ground-truth judgment recorded at settle time.
   Also direct events, excluded from snapshot correlation exactly like tier 1. That
   judgment is made inside ONE ≤60 s post-run refresh, so a metric on a nightly/weekly
   recompute cadence records false even when the fix worked; a late re-judgment sidecar
   (state/exec_log_rejudge.jsonl, written by refresh_dashboard) may upgrade such a row
   by entry_hash. It only ever amends THIS tier — a tier-1 numeric measurement wins.
3. CORRELATION, NOT CAUSATION (fallback for runs without fire-time values, e.g. human /
   telemetry uses): metric M was flagged at snapshot t_a, its remediation skill S was
   used at t_b > t_a, and the next snapshot t_c > t_b shows M moved toward healthy.
   Confounds exist (other work, noise); this measures association, not proof.

Separately, EVERY exec_log run counts as an ATTEMPT (attempted/completed), including
no_change/error/timeout — an engine that tries 49 times and improves nothing must show
up as exactly that, not as silence.

WINDOWING (2026-08-08 seq 9a): the headline counters are scoped to a trailing window
when the caller passes `window_days`. Counting the whole exec_log lifetime froze the
dashboard at one number that could not move — 18 days of zero autonomous attempts read
exactly like a healthy engine. Lifetime counts are kept alongside as
`*_lifetime` fields so no downstream reader loses data.

Sources (no new logging): state/exec_log.jsonl + metrics_history.jsonl (M over time)
+ telemetry skills_used+timestamp + insights.METRIC_RULES (grade any value)
+ insights.REMEDIATION (metric -> skill).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import insights
from .adapter import list_platforms
from .reflex_id import parse_reflex_id
from .telemetry import parse_ts

import os

_THIS = Path(__file__).resolve()
# exec_log.jsonl is written by the API server each time a dashboard skill button is clicked.
_OS_ROOT = Path(os.environ.get("ORDER_SAMURAI_ROOT",
    str(_THIS.parents[1] / "Order Samurai")))
_EXEC_LOG = _OS_ROOT / "state" / "exec_log.jsonl"
# Late re-judgment sidecar (see skill_efficacy.load_rejudged_hashes). Sits NEXT TO the
# exec log, never inside it: exec_log.jsonl is hash-chained and must stay byte-stable.
_REJUDGE_SIDECAR_NAME = "exec_log_rejudge.jsonl"


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


def _kill_switch_off(name: str) -> bool:
    """True when env var `name` explicitly disables a default-ON behaviour. House
    idiom: the kill switch defaults to the FIXED behaviour, so an operator can only
    opt BACK to the old semantics (mirrors doctor.AUDIT_CANARY_ENABLED)."""
    return os.environ.get(name, "true").strip().lower() in ("false", "0", "no")


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


def _has_engine_verdict(row: dict) -> bool:
    """Tier 2 (2026-08-08 seq 9b): the engine's OWN boolean `improved` judgment —
    but ONLY on a run that actually settled. Ground truth the headline was throwing
    away — the exec_log recorded 11 successes while the dashboard said 3.

    Settled-status gate: the engine's terminal vocabulary is done|error|timeout|
    no_change (reflex-engine.ts §_afterRun). `improved` is a real verdict only on
    the two that reached an outcome; on an `error`/`timeout` row it is the default
    written by a run that never finished, so grading it "flat" would assert the
    metric was observed and did not move — a measurement nobody took. Those rows
    stay in the `attempted` denominator (a crashed remediation IS a failed attempt)
    but never become graded events. Live log at time of writing: 24 error + 10
    timeout rows would otherwise have been ~32% of a 107-event `applied`.

    Precedence guard: a row carrying BOTH a fire-time numeric pair and the boolean
    is tier 1 and is counted exactly once (numeric wins) — hence the explicit
    `not _has_fire_time_measurement`. Like tier 1 these are DIRECT events and must
    not also feed the snapshot-correlation path.

    Kill switch: REMEDIATION_ENGINE_VERDICT=false restores the two-tier behaviour."""
    if _kill_switch_off("REMEDIATION_ENGINE_VERDICT"):
        return False
    return (
        row.get("source") == "reflex_engine"
        and row.get("status") in ("done", "no_change")
        and isinstance(row.get("improved"), bool)
        and not _has_fire_time_measurement(row)
    )


def _is_autonomous_attempt(row: dict) -> bool:
    """§A1 channel filter: only live ReflexEngine remediation runs belong in
    Self_Correction_Rate. Manual/dashboard rows, read-only diagnostic mechanisms and
    proposal-only worktree candidates are separate channels; mixing their outcomes
    into this denominator/numerator made the metric claim autonomous self-correction
    it did not perform. Every direct event must pass this too, or `improved` could
    exceed its own `attempted` denominator."""
    if not row.get("skill") or not parse_ts(row.get("timestamp")):
        return False
    if row.get("source") != "reflex_engine":
        return False
    if row.get("propose_only") is True:
        return False
    if row.get("kind") == "mechanism" and row.get("read_only") is True:
        return False
    return True


def _window_predicate(window_days: int | None, now: datetime | None):
    """dt -> bool for the trailing headline window. Lifetime (always True) when the
    caller asked for no window or the kill switch REMEDIATION_WINDOW=false is set.
    Naive timestamps are read as UTC (same rule as aggregate._within_days)."""
    if window_days is None or _kill_switch_off("REMEDIATION_WINDOW"):
        return lambda dt: True
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    cutoff = ref - timedelta(days=window_days)

    def _within(dt: datetime | None) -> bool:
        if dt is None:
            return False
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= cutoff

    return _within


def _skill_uses(records: list[dict], exec_log_path=None) -> dict[str, list[tuple[datetime, str]]]:
    """skill name -> sorted (timestamp, actor) pairs.

    Actor is "reflex" when the action was triggered by the always-on reflex
    engine automatically (threshold breach, or its ronin-mode regression
    bridge), "human" for dashboard button clicks or AI session use. NOTE:
    despite the name, this is NEVER the Sensei/ronin-mode backlog-implementation
    worker (bin/ronin-pillar, subagent_type="ronin") — that path commits directly
    via git and never touches exec_log.jsonl, so it is invisible here by design;
    see Backlog_Burn_Rate for that work instead (Research/METRICS.md, 2026-07-14).

    Two sources:
      1. Telemetry records with a ``skills_used`` list (emitted by AI sessions).
      2. ``state/exec_log.jsonl`` — written by the API server whenever a skill
         runs from the dashboard.  source=="reflex_engine" marks automated runs.
    """
    uses: dict[str, list[tuple[datetime, str]]] = {}

    # Source 1: telemetry records (Claude Code sessions — human-initiated).
    # The SessionEnd emitter re-emits a session's ENTIRE skills_used list on each
    # resume, so without per-(session, skill) dedup one invocation reappears as a
    # fresh "use" at every later emit timestamp — phantom uses that can land in
    # later snapshot windows. Keep the EARLIEST timestamp per (session, skill);
    # records without a real session identity keep one use per record.
    seen_sess_skill: set[tuple[str, str]] = set()
    for r in records:
        dt = parse_ts(r.get("timestamp"))
        if not dt:
            continue
        sid = r.get("session_id")
        real_sid = sid if sid and sid != "local-session" else None
        for s in (r.get("skills_used") or []):
            if real_sid:
                if (real_sid, s) in seen_sess_skill:
                    continue
                seen_sess_skill.add((real_sid, s))
            uses.setdefault(s, []).append((dt, "human"))

    # Source 2: dashboard exec_log (best-effort; absent on first run). Rows carrying a
    # fire-time before/after measurement — or the engine's own `improved` verdict —
    # are excluded: efficacy() turns those into direct events (tiers 1 and 2), so
    # feeding them into the snapshot correlation would double-count one physical run.
    for row in _load_exec_rows(exec_log_path):
        if row.get("status") != "done":
            continue
        if _has_fire_time_measurement(row) or _has_engine_verdict(row):
            continue
        dt = parse_ts(row.get("timestamp"))
        skill = row.get("skill", "")
        if dt and skill:
            actor = "reflex" if row.get("source") == "reflex_engine" else "human"
            uses.setdefault(skill, []).append((dt, actor))

    for s in uses:
        # Sort by timestamp; break ties so exec_log (reflex/human) entries sort before
        # telemetry entries — exec_log has ground-truth actor attribution and should
        # win deduplication when the reflex engine fires a skill that also appears in telemetry.
        uses[s].sort(key=lambda x: (x[0], 0 if x[1] == "reflex" else 1))
    return uses


def efficacy(history_path: Path | None = None, records: list[dict] | None = None,
             exec_log_path: Path | None = None, window_days: int | None = None,
             now: datetime | None = None, rejudge_path: Path | None = None) -> dict:
    """`window_days` scopes the HEADLINE counters to a trailing window (None = the
    lifetime behaviour every existing caller had). `now` is injectable so tests never
    assert against a live clock.

    `rejudge_path` overrides the late-re-judgment sidecar location (default: next to
    the exec log). The sidecar upgrades a fire-time `improved: false` on the TIER-2
    boolean channel only — tier 1's numeric before/after pair still wins, because
    _has_engine_verdict already excludes any row carrying one, so a measured
    non-improvement can never be overturned by a sidecar row."""
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
    uses = _skill_uses(records, exec_log_path)
    exec_rows = _load_exec_rows(exec_log_path)
    # Lazy import: skill_efficacy owns the sidecar contract (it is the other consumer),
    # and it imports nothing from this package, so there is no cycle.
    from .skill_efficacy import load_rejudged_hashes  # noqa: PLC0415
    if rejudge_path is None:
        base = Path(exec_log_path) if exec_log_path is not None else _EXEC_LOG
        rejudge_path = base.parent / _REJUDGE_SIDECAR_NAME
    rejudged = load_rejudged_hashes(rejudge_path)
    in_window = _window_predicate(window_days, now)
    events: list[dict] = []
    # A validated patch measured inside an isolated worktree is useful evidence,
    # but it did not change the live repository. Keep proposal efficacy separate
    # so a promising candidate cannot inflate the applied/live success rate.
    proposal_events: list[dict] = []

    # ── Autonomous attempt counting (§A1, channel filter in _is_autonomous_attempt).
    # Counted twice over: once inside the trailing window (the headline, so an engine
    # that stopped firing reads as a data gap instead of a frozen lifetime rate) and
    # once over the whole log (the `*_lifetime` fields, so nothing is lost).
    attempted = attempted_lifetime = 0
    completed = completed_lifetime = 0
    attempts_by_skill: dict[str, int] = {}
    for row in exec_rows:
        if not _is_autonomous_attempt(row):
            continue
        attempted_lifetime += 1
        completed_lifetime += row.get("status") == "done"
        if not in_window(parse_ts(row.get("timestamp"))):
            continue
        attempted += 1
        completed += row.get("status") == "done"
        skill = row["skill"]
        attempts_by_skill[skill] = attempts_by_skill.get(skill, 0) + 1

    # ── Direct events from fire-time measurements: the engine records the metric's
    # live value before and after each autonomous run (reflex-engine.ts §A1), so
    # these rows are judged on their own before/after instead of waiting for the
    # sparse metrics_history snapshots to bracket them.
    for row in exec_rows:
        if not _has_fire_time_measurement(row):
            continue
        rid = str(row.get("reflex_id", ""))
        metric = parse_reflex_id(rid).metric
        if metric is None:
            continue  # correlation/manual/malformed ids name no metric to judge
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
        event = {
            "metric": metric, "skill": skill, "command": row.get("command", ""),
            "before": round(va, 2), "after": round(vc, 2), "outcome": outcome,
            "used_at": dt.isoformat(),
            "actor": "reflex" if row.get("source") == "reflex_engine" else "human",
            "evidence": "fire_time",
        }
        if row.get("propose_only") is True:
            proposal_events.append(event)
        else:
            events.append(event)

    # ── Tier 2: engine verdict. The row has no numeric pair, but the run SETTLED
    # (done/no_change) and the engine judged the outcome and wrote it as `improved`.
    # A boolean cannot tell a regression from a no-op, so False grades "flat" —
    # never "regressed" (that would invent evidence the field does not carry).
    # _has_engine_verdict already excludes tier-1 rows and unsettled (error/timeout)
    # runs, so a row with BOTH kinds of evidence is counted once and a crashed run
    # is never graded at all.
    for row in exec_rows:
        if not _has_engine_verdict(row) or not _is_autonomous_attempt(row):
            continue
        dt = parse_ts(row.get("timestamp"))
        rid = str(row.get("reflex_id", ""))
        # Metric-scoped reflex ids name their metric; correlation/other ids don't, and
        # the verdict is still real — attribute it to the reflex id itself rather than
        # dropping the row (unlike tier 1, no METRIC_RULES direction is needed here).
        metric = parse_reflex_id(rid).metric or (rid or row["skill"])
        # Late re-judgment (2026-08-08): the engine decides `improved` inside one ≤60 s
        # post-run refresh, so a metric that only recomputes nightly/weekly is recorded
        # false even when the remediation worked. The sidecar carries that later verdict,
        # keyed by the row's entry_hash; exec_log.jsonl itself is never rewritten.
        late = row["improved"] is False and isinstance(row.get("entry_hash"), str) \
            and row["entry_hash"] in rejudged
        event = {
            "metric": metric, "skill": row["skill"], "command": row.get("command", ""),
            # No numeric reading exists for this tier; null keeps the key set uniform
            # instead of implying a measurement that was never taken.
            "before": None, "after": None,
            "outcome": "improved" if (row["improved"] or late) else "flat",
            "used_at": dt.isoformat(), "actor": "reflex", "evidence": "engine_verdict",
        }
        if late:
            event["late_rejudge"] = True
        events.append(event)

    for metric, rem in insights.REMEDIATION.items():
        rule = insights.METRIC_RULES.get(metric)
        if not rule:
            continue  # need a direction to judge improvement
        skill = rem["skill"]
        if skill not in uses:
            continue
        # metric timeline: (dt, numeric value) from snapshots whose key ends in /<metric>.
        # Per-session metrics (rule["per"] == "session") are normalized by that same
        # snapshot's Session_Count before grading -- the same normalization annotate()
        # and correlation.py apply (SENSEI-6). Without it, a raw cumulative total is
        # compared directly against a per-session warn/fail bar and reads as a deep
        # breach regardless of the real per-session rate.
        tl = []
        for dt, vals in snaps:
            sessions = None
            if rule.get("per") == "session":
                sessions = next(
                    (float(v2) for k2, v2 in vals.items()
                     if k2.split("/")[-1] == "Session_Count" and isinstance(v2, (int, float)) and v2 > 0),
                    None,
                )
                if sessions is None:
                    continue  # can't normalize this snapshot -> can't honestly grade it
            for k, v in vals.items():
                if k.split("/")[-1] == metric and isinstance(v, (int, float)):
                    tl.append((dt, float(v) / sessions if sessions else float(v)))
        tl.sort()
        if len(tl) < 2:
            continue
        # Deduplicate per snapshot WINDOW (before_ts, after_ts): the metric's
        # movement across one window can only be attributed once, however many
        # log entries land inside it. Keyed on snapshot TIMESTAMPS, not values —
        # value-keyed dedup conflated distinct windows whose before/after happened
        # to be equal, hiding real repeat runs (the reflex engine's 2026-06-09..14
        # simplify successes were invisible on the dashboard). Actor is NOT in the
        # key: a reflex-fired run also lands in session telemetry as a "human" use,
        # so actor-keyed dedup recorded one physical run twice. When both actors
        # share a window, reflex wins — exec_log has ground-truth attribution.
        window_events: dict[tuple[datetime, datetime], dict] = {}
        for ub, actor in uses[skill]:
            before = [(dt, v) for dt, v in tl if dt <= ub and insights._health(v, rule) < 40]
            after = [(dt, v) for dt, v in tl if dt > ub]
            if not before or not after:
                continue  # skill not used while flagged, or no post-use snapshot
            va, vc = before[-1][1], after[0][1]
            window = (before[-1][0], after[0][0])
            existing = window_events.get(window)
            if existing is not None:
                if actor == "reflex" and existing["actor"] == "human":
                    existing["actor"] = "reflex"
                    existing["used_at"] = ub.isoformat()
                continue
            improved = (vc > va) if rule["dir"] == "higher" else (vc < va)
            worse = (vc < va) if rule["dir"] == "higher" else (vc > va)
            outcome = "improved" if improved else ("regressed" if worse else "flat")
            window_events[window] = {
                "metric": metric, "skill": skill, "command": rem["command"],
                "before": round(va, 2), "after": round(vc, 2), "outcome": outcome,
                "used_at": ub.isoformat(), "actor": actor,
                "evidence": "snapshot_correlation",
            }
        events.extend(window_events.values())

    # Split causal channels before computing the headline. Snapshot correlation
    # from a human session remains useful observational evidence, but it cannot
    # raise (or lower) an autonomous engine score.
    human_events = [e for e in events if e.get("actor") != "reflex"]
    events_lifetime = [e for e in events if e.get("actor") == "reflex"]

    # Headline counters follow the same trailing window as `attempted`; the lifetime
    # tallies stay available so no downstream reader loses the long view.
    events = [e for e in events_lifetime if in_window(parse_ts(e["used_at"]))]
    human_events = [e for e in human_events if in_window(parse_ts(e["used_at"]))]
    proposal_events = [e for e in proposal_events if in_window(parse_ts(e["used_at"]))]

    applied = len(events)
    improved = sum(1 for e in events if e["outcome"] == "improved")
    regressed = sum(1 for e in events if e["outcome"] == "regressed")
    flat = applied - improved - regressed
    applied_lifetime = len(events_lifetime)
    improved_lifetime = sum(1 for e in events_lifetime if e["outcome"] == "improved")
    proposed_count = len(proposal_events)
    proposed_improved = sum(1 for e in proposal_events if e["outcome"] == "improved")
    by_skill: dict[str, dict] = {}
    for skill, n in attempts_by_skill.items():
        by_skill[skill] = {"applied": 0, "improved": 0, "attempted": n}
    for e in events:
        b = by_skill.setdefault(e["skill"], {"applied": 0, "improved": 0, "attempted": 0})
        b["applied"] += 1
        b["improved"] += e["outcome"] == "improved"
    # A window with zero autonomous attempts is a DATA GAP, not a 0% score: an engine
    # that has not fired in N days must say so out loud. (Lifetime callers — window_days
    # None — keep the old "attempted == 0 -> improvement_rate None" behaviour.)
    windowing = window_days is not None and not _kill_switch_off("REMEDIATION_WINDOW")
    data_gap = windowing and attempted == 0
    return {
        "applied": applied,
        "improved": improved,
        "regressed": regressed,
        "flat": flat,
        # Attempt counters (§A1) — extend, don't rename: `applied` stays "runs with a
        # judged before/after"; these count every exec_log run regardless of outcome.
        "attempted": attempted,
        "completed": completed,
        # Window bookkeeping (2026-08-08 seq 9a). `window_days` is None for lifetime
        # callers; the `*_lifetime` counters are the pre-windowing totals, kept so the
        # windowed headline never destroys the long view.
        "window_days": window_days if windowing else None,
        "attempted_lifetime": attempted_lifetime,
        "completed_lifetime": completed_lifetime,
        "applied_lifetime": applied_lifetime,
        "improved_lifetime": improved_lifetime,
        "data_gap": data_gap,
        "data_gap_detail": (f"no autonomous attempts in {window_days}d"
                            if data_gap else None),
        # improvement_rate (2026-08-01 metric-gap remediation, phase A1): the honest
        # headline. success_rate divides by `applied` — the subset of attempts that
        # happened to get a judged before/after — so the ~4-in-5 attempts that never
        # produced a measured bracket are silently excluded from BOTH numerator and
        # denominator, not counted as failures. That is how a 164-attempt, single-
        # digit-improvement window read as a 52% success rate. improvement_rate divides
        # by `attempted` (every engine run, incl. no_change/error/timeout) instead.
        "improvement_rate": round(100 * improved / attempted, 1) if attempted else None,
        # execution_success_rate (2026-08-14): improvement_rate alone conflates two
        # distinct failure classes into one flat number — "the engine never even
        # completed a run" (permission/tooling gate, worktree failure, timeout) vs
        # "runs complete cleanly but never move the metric" (wrong skill for the
        # metric, judge-window too short, genuinely non-remediable). completed/
        # attempted answers the first question in isolation; a reader who sees
        # improvement_rate=0 with execution_success_rate=0 should look at *why
        # nothing finishes* before touching skill selection, while
        # improvement_rate=0 with execution_success_rate=100 points the other way.
        "execution_success_rate": round(100 * completed / attempted, 1) if attempted else None,
        "proposed_count": proposed_count,
        "proposed_improved": proposed_improved,
        "proposal_improvement_rate": (
            round(100 * proposed_improved / proposed_count, 1)
            if proposed_count else None
        ),
        # success_rate: OBSERVATIONAL ONLY as of 2026-08-01 — no grading/summary consumer
        # may read this field (see aggregate.py::_self_correction_rate, which now sources
        # improvement_rate). Retained one release for external readers of this dict, then
        # delete; do not add a new consumer.
        "success_rate": round(100 * improved / applied, 1) if applied else None,
        "by_skill": by_skill,
        "events": sorted(events, key=lambda e: e["used_at"])[-20:],  # most recent
        "proposal_events": sorted(proposal_events, key=lambda e: e["used_at"])[-20:],
        "human_correlated": len(human_events),
        "human_correlated_improved": sum(
            1 for e in human_events if e["outcome"] == "improved"
        ),
        "human_events": sorted(human_events, key=lambda e: e["used_at"])[-20:],
        "note": ("fire-time before/after where recorded; else the engine's own `improved` "
                 "verdict on a SETTLED run (evidence=engine_verdict; done/no_change only — "
                 "error/timeout runs stay attempts and are never graded; a false verdict "
                 "grades flat, since a "
                 "boolean cannot distinguish a regression from a no-op, unless the late "
                 "re-judgment sidecar upgraded that entry_hash — those events carry "
                 "late_rejudge=true); else correlation "
                 "not causation "
                 "(flag -> skill used -> next snapshot moved healthy); headline fields "
                 "contain live autonomous ReflexEngine remediations only; human correlations, "
                 "read-only diagnoses, and proposal-only candidates are separate; attempted "
                 "counts autonomous no_change/error/timeout runs; improvement_rate = "
                 "improved/attempted (the honest headline) over window_days when set, with "
                 "the pre-windowing totals kept in the *_lifetime fields; success_rate = improved/applied "
                 "is observational-only, scheduled for deletion; proposal-only candidate "
                 "measurements are reported separately and never counted as live applications"),
    }


def main() -> int:
    r = efficacy()
    print(f"Remediation efficacy (lifetime): {r['attempted']} attempted · {r['completed']} completed · "
          f"{r['applied']} applied · {r['improved']} improved · "
          f"{r['regressed']} regressed · {r['flat']} flat · "
          f"improvement rate {r['improvement_rate']}% (observational success_rate {r['success_rate']}%)")
    print(f"  proposals: {r['proposed_improved']}/{r['proposed_count']} improved in validation")
    for s, b in sorted(r["by_skill"].items()):
        print(f"  {s}: {b['improved']}/{b['applied']} improved")
    for e in r["events"][-8:]:
        # Tier-2 rows have no numeric reading — say so instead of printing None->None.
        reading = ("engine verdict" if e.get("evidence") == "engine_verdict"
                   else f"{e['before']}->{e['after']}")
        print(f"  [{e['outcome']}] {e['metric']} {reading} via {e['command']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
