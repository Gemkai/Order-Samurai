"""Ronin estimates and the coefficient calibration they consume: estimated agent and human time saved, estimated cost savings, craft improvements, and the locked calibrate step with its thresholds and minimum-sample policy (split from aggregate.py)."""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import remediation
from .atomic import atomic_json_write, file_write_lock
from .telemetry import iso_week
from .aggregate_records import _get_prior_week_val, _get_weekly_promotions_count, _parse_iso, _target_week_anchor
from .aggregate_metrics import _cpt_and_n
from .aggregate_signals import _doc_parity_latency_days, _vibe_alignment_score


def _calibrate_coefficients(backlog: list[dict], coef_path: Path):
    if not coef_path.exists():
        return
    # Whole load-mutate-write under one lock. Locking only the write would still let
    # two processes read the same bytes and clobber each other's per-kind updates —
    # the scheduled refresh_dashboard pass and a manual `python -m agentica_core.aggregate`
    # genuinely do overlap on this box. (2026-08-16 audit, P2.)
    with file_write_lock(coef_path):
        _calibrate_coefficients_locked(backlog, coef_path)


def _calibrate_coefficients_locked(backlog: list[dict], coef_path: Path):
    try:
        coef = json.loads(coef_path.read_text(encoding="utf-8"))
    except Exception:
        return

    # Group timed samples by kind; track the earliest measurement start so the
    # time-bounded fallback knows how long collection has been running.
    samples_by_kind = defaultdict(list)
    earliest_start = None
    for item in backlog:
        if item.get("status") == "done":
            start = _parse_iso(item.get("started_at"))
            comp = _parse_iso(item.get("completed_at"))
            if start and comp:
                duration = (comp - start).total_seconds() / 60
                if duration <= 0:
                    # date-only completed_at parses as midnight and yields a
                    # negative duration — a poisoned sample, never a real one
                    continue
                samples_by_kind[item.get("kind")].append(duration)
                if earliest_start is None or start < earliest_start:
                    earliest_start = start
                
    # Config-first: see _calibration_threshold. The write gate and the display gate
    # MUST read the same value or the dashboard shows a bar the calculation ignores.
    thresholds = _calibration_threshold(coef, coef_path)
    sample_threshold = thresholds.get("samples", _CALIBRATION_MIN_SAMPLES)
    week_threshold = thresholds.get("weeks")

    # Time-bounded fallback: once real samples (of ANY kind) have been collecting
    # for `weeks`, calibrate from whatever exists rather than waiting for the full
    # sample count. Never fabricates — a kind with zero samples stays on its seed
    # benchmark. This clock is block-wide (earliest sample across all kinds) —
    # "the group has been measuring long enough" — matching the existing tests.
    enough_time = (
        week_threshold is not None
        and earliest_start is not None
        and (datetime.now(timezone.utc) - earliest_start) >= timedelta(weeks=week_threshold)
    )

    # Calibrate operations coefficients — PER KIND (2026-07-15 fix). A kind's
    # benchmark_min is the average of ONLY that kind's real samples, so its
    # statistical reliability depends on ITS OWN sample count, not a total pooled
    # across unrelated kinds. The prior version compared a cross-kind SUM against
    # sample_threshold and then stamped calibrated_via="samples" on every
    # contributing kind — so a kind with a single real sample could be marked
    # "calibrated via samples" purely because a DIFFERENT kind supplied the rest of
    # the pool. _coef_block_calibrated's per-kind re-check (see its tests) already
    # correctly rejects that, but the writer kept producing the misleading flag —
    # e.g. 11 real samples split 4/3/4 across three kinds, all stamped
    # calibrated_via="samples", yet none individually cleared a 10-sample bar; the
    # metric read as permanently stuck between "should be calibrated" (detail text)
    # and "not calibrated" (actual flag). Now each kind must independently clear
    # sample_threshold, or fall back to the block-wide time gate.
    any_written = False
    for kind, values in samples_by_kind.items():
        block = coef.get("operations", {}).get(kind)
        if block is None:
            continue
        enough_samples_this_kind = len(values) >= sample_threshold
        if enough_samples_this_kind or enough_time:
            via = "samples" if enough_samples_this_kind else "time"
            avg = sum(values) / len(values)
            block["benchmark_min"] = avg
            block["calibrated"] = True
            block["sample_count"] = len(values)
            block["calibrated_via"] = via
            any_written = True
        elif block.get("calibrated") is True:
            # Reconcile stale calibration state left by a prior run under the old
            # pooled-total rule — don't leave a misleading calibrated:true sitting
            # on a kind that has never individually cleared the bar; benchmark_min
            # is left untouched (it may still be a real, useful average).
            block["calibrated"] = False
            block["sample_count"] = len(values)
            block.pop("calibrated_via", None)
            any_written = True

    if any_written:
        # atomic_json_write, not a hand-rolled `<name>.tmp`: a fixed temp name is a
        # SHARED mutable file, so two writers interleave into it and one publishes the
        # other's half-written bytes. atomic.py already documents that exact failure and
        # uses a per-PID temp — this module imports it and used it for the payload write
        # while this path hand-rolled the broken version. (2026-08-16 audit, P2.)
        try:
            atomic_json_write(coef_path, coef)
        except Exception as e:  # noqa: BLE001 — calibration must not fail the whole run
            # Previously `pass`: a corrupt or unwritable coefficients file left every
            # subsequent run silently uncalibrated with nothing reported anywhere.
            print(f"[aggregate] calibration write failed for {coef_path}: {e!r}",
                  file=sys.stderr)


