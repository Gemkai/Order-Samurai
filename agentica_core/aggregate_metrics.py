"""Telemetry metric readers for the aggregator REGISTRY: the r_* reducers over loaded records and the helpers they need (split from aggregate.py)."""
from __future__ import annotations

import json
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import harness_config
from .model_tiers import model_tier
from .telemetry import parse_ts
from .aggregate_records import _count_success_sessions, _dedup_field, _has_field, _int_vals, _nums, _pctile, _real_sid, _sum_per_session, _w_num


def r_count(recs): return len(recs) or None


# Error_Rate (path-to-10): telemetry.validate_entry only admits status in {"success","error"},
# so the error set is exhaustive and no "allowlist" of designed-termination statuses is needed.
# The real fix is the MIN-SAMPLE GUARD: a window with fewer than MIN_ERROR_SAMPLE sessions is
# uncalibrated (return None) rather than a false FAIL on noise — otherwise 1 error of 2 sessions
# reads 50.0 and trips fail=5. None is treated as uncalibrated by the health layer.
# MIN_ERROR_SAMPLE = 10 carries over unchanged from the record-counted version: the guard
# always meant "at least 10 independent samples", and records were never independent (the
# claude SessionEnd emitter re-emits one session as up to 28 rows) — sessions are.
ERROR_STATUSES = frozenset({"error"})


MIN_ERROR_SAMPLE = 10


def error_rate_stats(recs) -> tuple[float | None, int, int]:
    """Return (rate_pct | None, error_sessions, total_sessions). SESSION-counted: a
    session with ANY error-status record is one error session; records with no real
    session_id (absent or placeholder, see _real_sid) count individually. Record
    counting diluted the rate ~6x — a 28-row success re-emit outvoted error sessions
    28:1 in the denominator (weight-3 metric, understated; 2026-07-12 redesign).
    rate is None (uncalibrated) when total sessions < MIN_ERROR_SAMPLE, OR when the
    window carries no evidence the error channel is wired (REPLACE, 2026-07-08 audit):
    all 3,755 historical records said "success" (S2), so a 0.0 rate was a guaranteed
    weight-3 PASS that was structurally unfalsifiable. Evidence = an error-status
    record, or any record stamped with the emitter's exit_code field (record-level
    scan: one stamped re-emit is proof enough that the channel is wired).
    Canonical Error_Rate computation — shared with bin/error_triage.py
    (kept in lockstep by tests/test_error_triage.py::test_error_rate_classification_no_drift)."""
    sids: set = set()
    err_sids: set = set()
    loose_total = loose_errors = 0
    for r in recs:
        is_err = str(r.get("status", "")).lower() in ERROR_STATUSES
        sid = _real_sid(r)
        if sid:
            sids.add(sid)
            if is_err:
                err_sids.add(sid)
        else:
            loose_total += 1
            if is_err:
                loose_errors += 1
    total = len(sids) + loose_total
    errors = len(err_sids) + loose_errors
    if total < MIN_ERROR_SAMPLE:
        return None, errors, total
    if errors == 0 and not any("exit_code" in r for r in recs):
        return None, errors, total
    return round(100 * errors / total, 1), errors, total


def r_error_rate(recs): return error_rate_stats(recs)[0]


def r_lat(p):
    # latency_ms == 0 means "not instrumented" (the claude emitter writes a constant
    # 0), never a real sub-millisecond session. Zeros diluted the percentile floor
    # until Latency_P50 read 0.0 and calibration produced a 2.75ms warn (audit
    # S2/S5) — grade only real measurements; all-zero windows are SIMULATED.
    return lambda recs: _pctile([v for v in _nums(recs, "latency_ms") if v > 0], p)


def r_tool_volume(recs):
    # tool_calls is a CUMULATIVE per-session counter re-emitted on resume (same
    # class as total_cost) — raw summing was 5.8x inflated. Max per session +
    # loose records individually.
    by_sess, loose = _dedup_field(recs, "tool_calls")
    if not by_sess and not loose:
        return None
    return int(sum(by_sess.values()) + sum(loose))