# Calibration honesty gate: a coefficient block counts as calibrated only when EVERY
# entry has >= this many REAL samples. The stored `calibrated` flag is not trusted on
# its own — seeded coefficient files ship `calibrated: true` with `sample_count: 0`,
# which would present pure benchmark guesses (e.g. Arts "52 hrs saved this week") as
# measured truth. Gating on sample_count makes an un-sampled estimate report
# calibrated=False, so the hero falls back to its real measured metric until 20
# genuine samples exist. Same honesty principle as the <synthetic> telemetry purge.
_CALIBRATION_MIN_SAMPLES = 20  # fallback only; real bar is calibration_threshold.samples


class CalibrationPolicyError(RuntimeError):
    """A present governed calibration policy cannot be trusted."""


def _calibration_threshold(coef_data: dict, coef_path: Path | None = None) -> dict:
    """The governed calibration bar, resolved config-first.

    Order: config/calibration_policy.json, then a legacy `calibration_threshold`
    block inside calibration_coefficients.json, then {} (callers apply their own
    constant default).

    Config wins over state on purpose. The threshold decides when an estimate may
    call itself measured, which is policy — but it used to live inside the state
    file the reducers WRITE, and that file is gitignored, so on a fresh clone the
    bar silently reverted to the _CALIBRATION_MIN_SAMPLES constant. Letting state
    override config would also let a reducer quietly re-raise its own bar.

    The path is a sibling of the coefficients file's `state/` dir rather than a
    module constant, so a tmp_path fixture resolves to its own (absent) config and
    keeps honouring the threshold it wrote inline.
    """
    if coef_path is not None:
        policy = coef_path.parent.parent / "config" / "calibration_policy.json"
        try:
            raw = policy.read_text(encoding="utf-8")
        except FileNotFoundError:
            raw = None
        except OSError as exc:
            raise CalibrationPolicyError(f"calibration policy unreadable: {exc}") from exc
        if raw is not None:
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError) as exc:
                raise CalibrationPolicyError(f"calibration policy is invalid JSON: {exc}") from exc
            if not isinstance(payload, dict):
                raise CalibrationPolicyError("calibration policy root must be an object")
            block = payload.get("calibration_threshold")
            if not isinstance(block, dict):
                raise CalibrationPolicyError(
                    "calibration policy missing object `calibration_threshold`"
                )
            for key in ("samples", "weeks"):
                value = block.get(key)
                if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                    raise CalibrationPolicyError(
                        f"calibration policy `{key}` must be a positive integer"
                    )
            return block
    block = coef_data.get("calibration_threshold")
    return block if isinstance(block, dict) else {}