def r_tool_diversity(recs):
    s = {t for r in recs for t in (r.get("tool_calls_list") or [])}
    return len(s) if s else None


def r_session_count(recs):
    # Distinct real sessions + placeholder-sid records counted individually
    # (each antigravity record is one task-run; the placeholder previously made
    # the whole platform read as ONE session).
    s = {sid for r in recs if (sid := _real_sid(r))}
    loose = sum(1 for r in recs if _real_sid(r) is None)
    total = len(s) + loose
    return total if total else None


def r_avg_session_turns(recs):
    # Prefer the emitter's `turns` field: user back-and-forth prompt turns parsed
    # from the transcript (agentica_emit.py), the semantic the manual warn=8/fail=15
    # thresholds were designed for. It is a cumulative per-session snapshot like
    # tool_calls/total_cost (a resumed session re-emits higher), so max per session;
    # zeros mean "not parsed", not a zero-turn session — grade only real measurements.
    by_sess, loose = _dedup_field(recs, "turns")
    vals = [v for v in list(by_sess.values()) + loose if v > 0]
    if vals:
        return round(sum(vals) / len(vals), 1)
    # Fallback PROXY for windows with no turns-stamped records (pre-2026-07-12
    # emitters): rows per real session. For claude this measures SessionEnd
    # RE-EMIT count, not turns — comparable over time, but not a true turn count.
    # Real-identity sessions only: averaging placeholder-sid records in would
    # treat a whole platform as one giant session.
    c = Counter(sid for r in recs if (sid := _real_sid(r)))
    return round(sum(c.values()) / len(c), 1) if c else None


def r_total_cost(recs):
    if not _has_field(recs, "total_cost"):
        return None
    by_sess, loose = _dedup_field(recs, "total_cost")  # per session, not per re-emit
    return round(sum(by_sess.values()) + sum(loose), 4)


def r_token_spend(recs):
    if not (_has_field(recs, "tokens_prompt") or _has_field(recs, "tokens_completion")):
        return None
    bp, lp = _dedup_field(recs, "tokens_prompt")
    bc, lc = _dedup_field(recs, "tokens_completion")
    return int(sum(bp.values()) + sum(lp) + sum(bc.values()) + sum(lc))


def _cpt_and_n(recs):
    """Canonical cost-per-task: dedup per session, positive costs only. A logged
    total_cost of 0.0 means cost was not attributed for that record (emitter task
    types like wid_pulse_gen / session), not a genuinely free task — including
    those zeros in the denominator systematically understates the metric. Dedup
    first so a session re-emitted N times isn't averaged in N times.
    Returns (cpt, n_cost_sessions); (None, 0) when no cost-bearing sessions.
    Every consumer that compares cost-per-task across windows MUST use this one
    definition — _estimated_cost_savings once recomputed it inline as raw-sum ÷
    all-records against this deduped baseline, inflating "savings" by ~$37 per
    zero-cost telemetry row (2026-07-26 audit)."""
    by_sess, loose = _dedup_field(recs, "total_cost")
    vals = [v for v in list(by_sess.values()) + loose if v > 0]
    if not vals:
        return None, 0
    return sum(vals) / len(vals), len(vals)


def r_cost_per_task(recs):
    cpt, _n = _cpt_and_n(recs)
    return round(cpt, 4) if cpt is not None else None


def _cost_per_outcome(records: list[dict], repo_root: Path | None = None) -> dict:
    """AUTO-002: $ spent per resolved outcome (Research/METRICS.md "Cost per Outcome"),
    not per raw task record (that's Cost_Per_Task's denominator). An "outcome" here is a
    real git commit — the one concrete, falsifiable signal that a task actually landed
    something, vs. exploration/research/aborted sessions that still cost tokens but
    resolved nothing. Numerator = deduped total_cost summed over the SAME record window
    the caller passed in (mirrors r_total_cost); denominator = distinct commits `git log`
    reports landed in that window's [earliest, latest] record timestamps. Both sides read
    real sources — no `outcome_ref` field is required (there is no live emitter for it:
    the SessionEnd hook lives outside this pillar's edit scope), so this stays truthful
    without waiting on a field this cycle can't wire."""
    from . import aggregate as _agg
    total_by_sess, total_loose = _dedup_field(records, "total_cost")
    total = sum(total_by_sess.values()) + sum(total_loose)
    stamps = sorted(
        t if t.tzinfo is not None else t.replace(tzinfo=timezone.utc)
        for t in (parse_ts(r.get("timestamp")) for r in records) if t is not None
    )
    if not stamps:
        return {"val": None, "error": "no timestamped records in window", "calibrated": False}
    root = repo_root or _agg._AGENTICA_REPO_ROOT
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "log",
             f"--since={stamps[0].isoformat()}", f"--until={stamps[-1].isoformat()}",
             "--format=%H"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        return {"val": None, "error": f"git unavailable: {e}", "calibrated": False}
    if out.returncode != 0:
        return {"val": None, "error": f"git log failed: {out.stderr.strip()[:200]}", "calibrated": False}
    outcomes = [line for line in out.stdout.splitlines() if line.strip()]
    if not outcomes:
        # No commit landed in this window — an honest zero-outcome week, not a data gap:
        # git itself is reachable and answered. total_cost still real; just uncalibrated
        # as a ratio (division by zero outcomes is undefined, not "free").
        return {"val": None, "data_gap": True, "calibrated": True}
    return {"val": round(total / len(outcomes), 4), "calibrated": True}


def r_token_density(recs):
    succ = _count_success_sessions(recs)  # distinct sessions, not per re-emit
    bp, lp = _dedup_field(recs, "tokens_prompt")
    bc, lc = _dedup_field(recs, "tokens_completion")
    tot = sum(bp.values()) + sum(lp) + sum(bc.values()) + sum(lc)
    return round(tot / succ, 1) if succ else None


def r_model_tier_mix(recs):
    return build_tier_mix(recs)["sword"]["slices"]


def _routing_tier_votes(recs) -> list[str]:
    """One model-tier vote per session, over records that carry a routing CHOICE.

    Two corrections, both measured against 30 days of live telemetry on 2026-08-17
    (4,989 records) — see the 2026-08-16 audit, P2 CONFIRMED:

    1. DEDUP. This reducer counted raw records while every other cumulative reducer
       here deduplicates by session (`_dedup_field`'s docstring: "~46% of windowed
       rows are such re-emits", one resumed session re-emitting up to 20 rows).
       Raw 30.6 -> session-deduped 34.8.

    2. NO-CHOICE ROWS. 1,699 records carried tier "unknown". Of those, 1,454 have no
       `model` at all — the emitter never observed one, so there was no routing
       decision to score — while 245 carry a real model (`claude-fable-5`) that the
       emitter's `_tier_for` simply cannot map (it looks for opus/sonnet/haiku).
       Only the FIRST group is excluded, mirroring r_mcp_vs_cli dropping "none".

       The second group is deliberately KEPT and counted as non-LOCAL, because that
       is what it is: a cloud call. Dropping all "unknown" rows — the obvious reading
       — erases 245 genuine cloud calls and reports 54.1 instead of 52.0. An
       unmappable model name must never flatter the local-routing number.
    """
    by_session: dict[str, str] = {}
    loose: list[str] = []
    for r in recs:
        tier = r.get("model_tier")
        if not tier:
            continue
        tier = str(tier).upper()
        has_model = bool(r.get("model")) and str(r.get("model")).strip().lower() not in ("", "none")
        if tier == "UNKNOWN" and not has_model:
            continue                      # no model observed => no routing choice to score
        sid = _real_sid(r)
        if sid:
            by_session.setdefault(sid, tier)   # first reading wins; re-emits repeat it
        else:
            loose.append(tier)            # no real session_id => cannot dedup, count once
    return list(by_session.values()) + loose