def _calibration_min_samples(coef_data: dict, coef_path: Path | None = None) -> int:
    """Display-gate bar = the SAME calibration_threshold.samples the write gate
    (_calibrate_coefficients) uses, so the dashboard never hides a threshold that
    disagrees with the calculation. Falls back to the constant only when neither
    the config policy nor a legacy state block supplies one."""
    threshold = _calibration_threshold(coef_data, coef_path)
    try:
        return int(threshold.get("samples", _CALIBRATION_MIN_SAMPLES))
    except (TypeError, ValueError):
        return _CALIBRATION_MIN_SAMPLES


def _coef_block_calibrated(block: dict, min_samples: int = _CALIBRATION_MIN_SAMPLES) -> bool:
    if not block:
        return False
    return all(
        isinstance(v, dict)
        and v.get("calibrated") is True
        and (v.get("sample_count", 0) >= min_samples or v.get("calibrated_via") == "time")
        for v in block.values()
    )


def _estimated_agent_time_saved(records: list[dict], repo_root: Path | None = None) -> dict:
    """Est. Agent Hours Saved — this week's completed backlog items priced at their
    per-kind benchmark minutes.

    2026-08-08 class sweep: `records` is no longer ignored. It is not a data source here
    (the values come from MEDITATION_STATE.json) but it IS what tells the reducer WHICH
    week it is being asked about — see _target_week_anchor. Without it, backfill_history's
    per-week replay stamped the current week's hours onto every historical row."""
    from . import aggregate as _agg
    if repo_root is None:
        repo_root = _agg._ORDER_SAMURAI_ROOT
    state_file = repo_root / "state" / "MEDITATION_STATE.json"
    coef_path = repo_root / "state" / "calibration_coefficients.json"
    
    if not state_file.exists() or not coef_path.exists():
        return {"val": 0.0, "week_delta": 0.0, "calibrated": False}
        
    try:
        state = json.loads(state_file.read_text(encoding="utf-8", errors="ignore"))
        backlog = state.get("backlog", [])
        
        # Trigger calibration check
        _calibrate_coefficients(backlog, coef_path)
        
        coef_data = json.loads(coef_path.read_text(encoding="utf-8", errors="ignore"))
        ops_coef = coef_data.get("operations", {})

        now = _target_week_anchor(records)
        this_week = now.strftime("%G-W%V")
        last_week = (now - timedelta(days=7)).strftime("%G-W%V")

        def week_done_items(week_str: str) -> list[dict]:
            out = []
            for item in backlog:
                if item.get("status") == "done":
                    comp_dt = _parse_iso(item.get("completed_at"))
                    if comp_dt and comp_dt.strftime("%G-W%V") == week_str:
                        out.append(item)
            return out

        def calculate_week_hours(week_str: str) -> float:
            total_min = 0.0
            for item in week_done_items(week_str):
                kind = item.get("kind", "skill")
                benchmark_min = ops_coef.get(kind, {}).get("benchmark_min", 30.0)
                total_min += benchmark_min
            return total_min / 60.0

        # Calibration is judged ONLY on the kinds that actually contribute to this
        # week's value — a week of stream/scout work must not wait on a `skill`
        # benchmark that no current work-unit ever samples (the all-kinds bar made
        # Bow structurally un-calibratable). With no contributing items the value is
        # a real 0 but nothing is measured, so it stays uncalibrated and the hero
        # falls back to the measured Complexity-Weighted Throughput.
        min_samples = _calibration_min_samples(coef_data, coef_path)
        week_kinds = {item.get("kind", "skill") for item in week_done_items(this_week)}
        calibrated = bool(week_kinds) and all(
            _coef_block_calibrated({k: ops_coef.get(k, {})}, min_samples) for k in week_kinds
        )
            
        val = calculate_week_hours(this_week)
        last_val = calculate_week_hours(last_week)
        week_delta = val - last_val
        out = {"val": round(val, 1), "week_delta": round(week_delta, 1), "calibrated": calibrated}
        if not calibrated:
            # Honest progress receipt (2026-07-11, corrected 2026-07-15): calibration
            # is gated PER KIND (see _calibrate_coefficients) — a cross-kind pooled
            # total reads as "cleared" while the kinds that actually matter this week
            # are each still under-sampled, which is exactly what misled this metric
            # before the fix (11 real samples pooled across 3 kinds, 4/3/4 each,
            # displayed as "11/10" while `calibrated` stayed false). Report each of
            # THIS WEEK's contributing kinds against the bar it must individually
            # clear, not an irrelevant cross-kind sum.
            per_kind = ", ".join(
                f"{k} {ops_coef.get(k, {}).get('sample_count', 0)}/{min_samples}"
                for k in sorted(week_kinds)
            ) if week_kinds else "no timed work this week"
            out["detail"] = f"estimate — {per_kind} real timed samples"
        return out
    except Exception as e:
        return {"val": None, "error": f"source unavailable: {str(e)}", "calibrated": False}