def r_local_routing(recs):
    """Percent of SESSIONS routed to the LOCAL model tier (Ollama). Higher = more work
    kept local/cheap/private per the local-LLM routing policy. Real efficiency signal,
    not a guess. Denominator is sessions that actually made a routing choice — see
    _routing_tier_votes for why unmappable model names stay in as non-LOCAL."""
    votes = _routing_tier_votes(recs)
    if not votes:
        return None
    return round(100 * sum(1 for t in votes if t == "LOCAL") / len(votes), 1)


def r_mcp_vs_cli(recs):
    """Percent of externally-connected sessions that used MCP (mcp_or_cli in
    {"mcp", "mixed"}). Lower = better per the Tool Connection Priority policy
    (pp-* CLI skill -> REST -> MCP last resort). Sessions with mcp_or_cli ==
    "none" made no external connection and carry no routing choice, so they are
    excluded from the denominator. None when no session in the window carries a
    routing value (tier honesty — registered 2026-07-12 from the frozen kernel's
    orphaned MCP_vs_CLI_Ratio, whose reducer counted only the literal "mcp",
    a value the canonical telemetry never emits)."""
    vals = [str(r.get("mcp_or_cli")).lower() for r in recs if r.get("mcp_or_cli")]
    routed = [v for v in vals if v in ("mcp", "mixed", "cli")]
    if not routed:
        return None
    return round(100 * sum(1 for v in routed if v in ("mcp", "mixed")) / len(routed), 1)


_MIX_FIELDS = ("tool_calls", "tokens_prompt", "tokens_completion", "output_words")


def _model_usage_parts(record):
    parts = record.get("_model_usage")
    return [part for part in parts if isinstance(part, dict)] if isinstance(parts, list) else []


def _tier_mix_records(recs):
    """Collapse cumulative session snapshots; retain unattributed activity, not empty hooks.

    A session that changes models uses its latest observed model. Session totals cannot
    tell us how much work each model did, so they must not be split across guessed shares.
    """
    sessions = {}
    loose = []
    excluded = 0
    for raw in recs:
        if raw.get("platform") == "git":
            continue
        r = dict(raw)
        r["model_tier"] = model_tier(r.get("model"), r.get("model_tier"))
        has_model = str(r.get("model") or "").strip().lower() not in {"", "none", "unknown"}
        if r["model_tier"] == "unknown" and not has_model and not any(
            _w_num(r.get(f)) > 0 for f in (*_MIX_FIELDS, "total_cost", "cache_read_tokens")
        ):
            excluded += 1
            continue
        sid = _real_sid(r)
        if not sid:
            loose.append(r)
            continue
        key = (r.get("platform"), sid)
        prev = sessions.get(key)
        if prev is not None:
            latest = r if str(r.get("timestamp", "")) >= str(prev.get("timestamp", "")) else prev
            merged = dict(latest)
            other = prev if latest is r else r
            if latest["model_tier"] == "unknown" and not latest.get("model"):
                merged["model_tier"] = other["model_tier"]
                merged["model"] = other.get("model")
            for field in _MIX_FIELDS:
                merged[field] = max(_w_num(r.get(field)), _w_num(prev.get(field)))
            r = merged
        sessions[key] = r
    return list(sessions.values()) + loose, excluded


def _tier_mix_weighted(recs, weight_fn=None):
    c: Counter = Counter()
    for r in recs:
        weighted = [(r, 1.0)]
        if weight_fn is not None:
            parts = _model_usage_parts(r)
            weighted = [(p, max(0, _w_num(weight_fn(p)))) for p in parts]
            if not any(weight for _, weight in weighted):
                weighted = [(r, max(0, _w_num(weight_fn(r))))]
        for item, weight in weighted:
            if weight:
                c[model_tier(item.get("model"), item.get("model_tier"))] += weight
    n = sum(c.values())
    if not n:
        return None
    return " ".join(f"{k}:{round(100 * v / n)}%" for k, v in c.most_common())