def _estimated_cost_savings(records: list[dict], repo_root: Path | None = None) -> dict:
    """Estimated_Cost_Savings — cost-per-task efficiency gain vs the prior week, priced
    at the target week's cost-bearing task volume.

    2026-08-08 (metric-honesty class sweep, goal data-honesty-cost-savings-v2). Three
    defects, all instances of the class Self_Correction_Rate was fixed for:

    (ii) WALL-CLOCK-IN-REDUCER — the target week came from datetime.now(), not from the
    record set, so a replayed historical week matched no records and wrote a structural
    0.0 into every rebuilt history row. Now anchored via _target_week_anchor() (see
    there for the mechanism and its AGENTICA_METRIC_WEEK_FROM_RECORDS kill switch);
    live refreshes are unaffected, a replay of week N prices week N.

    (iii) STRUCTURALLY-SIGNED — `if this_cpt < prior_cpt` floored the result at 0.0, so
    a week where cost-per-task got WORSE was indistinguishable from a week where it held
    flat: the bad direction had no representation at all. The floor is removed rather
    than the metric renamed, because renaming the registry key would have to ripple
    through insights/state_report/ronin_metrics/the dashboard — all outside this change's
    scope — whereas an unfloored signed value is what the consumers already treat it as
    (insights' brush lead branches on zero-vs-nonzero, and a negative reads correctly as
    a regression). A negative value now means cost-per-task rose.

    (iv) NO-DATA-LOOKS-HEALTHY (window variants) — under a window shorter than one ISO
    week (refresh_dashboard's 1d variant) the record set structurally cannot contain a
    whole week, so the number was a partial-week figure presented with the same
    confidence as the 30d one. Such a build now returns a data gap with an explicit
    reason instead of a truncated dollar figure.

    Kill switches (both default to the fixed behaviour):
      AGENTICA_COST_SAVINGS_V2=false|0|no        — restores the positive-only floor and
                                                   drops the short-window suppression.
      AGENTICA_METRIC_WEEK_FROM_RECORDS=false|.. — restores the wall-clock target week
                                                   (shared with the other weekly reducers).
    """
    from . import aggregate as _agg
    if repo_root is None:
        repo_root = _agg._ORDER_SAMURAI_ROOT

    # Resolve history path relative to repo_root
    history_path = repo_root.parent.parent / "Data" / "telemetry" / "metrics_history.jsonl"

    try:
        # Shared house idiom (remediation._kill_switch_off): env var defaults to the
        # FIXED behaviour; an operator can only opt BACK to the old semantics.
        v2 = not remediation._kill_switch_off("AGENTICA_COST_SAVINGS_V2")

        anchor = _target_week_anchor(records)
        this_week = anchor.strftime("%G-W%V")
        last_week = (anchor - timedelta(days=7)).strftime("%G-W%V")

        # A build whose telemetry window is shorter than an ISO week cannot hold one, so
        # there is no weekly figure to report — suppressed outright rather than shown as
        # a truncated partial-week dollar amount. Read-only use of the window aggregate()
        # published (see _ACTIVE_WINDOW_DAYS); this changes no other metric's windowing.
        if v2 and _agg._ACTIVE_WINDOW_DAYS < 7:
            return {
                "val": None, "week_delta": 0.0, "calibrated": False,
                "estimate_by_design": True, "data_gap": True,
                "detail": (f"suppressed — a {_agg._ACTIVE_WINDOW_DAYS}d telemetry window cannot "
                           f"cover ISO week {this_week}; this is a weekly figure"),
            }

        # Component 1: cost-per-task improvement x this week's cost-bearing volume.
        # A raw spend drop vs last week is NOT savings — it also falls when less
        # work happens. Efficiency gain per task at this week's volume is.
        # BOTH sides of the comparison use _cpt_and_n (the r_cost_per_task
        # definition the history baseline was recorded under), and the multiplier
        # is the deduped cost-bearing session count — NOT len(wk_recs). The old
        # inline raw-sum ÷ all-records version compared incompatible definitions
        # and grew ~$37 per zero-cost telemetry row (reproduced 2026-07-26:
        # claimed $22,878 "saved" in a week that spent $3,742).
        wk_recs = [r for r in records if iso_week(r.get("timestamp", "")) == this_week]
        this_cpt, n_tasks = _cpt_and_n(wk_recs)
        prior_cpt = _get_prior_week_val(history_path, "brush/Token Efficiency/Cost_Per_Task",
                                        before_week=this_week)

        comp1_savings = 0.0
        comp1_present = this_cpt is not None and prior_cpt is not None
        if comp1_present and (v2 or this_cpt < prior_cpt):
            # SIGNED by design under v2 (default). The old guard also required
            # `this_cpt < prior_cpt`, so the number could only ever be a gain or
            # zero: a week that got MORE expensive per task reported the same $0
            # as a week that held flat, and the hero could never show a
            # regression that had actually happened. A figure that cannot go
            # down is not a measurement. Negative now means a real
            # cost-per-task regression. Unfloored under v2: a rise in
            # cost-per-task yields a NEGATIVE saving.
            comp1_savings = (prior_cpt - this_cpt) * n_tasks

        # This metric is now the REAL cost-per-task saving only. The former
        # component 2 (efficient_runs x $0.05) was an estimate coefficient with no
        # per-event $ sample, so it could never calibrate and only dragged the whole
        # metric's calibrated flag to permanent False. Routing efficiency is still
        # worth surfacing — but as a real COUNT of efficient routings, not fabricated
        # dollars (hero-metrics plan step 4 / Estimated_Cost_Savings rename).
        val = comp1_savings

        # Honest week-over-week delta: last week's cost-per-task saving computed the
        # same way (prior-prior-week CPT vs last-week CPT x last week's task volume).
        last_wk_recs = [r for r in records if iso_week(r.get("timestamp", "")) == last_week]
        last_cpt, last_n_tasks = _cpt_and_n(last_wk_recs)
        last_comp1_savings = 0.0
        if last_cpt is not None:
            prior_prior_cpt = _get_prior_week_val(history_path, "brush/Token Efficiency/Cost_Per_Task",
                                                  before_week=last_week)
            # Same unfloored rule as the current week, so week_delta compares like
            # with like instead of a signed value against a clamped one.
            if prior_prior_cpt is not None and (v2 or last_cpt < prior_prior_cpt):
                last_comp1_savings = (prior_prior_cpt - last_cpt) * last_n_tasks
        week_delta = val - last_comp1_savings

        # Data gap = no real cost-per-task baseline to measure savings from (no
        # prior-week CPT in history, or no cost-bearing sessions this week). When
        # true the hero falls back to the measured Cost-per-Task rather than show
        # a confident $0 saving.
        data_gap = not comp1_present
        detail = (
            f"no prior-week cost-per-task baseline before {this_week}" if not comp1_present
            else (f"week {this_week}: cost-per-task {prior_cpt:.4g} → {this_cpt:.4g} "
                  f"across {n_tasks} cost-bearing session(s)")
        )

        # calibrated stays False by design: even with matched definitions this is
        # a modeled counterfactual ("last week's unit price held at this week's
        # volume"), not measured cash. It reports as an estimate until a
        # validation exists — a reducer must not self-certify as "measured" just
        # because both inputs happen to be present (2026-07-26 audit).
        calibrated = False
        return {
            "val": round(val, 2),
            "week_delta": round(week_delta, 2),
            "calibrated": calibrated,
            # calibrated=False above says "not measured"; this says "and it never will be".
            # Without the distinction the UI rendered "EST · UNCALIBRATED — not yet calibrated
            # against measured samples" on a metric that has no samples to await, and
            # resolveHero read that as untrustworthy — so the brush hero fell back to
            # Cost_Per_Task every week. Unlike the arts hero (which sets calibrated=True),
            # this one keeps False and lets the flag carry the honesty.
            "estimate_by_design": True,
            "data_gap": data_gap,
            "detail": detail,
        }
    except Exception as e:
        return {"val": None, "error": f"source unavailable: {str(e)}", "calibrated": False}