def build_tier_mix(recs) -> dict:
    records, excluded = _tier_mix_records(recs)
    models = defaultdict(set)
    for r in records:
        if r.get("model"):
            models[r["model_tier"]].add(str(r["model"]))
        parts = [part for part in _model_usage_parts(r) if part.get("model")]
        for part in parts:
            part_tier = model_tier(part.get("model"), part.get("model_tier"))
            models[part_tier].add(str(part["model"]))
            if r["model_tier"] == "MIXED":
                models["MIXED"].add(str(part["model"]))
    labels = {tier: " · ".join(sorted(names)) for tier, names in models.items()}
    weights = {
        "bow": ("Tool Calls", lambda r: r.get("tool_calls")),
        "sword": ("Task Volume", None),
        "brush": ("Token Spend", lambda r: _w_num(r.get("tokens_prompt")) + _w_num(r.get("tokens_completion"))),
        "arts": ("Output Words", lambda r: r.get("output_words")),
    }
    return {
        pillar: {"backing": backing, "slices": _tier_mix_weighted(records, weight),
                 "models": labels, "excluded_empty_records": excluded,
                 "attribution": ("One task per session; mixed when multiple models observed"
                                 if weight is None else "Exact per-model transcript usage")}
        for pillar, (backing, weight) in weights.items()
    }


def r_revision_ratio(recs):
    observed = any(r.get("mod_type") for r in recs)
    mods = [r.get("mod_type") for r in recs if r.get("mod_type") in ("SURGICAL", "CLOBBER")]
    if mods:
        return round(100 * sum(1 for m in mods if m == "CLOBBER") / len(mods), 1)
    return 0.0 if observed else None


def r_slop_density(recs):  # slop markers per 1k words of agent output
    # Both fields are per-session cumulative counters re-emitted on resume —
    # sum each session once, not per re-emit row.
    sw = _sum_per_session(recs, "slop_markers")
    ow = _sum_per_session(recs, "output_words")
    return round(sw / ow * 1000, 2) if ow else None


def r_sum_field(field):
    def f(recs):
        return _sum_per_session(recs, field) if _has_field(recs, field) else None
    return f


def r_simplify_age(recs):
    """Days since simplification last LANDED (recency of the quality gate). Lower
    is better. OUTCOME-based + runtime-agnostic: resets on the most recent of a
    /simplify skill invocation (Claude telemetry) OR a simplify/refactor/cleanup
    commit in git. The commit signal credits hand-rolled work AND the Antigravity
    runtime, which emit no Claude skill telemetry — previously this counted only
    /simplify runs, so cross-runtime or hand-rolled simplification was invisible
    and the age climbed while real work happened."""
    from . import aggregate as _agg
    stamps = [parse_ts(r.get("timestamp")) for r in recs if "simplify" in (r.get("skills_used") or [])]
    stamps.append(_agg._last_simplify_commit_ts())
    stamps = [t for t in stamps if t]
    if not stamps:
        return None
    latest = max(stamps)
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    return round((datetime.now(timezone.utc) - latest).total_seconds() / 86400, 1)


# Transcript-SAMPLED judge metrics get a min-sample guard: a WARN/FAIL asserted from a
# handful of judgments (the 2026-07-19 audit found Faithfulness graded FAIL on n=15)
# trains alarm blindness. Retrieval_Relevance is deliberately absent — it scores a FIXED
# curated seed-query benchmark, not a population sample, so small n is by construction.
_JUDGE_SAMPLED_METRICS = frozenset({
    "Tool_Selection_Accuracy", "Tool_Arg_Correctness", "Tool_Response_Utilization",
    "Faithfulness_Score", "Refusal_Appropriateness",
})


def _tool_quality(metric_key: str):
    """REGISTRY reducer for an Arts tool-use-quality metric. Reads the offline scout's output
    (state/tool_quality.json) — the LLM-judge work runs in tool_quality_scout.py, never here on
    the refresh hot path. Returns a 0-100 percent, or None (-> SIMULATED) when the scout hasn't
    run, the metric was a gap, or a transcript-sampled metric hasn't reached judge_min_n
    accepted judgments (same honesty pattern as Error_Rate's min-sample guard).
    Ignores `recs` (like _cache_hit_rate: source is a state file)."""
    def reducer(recs):  # noqa: ARG001
        from . import aggregate as _agg
        f = _agg._ORDER_SAMURAI_ROOT / "state" / "tool_quality.json"
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        m = (data.get("metrics") or {}).get(metric_key)
        if not isinstance(m, dict):
            return None
        score = m.get("score")
        if not isinstance(score, (int, float)) or isinstance(score, bool) or score < 0:
            return None  # gap -> SIMULATED, never a fabricated 0
        n = m.get("n")
        if (metric_key in _JUDGE_SAMPLED_METRICS
                and isinstance(n, int) and not isinstance(n, bool) and n < _agg._judge_min_n()):
            return None  # sample too thin to grade -> SIMULATED (pre-"n" payloads pass through)
        return round(score * 100, 1)
    return reducer


r_tool_selection = _tool_quality("Tool_Selection_Accuracy")


r_tool_args = _tool_quality("Tool_Arg_Correctness")


r_tool_util = _tool_quality("Tool_Response_Utilization")


r_faithfulness = _tool_quality("Faithfulness_Score")


r_refusal_appropriateness = _tool_quality("Refusal_Appropriateness")


r_retrieval_relevance = _tool_quality("Retrieval_Relevance")


_CLIFF_SCAN_FILES = 60


_CLIFF_THRESHOLD = 140_000  # absolute high-context cutoff (window-agnostic); surface fallback


def _cliff_threshold() -> int:
    """Declared cutoff from the editable surface, falling back to the literal above.

    Resolved once per run rather than per message: this is a file read, and the surface is
    writable by the self-harness cycle, so it must not be lru_cached into a stale value.
    """
    try:
        return int(harness_config.get_value("context_cliff_token_threshold"))
    except (OSError, ValueError, KeyError):
        return _CLIFF_THRESHOLD


def r_chain_depth_avg(recs):
    # The chain_depth field counts total Agent/Task calls per session (not nesting depth).
    # Large orchestration runs (ultracode) produce counts >1000 and skew the mean badly.
    # Median gives the typical session's orchestration load truthfully.
    vals = _int_vals(recs, "chain_depth")
    return _pctile(vals, 50) if vals else None


def r_complexity_weighted_throughput(records: list[dict]) -> float | None:
    # tool_calls / tokens_completion are CUMULATIVE per-session snapshots; each
    # re-emit row re-added the whole session's totals (6.1x inflation, 2026-07-12
    # audit). Score each real session once from its MAX (latest) snapshot; records
    # without a real session identity score individually.
    if not records:
        return None
    per_sess: dict[str, tuple[float, float]] = {}
    loose: list[tuple[float, float]] = []
    for r in records:
        if r.get("status") != "success":
            continue
        tool_calls = r.get("tool_calls", 0)
        if not isinstance(tool_calls, (int, float)) or isinstance(tool_calls, bool):
            tool_calls = 0
        tokens_comp = r.get("tokens_completion", 0)
        if not isinstance(tokens_comp, (int, float)) or isinstance(tokens_comp, bool):
            tokens_comp = 0
        sid = _real_sid(r)
        if sid:
            prev = per_sess.get(sid, (0.0, 0.0))
            per_sess[sid] = (max(prev[0], float(tool_calls)), max(prev[1], float(tokens_comp)))
        else:
            loose.append((float(tool_calls), float(tokens_comp)))
    if not per_sess and not loose:
        return 0.0
    total = sum(1.0 + tc * 0.5 + tk / 1000.0 for tc, tk in per_sess.values())
    total += sum(1.0 + tc * 0.5 + tk / 1000.0 for tc, tk in loose)
    return round(total, 1)