def _craft_improvements(records: list[dict], repo_root: Path | None = None) -> dict:
    """Real, measured craft wins this week — NOT a synthetic hours estimate.

    The former Estimated_Human_Time_Saved multiplied real signals (vibe Δ, doc-parity
    Δ, promotions, arts effort) by hours-per-unit coefficients that had no per-event
    sample source, so they could never calibrate — the "awaiting calibration" badge
    was permanent and the dollar/hours figure was fabricated. This drops the invented
    hours and surfaces the underlying real improvements directly:
      • skill promotions this week (real count)
      • completed arts backlog items this week (real count)
    The headline value is the count of those discrete craft deliverables; the vibe
    and doc-parity deltas (real but continuous, not counts) ride in `detail` and are
    each tracked as their own metrics on the Arts pillar. Everything here is measured,
    so calibrated is True by design — there is no coefficient left to calibrate.

    2026-08-08 class sweep: the target week comes from `records` (see
    _target_week_anchor) so a backfill replay of week N counts week N's deliverables
    instead of re-stamping the current week's onto every historical row.
    """
    from . import aggregate as _agg
    if repo_root is None:
        repo_root = _agg._ORDER_SAMURAI_ROOT
    state_file = repo_root / "state" / "MEDITATION_STATE.json"

    history_path = repo_root.parent.parent / "Data" / "telemetry" / "metrics_history.jsonl"

    try:
        now = _target_week_anchor(records)
        this_week = now.strftime("%G-W%V")
        last_week = (now - timedelta(days=7)).strftime("%G-W%V")

        backlog = []
        if state_file.exists():
            backlog = json.loads(state_file.read_text(encoding="utf-8", errors="ignore")).get("backlog", [])

        def arts_items_done(week_str: str) -> int:
            n = 0
            for item in backlog:
                if item.get("status") == "done" and item.get("pillar") == "arts":
                    comp_dt = _parse_iso(item.get("completed_at"))
                    if comp_dt and comp_dt.strftime("%G-W%V") == week_str:
                        n += 1
            return n

        this_promos = _get_weekly_promotions_count(now)
        last_promos = _get_weekly_promotions_count(now - timedelta(days=7))
        this_arts = arts_items_done(this_week)
        last_arts = arts_items_done(last_week)

        # Headline = discrete craft deliverables (real event counts).
        val = this_promos + this_arts
        week_delta = val - (last_promos + last_arts)

        # Quality deltas — real, but continuous magnitudes, so they ride in the
        # breakdown rather than the headline count. A signed delta is only honest
        # when a real prior-week baseline exists; with no baseline we show the
        # current level instead of pretending the whole value is a week's gain.
        # (Positive doc delta = parity gap shrank — latency days fell.)
        vibe_now = _vibe_alignment_score(records, repo_root)
        prior_vibe = _get_prior_week_val(history_path, "arts/Output Quality/Vibe_Alignment",
                                         before_week=this_week)
        # A gap (absent/null/unreadable source) renders "no data", never a
        # fabricated level or delta — vibe_now=None must not reach the :g
        # format spec or a None-minus-number subtraction.
        if vibe_now is None:
            vibe_str = "Vibe no data"
        elif prior_vibe is None:
            vibe_str = f"Vibe {vibe_now:g}"
        else:
            vibe_str = f"Vibe {round(vibe_now - prior_vibe, 1):+g}"

        doc_now = _doc_parity_latency_days(records, repo_root)
        prior_doc = _get_prior_week_val(history_path, "arts/Docs/Documentation_Parity_Latency",
                                        before_week=this_week)
        doc_str = (f"Doc-parity {doc_now:g}d" if prior_doc is None
                   else f"Doc-parity {round(prior_doc - doc_now, 1):+g}d")

        detail = (f"{vibe_str} · {doc_str} · "
                  f"{this_promos} promo{'' if this_promos == 1 else 's'} · "
                  f"{this_arts} arts item{'' if this_arts == 1 else 's'}")

        out = {
            "val": val,
            "week_delta": week_delta,
            "calibrated": True,
            "detail": detail,
        }
        # RETUNE 2026-07-08 audit: with skill_promotion_log.jsonl absent (S3 dead
        # source) the promotions numerator reads 0 — a dead numerator must not be
        # presented as a confident measured zero.
        if not (Path.home() / ".claude" / "data" / "skill_promotion_log.jsonl").exists():
            out["data_gap"] = True
        return out
    except Exception as e:
        return {"val": None, "error": f"source unavailable: {str(e)}", "calibrated": False}


def _estimated_human_time_saved(records: list[dict], repo_root: Path | None = None) -> dict:
    """Est. Human Hours Saved — an ESTIMATE BY DESIGN (restored per user decision
    2026-07-08 as the Arts hero, with Craft_Improvements as the measured fallback).

    Real event counts × seed hour-coefficients from calibration_coefficients.json
    ("craft" block). Unlike Estimated_Agent_Time_Saved there is NO per-event sample
    source for how long the HUMAN would have taken, so the coefficients are
    unvalidatable and this number can never earn a from-samples calibration badge.
    The envelope therefore carries estimate_by_design=True and the UI labels the
    value "est." — never "awaiting calibration" (it is not waiting for anything).
    Only positive improvements convert to hours; a regression never produces
    negative "hours saved" — a deliberate asymmetry, kept in the 2026-08-08 class sweep
    because the coefficients only model time an improvement AVOIDS, and there is no
    symmetric coefficient for the hours a regression costs. What that sweep did change:
    the target week now comes from `records` via _target_week_anchor, so a backfill
    replay of week N reports week N instead of the current week."""
    from . import aggregate as _agg
    if repo_root is None:
        repo_root = _agg._ORDER_SAMURAI_ROOT
    state_file = repo_root / "state" / "MEDITATION_STATE.json"
    coef_path = repo_root / "state" / "calibration_coefficients.json"
    history_path = repo_root.parent.parent / "Data" / "telemetry" / "metrics_history.jsonl"
    if not coef_path.exists():
        return {"val": None, "error": "calibration_coefficients.json missing", "calibrated": False}
    try:
        craft = json.loads(coef_path.read_text(encoding="utf-8", errors="ignore")).get("craft", {})
        c_promo = float(craft.get("skill_promotion_hrs_per_promotion", {}).get("benchmark", 0))
        c_arts = float(craft.get("arts_backlog_hrs_per_effort_point", {}).get("benchmark", 0))
        c_vibe = float(craft.get("vibe_alignment_hrs_per_point", {}).get("benchmark", 0))
        c_doc = float(craft.get("doc_parity_latency_hrs_per_day", {}).get("benchmark", 0))

        now = _target_week_anchor(records)
        this_week = now.strftime("%G-W%V")
        last_week = (now - timedelta(days=7)).strftime("%G-W%V")

        backlog = []
        if state_file.exists():
            backlog = json.loads(state_file.read_text(encoding="utf-8", errors="ignore")).get("backlog", [])

        def arts_effort_done(week_str: str) -> float:
            total = 0.0
            for item in backlog:
                if item.get("status") == "done" and item.get("pillar") == "arts":
                    comp_dt = _parse_iso(item.get("completed_at"))
                    if comp_dt and comp_dt.strftime("%G-W%V") == week_str:
                        # default a missing/null effort to 1 point, but honor an
                        # explicit 0 — `effort or 1` would coerce a real 0 to 1
                        # and inflate the hero metric with a phantom point.
                        eff = item.get("effort")
                        total += float(eff if eff is not None else 1)
            return total

        this_promos = _get_weekly_promotions_count(now)
        last_promos = _get_weekly_promotions_count(now - timedelta(days=7))

        # continuous quality deltas — only a real prior-week baseline yields hours,
        # and only improvement counts (never negative savings)
        vibe_now = _vibe_alignment_score(records, repo_root)
        prior_vibe = _get_prior_week_val(history_path, "arts/Output Quality/Vibe_Alignment",
                                         before_week=this_week)
        # A gap contributes no hours (never a negative or fabricated gain) — the
        # subtraction below is only valid once both sides are real measurements.
        vibe_gain = (max(vibe_now - prior_vibe, 0.0)
                     if vibe_now is not None and prior_vibe is not None else 0.0)
        doc_now = _doc_parity_latency_days(records, repo_root)
        prior_doc = _get_prior_week_val(history_path, "arts/Docs/Documentation_Parity_Latency",
                                        before_week=this_week)
        doc_gain = max(prior_doc - doc_now, 0.0) if prior_doc is not None else 0.0

        promo_hrs = this_promos * c_promo
        arts_hrs = arts_effort_done(this_week) * c_arts
        vibe_hrs = vibe_gain * c_vibe
        doc_hrs = doc_gain * c_doc
        val = round(promo_hrs + arts_hrs + vibe_hrs + doc_hrs, 1)
        # week delta compares only the event-count components (quality deltas are
        # already week-over-week gains and have no meaningful second difference)
        last_events = last_promos * c_promo + arts_effort_done(last_week) * c_arts
        week_delta = round((promo_hrs + arts_hrs) - last_events, 1)

        detail = (f"{promo_hrs:g}h promos · {arts_hrs:g}h arts · "
                  f"{vibe_hrs:g}h vibe · {doc_hrs:g}h docs · seed coefficients")
        return {
            "val": val,
            "week_delta": week_delta,
            # not from samples and never will be — the flag below carries the honesty;
            # calibrated stays True so the hero slot doesn't fall back as "awaiting"
            "calibrated": True,
            "estimate_by_design": True,
            "detail": detail,
        }
    except Exception as e:
        return {"val": None, "error": f"source unavailable: {str(e)}", "calibrated": False}