def _subagent_efficiency_index(records: list[dict]) -> float | None:
    """Median MARGINAL spawn cost vs the median solo-session cost.

    REPLACE-IN-PLACE (Wargame 01, Move 4): the prior version benchmarked the
    WHOLE-SESSION cost of a spawning session against solo cost — apples-to-oranges,
    since a session that spawns N subagents does more work than a solo session, so
    it flagged orchestration as waste even when every spawn was justified. The
    2026-07-08 subagent-audit grader found 98.6% of spawns justified; the sole
    brush F was this benchmark-design bug, not real waste. This version divides
    each spawning session's cost by its spawn count to get the MARGINAL cost of one
    spawn and compares that to the median solo-session cost:
        spawn_marginal = median(total_cost / max(spawns, 1) for spawning records)
        allowed        = median(solo costs)
        index          = 100 × min(1, allowed / spawn_marginal)
    None (no signal) when either side lacks positive-cost data. Advisory only
    (auto_remediable=False) — orchestration cost is a design tradeoff, not an
    auto-fixable defect."""
    from statistics import median
    # Per-session dedup: total_cost/subagent_spawns are CUMULATIVE per-session snapshots
    # written by the same SessionEnd emitter every other reducer in this file dedupes via
    # _dedup_field (a resumed session re-emits the SAME session_id with growing totals on
    # every resume — verified 6.1x/5.8x inflation, 2026-07-12 audit). Keep each session's
    # latest (max total_cost) snapshot so a session resumed N times contributes exactly one
    # sample to the median instead of N partial ones skewing it toward whichever session
    # happens to resume most often. total_cost/subagent_spawns are kept paired from the
    # SAME row (not maxed independently) since they are a single snapshot's cumulative pair.
    best_by_sid: dict[str, dict] = {}
    loose: list[dict] = []
    for r in records:
        c = r.get("total_cost")
        if not isinstance(c, (int, float)) or isinstance(c, bool) or c <= 0:
            continue  # zero cost = unattributed, not free (same rule as r_cost_per_task)
        sid = _real_sid(r)
        if sid:
            prev = best_by_sid.get(sid)
            if prev is None or c > prev.get("total_cost", 0):
                best_by_sid[sid] = r
        else:
            loose.append(r)
    spawn_marginals: list[float] = []
    solo_costs: list[float] = []
    for r in [*best_by_sid.values(), *loose]:
        c = float(r["total_cost"])
        n = r.get("subagent_spawns")
        spawns = n if isinstance(n, (int, float)) and not isinstance(n, bool) else 0
        if spawns > 0:
            spawn_marginals.append(c / max(spawns, 1))
        else:
            solo_costs.append(c)
    if not spawn_marginals or not solo_costs:
        return None
    spawn_marginal = median(spawn_marginals)
    if spawn_marginal <= 0:
        return None
    allowed = median(solo_costs)
    return round(min(100.0, 100.0 * allowed / spawn_marginal), 1)


def _cache_hit_rate(records: list[dict]) -> dict:
    """Reduce only attributable records; platform/window collection happens once upstream."""
    from . import aggregate as _agg
    return _agg.knowledge_metrics.provider_rate(records, dedup=True)


def r_skill_routing_adherence(recs):  # noqa: ARG001
    """Sword — % of critical-work detections routed through their governing skill
    (same session, at or after the detection; hiring-review nudges excluded).

    Reads the two skill-routing hook logs under ~/.claude/data (repo-independent),
    so `recs` is ignored. Mirrors _governance_pass_rate's inside-fn `execution.*`
    import: the live consumer (verify_live_sources) puts the Order Samurai repo
    root on sys.path. Returns the scalar percent, or None -> SIMULATED when no
    critical-work prompt has been detected yet (never a fabricated 0)."""
    try:
        from execution.skill_routing_adherence import compute_adherence
        return compute_adherence().get("val")
    except Exception:
        return None


def r_governance_work_volume(recs):  # noqa: ARG001
    """Sword — critical-work detections this window, routed or not (backlog P1).

    The VOLUME pair to Skill_Routing_Adherence's ratio: a busy hand-rolled or
    Antigravity session reads "high volume, low adherence" instead of vanishing
    into a ~0 adherence with no context. Same import pattern and same
    recs-ignored caveat as r_skill_routing_adherence (the hook log under
    ~/.claude/data is repo- and platform-independent). None -> SIMULATED until
    the router hook logs its first detection (never a fabricated 0)."""
    try:
        from execution.skill_routing_adherence import compute_work_volume
        return compute_work_volume().get("val")
    except Exception:
        return None
