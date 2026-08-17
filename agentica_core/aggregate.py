"""The unified aggregator — the "senses" half of Governance. Reads canonical telemetry from
BOTH platforms via the adapter, computes the LIVE metrics from the METRICS.md registry, and
emits one cross-platform WIDPayload. Metrics whose source isn't wired yet are emitted as
SIMULATED (tier honesty) — never faked as live. Supersedes Jarvis's aggregator (HARVEST §7).

Registry-driven: each metric is an entry with a reducer; add a metric = an entry + a reducer fn.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Any

import jsonschema

_ORDER_SAMURAI_ROOT = Path(os.environ.get(
    "ORDER_SAMURAI_ROOT", str(Path(__file__).resolve().parents[1] / "Order Samurai")))
# The repo root containing .git (Governance/agentica_core -> Governance -> repo root).
# Used by _cost_per_outcome (AUTO-002) to read real `git log` outcomes.
_AGENTICA_REPO_ROOT = Path(os.environ.get(
    "AGENTICA_REPO_ROOT", str(Path(__file__).resolve().parents[2])))
_VAULT_HEALTH_SCRIPT = Path(__file__).resolve().parents[2] / "Knowledge" / "vault" / "_scripts" / "vault_health.py"

# Additional roots where prompt_injection_guard.py may have written kill-chain events
# when Claude sessions ran from a different cwd than Order Samurai.
# Scope this to the Governance tree (NOT the whole Agentica-OS repo root): every cwd a session
# runs from — Governance/, api/, dashboard-ui/, Order Samurai/ — lives under it, while the repo
# root also holds sub-bundles/ (vendored submodule repos) and other heavy trees totalling ~10k
# dirs that never contain kill-chain events. Walking the full repo re-scanned 10k+ dirs on EVERY
# metric call (per platform x per project x 3 reducers) and effectively hung aggregate() under
# load. Governance/ is ~130 dirs and walks in <0.1s.
_KILL_CHAIN_EXTRA_ROOTS: list[Path] = [
    Path(__file__).resolve().parents[1],  # Governance/ (covers all session cwds)
]

from . import (harness_config, insights, reflexes, remediation, remediation_delta, scouts,
               threshold_audit, verify_secrets)
from .atomic import atomic_json_write, file_write_lock
from .adapter import PlatformUnavailable, list_platforms, resolve_platform
from .telemetry import (SCHEMA_VERSION, default_events_path, iso_week, normalize_entry,
                        parse_ts, validate_entry, validate_metric)
from .verifiers import load_verifiers, run_all

_THIS = Path(__file__).resolve()
PILLARS = ("bow", "sword", "brush", "arts")
_SCORECARD_DIR = Path(__file__).resolve().parent.parent / "config"
_SCORECARDS = {
    "default": _SCORECARD_DIR / "architecture_scorecard.json" if (_SCORECARD_DIR / "architecture_scorecard.json").exists() else _SCORECARD_DIR / "claude_architecture_scorecard.json",
    "claude": _SCORECARD_DIR / "claude_architecture_scorecard.json",
    "codex": _SCORECARD_DIR / "codex_architecture_scorecard.json" if (_SCORECARD_DIR / "codex_architecture_scorecard.json").exists() else _SCORECARD_DIR / "architecture_scorecard.json",
    "gemini": _SCORECARD_DIR / "gemini_architecture_scorecard.json" if (_SCORECARD_DIR / "gemini_architecture_scorecard.json").exists() else _SCORECARD_DIR / "architecture_scorecard.json",
}


def architecture_breakdown(scorecard_path: Path | None) -> dict | None:
    """Per-category architecture decomposition for the demoted-score view (plan Phase 5).

    Reads the rich scorecard *output* (earned/status/blocking/advisory-gap per category) that
    execution/score_architecture.py emits — resolved from the scorecard config's
    reporting.emitJsonTo, relative to the repo root (config's parent.parent). Returns only the
    fields the dashboard renders; None when either file is missing/unreadable so the panel can
    degrade to "no data" rather than a false zero. This is presentation-only: the headline
    Architecture_Scorecard_Grade metric (and its history) is computed independently and untouched.
    """
    if not scorecard_path:
        return None
    try:
        cfg = json.loads(scorecard_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    rel = (cfg.get("reporting") or {}).get("emitJsonTo") or "artifacts/architecture_score.json"
    artifact = scorecard_path.parent.parent / rel
    try:
        art = json.loads(artifact.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    cats = [
        {
            "id": c.get("id"), "label": c.get("label"), "weight": c.get("weight", 0),
            "earned": c.get("earned", 0), "status": c.get("status", "unknown"),
            "missing_verifiers": c.get("missing_verifiers", []),
            "warnings": c.get("warnings", []),
        }
        for c in (art.get("categories") or [])
    ]
    return {
        "score": art.get("score"),
        "target_score": art.get("target_score"),
        "merge_floor": art.get("merge_floor"),
        "release_floor": art.get("release_floor"),
        "meets_merge_floor": art.get("meets_merge_floor"),
        "meets_release_floor": art.get("meets_release_floor"),
        "enforcement_mode": art.get("enforcement_mode"),
        "blocking_categories": art.get("blocking_categories", []),
        "advisory_gaps": art.get("advisory_gaps", []),
        "categories": cats,
        "generated_at": art.get("generated_at"),
    }


# Session-level project overrides — applied at load time, non-destructive.
# Use when a session was run from the wrong cwd (e.g. Codex opened from JIH
# but actually working on a different project). Keyed by full session_id.
_SESSION_PROJECT_OVERRIDES: dict[str, str] = {
    # Codex session "Analyze Dendrite app codebase" (2026-06-02) — cwd was
    # Jarvis-Intelligence-Hub but the session was entirely about Dendrite app.
    "019e8603-ae73-7873-89e7-8ca90b3b0ae1": "Dendrite app",
}


# ---------------------------------------------------------------- loading
def load_records(platform: str) -> list[dict]:
    try:
        src = resolve_platform(platform).telemetry_source
    except PlatformUnavailable:
        return []
    if not src.exists():
        return []
    out: list[dict] = []
    for line in src.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = normalize_entry(json.loads(line), platform=platform)
            # Reject fabricated records: model "<synthetic>" is a placeholder written
            # by an ad-hoc transcript backfill (not the real SessionEnd emitter, which
            # uses a genuine model id or None). These carried estimated costs and were
            # duplicated up to 13x, inflating Total_Cost ~34%. The emitter contract is
            # "NEVER fabricates" — enforce it at the read funnel so any future reappearance
            # of the backfill can never re-pollute the metrics.
            if rec.get("model") == "<synthetic>":
                continue
            # Correct mis-attributed sessions (cwd ≠ actual project worked on)
            sid = rec.get("session_id", "")
            if sid in _SESSION_PROJECT_OVERRIDES:
                rec["project"] = _SESSION_PROJECT_OVERRIDES[sid]
            validate_entry(rec)
            out.append(rec)
        except Exception:
            continue  # skip malformed / legacy-incompatible lines
    return out


# ---------------------------------------------------------------- reducers (records -> value|None)
def _nums(records: list[dict], field: str) -> list[float]:
    return [r[field] for r in records
            if isinstance(r.get(field), (int, float)) and not isinstance(r.get(field), bool)]


# session_id values that are PLACEHOLDERS, not identities. The antigravity
# emitter stamps every record "local-session", and telemetry.normalize_entry
# defaults absent sids to the same string. Keying per-session dedup on it
# collapsed 3,300 distinct antigravity task records into ONE "session" —
# max-per-session kept a single record's cost/tokens and discarded the rest
# ($28.76 true spend reported as $0.075; 2026-07-12 audit). Placeholder-sid
# records must always take the loose (count-individually) path.
_PLACEHOLDER_SIDS = frozenset({"local-session"})


def _real_sid(r: dict) -> str | None:
    """The record's session_id when it is a genuine identity, else None."""
    sid = r.get("session_id")
    if not sid or sid in _PLACEHOLDER_SIDS:
        return None
    return sid


def _dedup_field(records: list[dict], field: str) -> tuple[dict[str, float], list[float]]:
    """Per-session dedup for cumulative snapshot fields (tokens_prompt/completion, total_cost).

    The Claude SessionEnd emitter (scripts/agentica_emit.py) writes ONE record per session
    from the whole transcript, so a resumed session re-emits the SAME session_id with higher
    cumulative totals (verified: session 12e49f9b… emitted 20 rows, tokens_completion rising
    684k→1009k). Summing raw double-counts a single session up to 20x (~46% of windowed rows
    are such re-emits). Each session therefore contributes its MAX (latest cumulative) value
    once. Mirrors _sum_per_session() but preserves floats (that helper int()-truncates, which
    is wrong for dollar costs). Records with no REAL session_id (absent or placeholder)
    can't be deduped → counted individually. Returns (max-per-session dict, loose values)."""
    by_sess: dict[str, float] = {}
    loose: list[float] = []
    for r in records:
        v = r.get(field)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        sid = _real_sid(r)
        if sid:
            by_sess[sid] = max(by_sess.get(sid, 0.0), float(v))
        else:
            loose.append(float(v))
    return by_sess, loose


def _count_success_sessions(records: list[dict]) -> int:
    """Distinct successful sessions (each real session_id once) + loose success records."""
    sids: set = set()
    loose = 0
    for r in records:
        if r.get("status") != "success":
            continue
        sid = _real_sid(r)
        if sid:
            sids.add(sid)
        else:
            loose += 1
    return len(sids) + loose


def _pctile(vals: list[float], p: float):
    if not vals:
        return None
    s = sorted(vals)
    k = (len(s) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return round(s[f] + (s[c] - s[f]) * (k - f), 1)


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
    total_by_sess, total_loose = _dedup_field(records, "total_cost")
    total = sum(total_by_sess.values()) + sum(total_loose)
    stamps = sorted(
        t if t.tzinfo is not None else t.replace(tzinfo=timezone.utc)
        for t in (parse_ts(r.get("timestamp")) for r in records) if t is not None
    )
    if not stamps:
        return {"val": None, "error": "no timestamped records in window", "calibrated": False}
    root = repo_root or _AGENTICA_REPO_ROOT
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
    c = Counter(r.get("model_tier") for r in recs if r.get("model_tier"))
    if not c:
        return None
    n = sum(c.values())
    return " ".join(f"{k}:{round(100 * v / n)}%" for k, v in c.most_common())


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


def _w_num(v) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def _tier_mix_weighted(recs, weight_fn=None):
    """Model-tier distribution weighted by weight_fn(record) (e.g. token spend, tool calls).
    weight_fn=None weights each record equally (task volume). Returns 'FAST:48% ...' or None."""
    c: Counter = Counter()
    for r in recs:
        tier = r.get("model_tier")
        if not tier:
            continue
        c[tier] += 1.0 if weight_fn is None else _w_num(weight_fn(r))
    n = sum(c.values())
    if not n:
        return None
    return " ".join(f"{k}:{round(100 * v / n)}%" for k, v in c.most_common())


def build_tier_mix(recs) -> dict:
    """Per-pillar model-tier mix, each weighted by a metric appropriate to that pillar.
    Weight functions read records read-only (no mutation)."""
    spend = lambda r: _w_num(r.get("tokens_prompt")) + _w_num(r.get("tokens_completion"))
    return {
        "bow":   {"backing": "Tool Calls",   "slices": _tier_mix_weighted(recs, lambda r: r.get("tool_calls"))},
        "sword": {"backing": "Task Volume",  "slices": _tier_mix_weighted(recs, None)},
        "brush": {"backing": "Token Spend",  "slices": _tier_mix_weighted(recs, spend)},
        "arts":  {"backing": "Output Words", "slices": _tier_mix_weighted(recs, lambda r: r.get("output_words"))},
    }
def r_revision_ratio(recs):
    observed = any(r.get("mod_type") for r in recs)
    mods = [r.get("mod_type") for r in recs if r.get("mod_type") in ("SURGICAL", "CLOBBER")]
    if mods:
        return round(100 * sum(1 for m in mods if m == "CLOBBER") / len(mods), 1)
    return 0.0 if observed else None


def _int_vals(recs, field):
    """Integer values of `field` across records (bools/non-numerics dropped). Returns the
    list — caller decides sum-vs-None — hence not named '_isum'."""
    return [int(r[field]) for r in recs
            if isinstance(r.get(field), (int, float)) and not isinstance(r.get(field), bool)]


def r_slop_density(recs):  # slop markers per 1k words of agent output
    # Both fields are per-session cumulative counters re-emitted on resume —
    # sum each session once, not per re-emit row.
    sw = _sum_per_session(recs, "slop_markers")
    ow = _sum_per_session(recs, "output_words")
    return round(sw / ow * 1000, 2) if ow else None


def _sum_per_session(recs, field) -> int:
    """Sum `field` counting each session once (max value seen per real session_id).

    These are per-session counters (rule_violations, frustration_signals, …). When a
    session is logged to telemetry more than once the static count is re-emitted, so
    summing raw records double-counts it. Records without a REAL session_id (absent
    or placeholder) can't be deduplicated, so each is counted individually."""
    by_sess: dict = {}
    loose: list[int] = []
    for r in recs:
        v = r.get(field)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        sid = _real_sid(r)
        if sid:
            by_sess[sid] = max(by_sess.get(sid, 0), int(v))
        else:
            loose.append(int(v))
    return sum(by_sess.values()) + sum(loose)


def _has_field(recs, field) -> bool:
    return any(
        isinstance(v := r.get(field), (int, float)) and not isinstance(v, bool)
        for r in recs
    )


def r_sum_field(field):
    def f(recs):
        return _sum_per_session(recs, field) if _has_field(recs, field) else None
    return f


def _last_simplify_commit_ts():
    """Timestamp of the most recent simplify/refactor/cleanup commit in the repo,
    or None if git is unavailable or none exist. Runtime-agnostic OUTCOME signal:
    any agent (Claude, Antigravity) or a human landing such a commit counts —
    unlike /simplify skill telemetry, which only sees the Claude channel. Mirrors
    _cost_per_outcome's git-log pattern (repo root, timeout, fail-safe). Matches
    conventional-commit subjects starting with refactor/simplif/cleanup."""
    try:
        out = subprocess.run(
            ["git", "-C", str(_AGENTICA_REPO_ROOT), "log", "-1", "--format=%cI",
             "-E", "-i", "--grep=^(refactor|simplif|cleanup|clean[- ]up)"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    line = out.stdout.strip()
    return parse_ts(line) if line else None


def r_simplify_age(recs):
    """Days since simplification last LANDED (recency of the quality gate). Lower
    is better. OUTCOME-based + runtime-agnostic: resets on the most recent of a
    /simplify skill invocation (Claude telemetry) OR a simplify/refactor/cleanup
    commit in git. The commit signal credits hand-rolled work AND the Antigravity
    runtime, which emit no Claude skill telemetry — previously this counted only
    /simplify runs, so cross-runtime or hand-rolled simplification was invisible
    and the age climbed while real work happened."""
    stamps = [parse_ts(r.get("timestamp")) for r in recs if "simplify" in (r.get("skills_used") or [])]
    stamps.append(_last_simplify_commit_ts())
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


def _judge_min_n() -> int:
    """Declared min accepted judgments from the editable surface; 30 is the fallback literal."""
    try:
        from agentica_core import harness_config
        return int(harness_config.get_value("judge_min_n"))
    except Exception:
        return 30


def _tool_quality(metric_key: str):
    """REGISTRY reducer for an Arts tool-use-quality metric. Reads the offline scout's output
    (state/tool_quality.json) — the LLM-judge work runs in tool_quality_scout.py, never here on
    the refresh hot path. Returns a 0-100 percent, or None (-> SIMULATED) when the scout hasn't
    run, the metric was a gap, or a transcript-sampled metric hasn't reached judge_min_n
    accepted judgments (same honesty pattern as Error_Rate's min-sample guard).
    Ignores `recs` (like _cache_hit_rate: source is a state file)."""
    def reducer(recs):  # noqa: ARG001
        f = _ORDER_SAMURAI_ROOT / "state" / "tool_quality.json"
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
                and isinstance(n, int) and not isinstance(n, bool) and n < _judge_min_n()):
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


def r_context_cliff_events(recs):  # noqa: ARG001
    """Count recent sessions whose max single-message input context
    (input + cache_read + cache_creation) exceeded ~140k tokens — a context-pressure signal
    (agents degrade in very large contexts). Reads transcripts directly (like _cache_hit_rate);
    SessionEnd telemetry lacks per-msg usage. ABSOLUTE cutoff, window-agnostic: this machine's
    models are ~1M-window (contexts reach 562k with no 1M marker), so a 70%-of-window rule would
    never fire. Returns None (-> SIMULATED) when no usage data is present."""
    projects_dir = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".claude" / "projects"
    if not projects_dir.exists():
        return None
    jsonls = sorted(projects_dir.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime)[-_CLIFF_SCAN_FILES:]
    threshold = _cliff_threshold()
    scanned = cliffs = 0
    for jl in jsonls:
        max_ctx = 0
        try:
            with open(jl, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if '"usage"' not in line:
                        continue
                    try:
                        entry = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(entry, dict) or entry.get("type") != "assistant":
                        continue
                    usage = (entry.get("message") or {}).get("usage")
                    if not isinstance(usage, dict):
                        continue
                    ctx = 0
                    for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                        v = usage.get(k)
                        if isinstance(v, (int, float)) and not isinstance(v, bool):
                            ctx += v
                    max_ctx = max(max_ctx, ctx)
        except OSError:
            continue
        if max_ctx > 0:
            scanned += 1
            if max_ctx > threshold:
                cliffs += 1
    # Share, not count (2026-07-19 recalibration): the absolute weekly count was
    # volume-coupled — a busy week 'failed' automatically. 100×cliffs/scanned reads
    # as 'what fraction of recent sessions ran hot'. (History rows before this date
    # are counts; the σ window flushes within ~a week.)
    return round(100.0 * cliffs / scanned, 1) if scanned else None


def r_chain_depth_avg(recs):
    # The chain_depth field counts total Agent/Task calls per session (not nesting depth).
    # Large orchestration runs (ultracode) produce counts >1000 and skew the mean badly.
    # Median gives the typical session's orchestration load truthfully.
    vals = _int_vals(recs, "chain_depth")
    return _pctile(vals, 50) if vals else None


# ---------------------------------------------------------------- registry
# (pillar, group, key, reducer|None, live_tier, is_percent, is_count)
# ---------------------------------------------------------------------------
# Helper functions for new reducers
# ---------------------------------------------------------------------------

def _parse_iso(val: Any) -> datetime | None:
    if not val or not isinstance(val, str):
        return None
    try:
        dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None

def _get_weekly_promotions_count(now: datetime) -> int:
    log_path = Path.home() / ".claude" / "data" / "skill_promotion_log.jsonl"
    if not log_path.exists():
        return 0
    count = 0
    this_week = now.strftime("%G-W%V")
    try:
        for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                ts_val = obj.get("timestamp") or obj.get("ts") or obj.get("created_at")
                if ts_val:
                    dt = datetime.fromisoformat(ts_val.replace("Z", "+00:00"))
                    if dt.strftime("%G-W%V") == this_week:
                        count += 1
            except Exception:
                continue
    except Exception:
        pass
    return count

def _get_prior_week_val(history_path: Path, metric_key: str,
                        before_week: str | None = None) -> float | None:
    """Latest history value for metric_key; with before_week (\"%G-W%V\"), only
    snapshots from a strictly earlier ISO week count — otherwise the most recent
    snapshot may already contain the current week and deltas self-compare to 0."""
    if not history_path.exists():
        return None
    try:
        lines = history_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        for line in reversed(lines):
            if not line.strip():
                continue
            obj = json.loads(line)
            # Live rows are computed over the payload's 30-day window, not one ISO
            # week — using one as a week baseline made the savings figure swing
            # ~2x on scheduler timing (whether backfill had pruned it yet).
            # Weekly baselines come from kind:"weekly" (or legacy un-kinded) rows.
            if obj.get("kind") == "live":
                continue
            if before_week is not None:
                ts = _parse_iso(obj.get("ts"))
                # zero-padded %G-W%V strings order lexicographically
                if ts is None or ts.strftime("%G-W%V") >= before_week:
                    continue
            vals = obj.get("values", {})
            val = vals.get(metric_key)
            if val is not None:
                try:
                    if isinstance(val, str):
                        cleaned = "".join(c for c in val if c.isdigit() or c == "." or c == "-")
                        return float(cleaned)
                    return float(val)
                except Exception:
                    pass
    except Exception:
        pass
    return None

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

# ---------------------------------------------------------------------------
# Reducer implementations
# ---------------------------------------------------------------------------

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

# Vulnerability_MTTR retired 2026-07-11 (C/D/F remediation plan step 3): the name
# promised CVE mean-time-to-resolution but the reducer read kill-chain events, and
# after the 2026-07-08 default-removal it was permanently SIMULATED (no chains most
# weeks = no measurement). Kill-chain response is covered by Kill_Chains_Open /
# Kill_Chains_Disrupted; CVE exposure is now graded directly via Open_CVEs (the
# dependency_audit.json emitter is live again). Re-add a real MTTR only when a
# first-seen→resolved CVE ledger exists.

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
    spawn_marginals: list[float] = []
    solo_costs: list[float] = []
    for r in records:
        c = r.get("total_cost")
        if not isinstance(c, (int, float)) or isinstance(c, bool) or c <= 0:
            continue  # zero cost = unattributed, not free (same rule as r_cost_per_task)
        n = r.get("subagent_spawns")
        spawns = n if isinstance(n, (int, float)) and not isinstance(n, bool) else 0
        if spawns > 0:
            spawn_marginals.append(float(c) / max(spawns, 1))
        else:
            solo_costs.append(float(c))
    if not spawn_marginals or not solo_costs:
        return None
    spawn_marginal = median(spawn_marginals)
    if spawn_marginal <= 0:
        return None
    allowed = median(solo_costs)
    return round(min(100.0, 100.0 * allowed / spawn_marginal), 1)


_MCP_UUID_NAMES: dict[str, str] = {
    "2f62a1e0": "Vercel", "951222fb": "Exa Search", "952281cb": "Gmail",
    "b39b0009": "Google Calendar", "d66590cf": "Supabase", "0667bb3a": "Drive",
    "9aa7cbe6": "Context7", "831d333c": "Visualize", "becec896": "Stripe",
    "694eaac4": "Firecrawl",
}
_MCP_SERVER_DISPLAY: dict[str, str] = {
    "ccd_session": "CCD Session", "ccd_directory": "CCD Directory",
    "Claude_Preview": "Claude Preview", "Claude_in_Chrome": "Chrome MCP",
    "computer-use": "Computer Use", "scheduled-tasks": "Scheduled Tasks",
    "mcp-registry": "MCP Registry",
    "plugin_engineering_github": "GitHub Plugin", "plugin_engineering_datadog": "Datadog Plugin",
    "plugin_productivity_linear": "Linear Plugin", "plugin_productivity_notion": "Notion Plugin",
}

def _mcp_server_label(server: str) -> str:
    if server in _MCP_SERVER_DISPLAY:
        return _MCP_SERVER_DISPLAY[server]
    slug = server.replace("-", "").replace("_", "")[:8].lower()
    for prefix, name in _MCP_UUID_NAMES.items():
        if slug.startswith(prefix[:8]):
            return name
    return server.replace("_", " ").replace("-", " ").title()


# 60s TTL cache of parsed (timestamp, subagent_type) spawn events — the transcript
# scan is window-independent; the per-window filter happens on the parsed list, so
# a 3-window refresh parses the transcripts once instead of three times.
_AGENT_SPAWN_CACHE: dict = {"t": 0.0, "v": None}

# Safety valve on the file scan, not a real limit: callers window this list down to
# 1d/7d/30d/all-time (refresh_dashboard.py's "all" variant uses window_days=36500),
# so the scan must cover every session file that could fall in the widest window, or
# the narrower windows silently truncate too (a fixed 40-file cap once made "top
# agents, last 7 days" actually mean "top agents in whatever sliver of today those 40
# files happened to span" — found 2026-08-14, 589 files were touched in a real 7-day
# window). Measured at ~3s for the full ~2.3k-file corpus (cheap '"Agent"' substring
# pre-filter below), well inside the 60s cache TTL, so there's no perf reason to cap
# tighter. This ceiling only exists to bound runaway growth, not to bind in practice.
_AGENT_SPAWN_FILE_CAP = 10_000


def _agent_spawn_events() -> list[tuple[str, str]]:
    """(timestamp, subagent_type) for Agent tool_use entries across up to
    _AGENT_SPAWN_FILE_CAP most recently touched session JSONLs — effectively all of
    them at current volume, so windowed counts (1d/7d/30d/all) are accurate rather
    than truncated to whatever a small fixed file cap happened to cover."""
    import json as _json
    now = time.monotonic()
    if (_AGENT_SPAWN_CACHE["v"] is not None
            and now - _AGENT_SPAWN_CACHE["t"] < _SCOUT_CACHE_TTL_SEC):
        return _AGENT_SPAWN_CACHE["v"]
    projects_dir = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".claude" / "projects"
    events: list[tuple[str, str]] = []
    if not projects_dir.exists():
        return events
    jsonls = sorted(projects_dir.rglob("*.jsonl"),
                     key=lambda p: p.stat().st_mtime)[-_AGENT_SPAWN_FILE_CAP:]
    for jl in jsonls:
        try:
            with open(jl, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if '"Agent"' not in line:  # cheap pre-filter before json.loads
                        continue
                    try:
                        entry = _json.loads(line)
                        if entry.get("type") != "assistant":
                            continue
                        for c in (entry.get("message") or {}).get("content") or []:
                            if (isinstance(c, dict) and c.get("type") == "tool_use"
                                    and c.get("name") == "Agent"):
                                st = (c.get("input") or {}).get("subagent_type")
                                if st:
                                    events.append((entry.get("timestamp", ""), st))
                    except Exception:
                        pass
        except Exception:
            pass
    _AGENT_SPAWN_CACHE.update(t=now, v=events)
    return events


def _count_agent_types(window_days: int | None = None) -> Counter:
    """Count Agent subagent_type invocations, optionally windowed by timestamp."""
    return Counter(
        st for ts, st in _agent_spawn_events()
        if window_days is None or _within_days(ts, window_days)
    )


def _top_usage(records: list[dict], window_days: int | None = None) -> dict:
    """Leaderboard over the given (already window-filtered) records.

    skills_used / tool_calls_list are re-emitted whole on every SessionEnd
    re-emit, so raw counting didn't just inflate the leaderboard — it changed
    the ORDER (2026-07-12 audit: raw top-3 was model/improve-system/goal;
    per-session top-3 is simplify/status/goal-check). Count each skill and
    connection once per real session; placeholder-sid records count per row."""
    skill_counts: Counter = Counter()
    conn_counts: Counter = Counter()
    seen_skill: set[tuple[str, str]] = set()   # (sid, skill)
    seen_conn: set[tuple[str, str]] = set()    # (sid, server label)
    for r in records:
        sid = _real_sid(r)
        for s in (r.get("skills_used") or []):
            if sid:
                if (sid, s) in seen_skill:
                    continue
                seen_skill.add((sid, s))
            skill_counts[s] += 1
        for tool in (r.get("tool_calls_list") or []):
            if tool.startswith("mcp__"):
                parts = tool.split("__")
                server = parts[1] if len(parts) > 1 else tool
                label = _mcp_server_label(server)
                if sid:
                    if (sid, label) in seen_conn:
                        continue
                    seen_conn.add((sid, label))
                conn_counts[label] += 1

    agent_counts = _count_agent_types(window_days)

    return {
        "skills": [{"name": k, "count": v} for k, v in skill_counts.most_common(5)],
        "connections": [{"name": k, "count": v} for k, v in conn_counts.most_common(5)],
        "agents": [{"name": k, "count": v} for k, v in agent_counts.most_common(5)],
    }


# Remediation actions that count as actually disrupting a chain — the single
# source of truth for "disrupted" vs "still open" across the kill-chain reducers.
_DISRUPT_ACTIONS = ("block", "patch", "remediat", "quarantine", "revert")


def _kill_chain_week_sets(paths: list[Path]) -> tuple[set, set, set, set]:
    """Distinct chain_ids with events this/last ISO week across all paths, split into
    (this_detected, last_detected, this_disrupted, last_disrupted).
    Disrupted = at least one event whose remediation_action goes beyond logging.
    Uses sets so the same chain_id from multiple files is counted only once."""
    now = datetime.now(timezone.utc)
    this_week = now.strftime("%G-W%V")
    last_week = (now - timedelta(days=7)).strftime("%G-W%V")
    this_det: set = set()
    last_det: set = set()
    this_dis: set = set()
    last_dis: set = set()
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                ts = obj.get("ts")
                chain_id = obj.get("chain_id")
                if ts is None or chain_id is None:
                    continue
                wk = datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%G-W%V")
                action = str(obj.get("remediation_action", "")).lower()
                disruptive = any(a in action for a in _DISRUPT_ACTIONS)
                if wk == this_week:
                    this_det.add(chain_id)
                    if disruptive:
                        this_dis.add(chain_id)
                elif wk == last_week:
                    last_det.add(chain_id)
                    if disruptive:
                        last_dis.add(chain_id)
            except Exception:
                continue
    return this_det, last_det, this_dis, last_dis


_KC_PRUNE = {"node_modules", ".git", "__pycache__", "dist", ".venv", "venv",
             "sub-bundles", ".tmp", "artifacts", ".ruff_cache", ".pytest_cache"}


def _kill_chain_paths(repo_root: Path) -> list[Path]:
    """Every kill_chain_events.jsonl under the hub and the Agentica OS repo tree.

    prompt_injection_guard.py writes events to ``<cwd>/state/kill_chain_events.jsonl``,
    so sessions launched from sub-directories (Governance/, api/, dashboard-ui/, …)
    scatter event files across the tree. A fixed root list silently misses them; we
    walk the tree (pruning heavy dirs) so no events are lost."""
    paths: list[Path] = [repo_root / "state" / "kill_chain_events.jsonl"]
    for root in _KILL_CHAIN_EXTRA_ROOTS:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _KC_PRUNE]
            if Path(dirpath).name == "state" and "kill_chain_events.jsonl" in filenames:
                paths.append(Path(dirpath) / "kill_chain_events.jsonl")
    return list(dict.fromkeys(p.resolve() for p in paths))


def _kill_chains_disrupted(records: list[dict], repo_root: Path | None = None) -> dict:  # noqa: ARG001
    if repo_root is None:
        repo_root = _ORDER_SAMURAI_ROOT
    paths = _kill_chain_paths(repo_root)
    if not any(p.exists() for p in paths):
        # No kill_chain_events source anywhere = the emitter is dead/unwired. A bare
        # "0 disrupted" here is indistinguishable from a genuinely secure week, so flag
        # a data_gap and let the hero fall back to a real measured security signal
        # (Secrets_Detected) instead of presenting a confident — possibly false — 0.
        return {"val": 0, "week_delta": 0, "calibrated": True, "data_gap": True}
    try:
        _, _, this_dis, last_dis = _kill_chain_week_sets(paths)
        return {"val": len(this_dis), "week_delta": len(this_dis) - len(last_dis), "calibrated": True}
    except Exception as e:
        return {"val": None, "error": f"source unavailable: {str(e)}", "calibrated": False}


def _kill_chains_open(records: list[dict], repo_root: Path | None = None) -> dict:  # noqa: ARG001
    """Open exposure: chains detected this ISO week with NO disruptive event yet
    (block/patch/quarantine/revert). Graded successor of the ungraded
    Kill_Chains_Detected row (2026-07-08 audit consolidation) — the actionable
    number is the detected−disrupted gap, not raw detections."""
    if repo_root is None:
        repo_root = _ORDER_SAMURAI_ROOT
    paths = _kill_chain_paths(repo_root)
    if not any(p.exists() for p in paths):
        # same honesty rule as _kill_chains_disrupted: a bare 0 with no emitter is
        # indistinguishable from a secure week — flag the gap
        return {"val": 0, "week_delta": 0, "calibrated": True, "data_gap": True}
    try:
        this_det, last_det, this_dis, last_dis = _kill_chain_week_sets(paths)
        this_open = len(this_det - this_dis)
        last_open = len(last_det - last_dis)
        return {"val": this_open, "week_delta": this_open - last_open, "calibrated": True}
    except Exception as e:
        return {"val": None, "error": f"source unavailable: {str(e)}", "calibrated": False}

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
    if repo_root is None:
        repo_root = _ORDER_SAMURAI_ROOT
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


# Self_Correction_Rate is a REGISTRY reducer: build_pillars() hands it `records` and
# nothing else, so aggregate()'s window_days cannot reach it as an argument. aggregate()
# publishes the window it is currently building here (the refresh is single-threaded);
# direct build_pillars() callers get this default. Without a window the rate counted the
# whole exec_log lifetime — one frozen number that 18 days of zero autonomous attempts
# could not move, which is precisely the failure this metric exists to surface.
_ACTIVE_WINDOW_DAYS = 30


def _self_correction_rate(records: list[dict]) -> dict:
    """AUTO-005: Self-Correction Rate — the autonomic self-healing IMPROVEMENT rate:
    % of ALL autonomous-remediation attempts that actually IMPROVED their target metric
    (improved / attempted), from remediation.efficacy() over this window. A reflex/
    sensei remediation fires on a degraded metric; this measures how often the metric
    then moved the right way, from a real before/after reading per event — so it is
    calibrated from day one (no seed coefficient, unlike Est. Agent Hours Saved).

    2026-08-01 (metric-gap remediation, phase A1): sources `improvement_rate`
    (improved/attempted), not the retired `success_rate` (improved/applied). `applied`
    only counts attempts that got a judged before/after bracket — most attempts never
    do — so success_rate silently dropped the majority of no-op/unmeasured runs from
    its denominator, turning a single-digit real improvement rate into a ~52% headline.
    A window with zero attempted remediations is a data gap (nothing to rate), never a
    fabricated 0%.

    2026-08-08 (seq 9a): windowed. The counters now cover `_ACTIVE_WINDOW_DAYS` of
    exec_log instead of its whole lifetime, so an engine that has stopped firing shows
    up as a data gap ("no autonomous attempts in Nd") rather than holding its last
    lifetime rate forever. Lifetime totals remain in the efficacy dict."""
    try:
        eff = remediation.efficacy(records=records, window_days=_ACTIVE_WINDOW_DAYS)
        attempted = eff.get("attempted", 0) or 0
        rate = eff.get("improvement_rate")
        if not attempted or rate is None:
            return {"val": None, "data_gap": True, "calibrated": True,
                    "detail": eff.get("data_gap_detail") or "no judgeable remediation attempts"}
        # execution_success_rate (completed/attempted) is reported alongside the movement
        # rate so a low `rate` reads as one of two distinct problems: an engine that isn't
        # completing runs at all (low execution_success_rate — check permission/tooling
        # gates first) vs one that completes cleanly but doesn't move the metric (high
        # execution_success_rate despite low rate — check skill selection/judge timing).
        exec_rate = eff.get("execution_success_rate")
        exec_detail = (f", {eff.get('completed', 0)}/{attempted} runs completed ({exec_rate}%)"
                       if exec_rate is not None else "")
        return {"val": rate, "calibrated": True,
                "detail": (f"{eff.get('improved', 0)}/{attempted} remediation attempts improved "
                           f"their metric in the last {eff.get('window_days')}d"
                           f"{exec_detail} "
                           f"({eff.get('improved_lifetime', 0)}/{eff.get('attempted_lifetime', 0)} lifetime)")}
    except Exception as e:
        return {"val": None, "error": f"source unavailable: {str(e)}", "calibrated": False}


def _mitigation_route_validity(records: list[dict]) -> dict:  # noqa: ARG001
    """Mitigation_Route_Validity (2026-08-01, metric-gap remediation, phase D1): %
    of this codebase's full metric REGISTRY (every row the dashboard emits, graded
    or not) whose mitigation route is real and not known-broken. A metric counts
    as VALID when it has both a METRIC_RULES entry (a direction to judge it by)
    and a REMEDIATION entry (a skill/command) whose resolved kind is NOT
    `mis_route` (insights.remediation_kind's explicit DO-NOT-USE marker, e.g.
    Boundary_Violations -> /guard, which can't fix existing violations). A metric
    with no rule or no route at all (an informational counter, or a compound
    AUTO-tier reducer like Self_Correction_Rate that no single skill remediates)
    is INVALID here -- it structurally has no route to validate, which is exactly
    what this metric surfaces: not every tracked number has an owner for fixing
    it. Pure computation over the static registry; no new collection, `records`
    unused. OBSERVATIONAL/informational (no METRIC_CONFIG entry, like its sibling
    Self_Correction_Rate) -- this metric describes the registry's own wiring
    health and isn't itself a thing any skill remediates."""
    total = len(REGISTRY)
    if not total:
        return {"val": None, "data_gap": True, "calibrated": True}
    valid = 0
    for _pillar, _group, key, _fn, _tier, _is_pct, _is_cnt in REGISTRY:
        cfg = insights.METRIC_CONFIG.get(key, {})
        rem = insights.REMEDIATION.get(key)
        rule = insights.METRIC_RULES.get(key)
        if not rule or not rem:
            continue
        kind = insights.remediation_kind(
            rem["command"], readonly=cfg.get("readonly", False),
            auto_remediable=cfg.get("auto_remediable"), explicit_kind=cfg.get("kind"))
        if kind != "mis_route":
            valid += 1
    return {"val": round(100 * valid / total, 1), "calibrated": True,
            "detail": f"{valid}/{total} registry metrics have a real, non-mis-routed remediation path"}


def _remediation_delta(records: list[dict]) -> dict:  # noqa: ARG001
    """Remediation_Delta (2026-08-01, metric-gap remediation, phase B2): magnitude
    companion to Self_Correction_Rate's yes/no judgment. val = the overall median
    delta (median(3 post-firing) - median(3 pre-firing) history values, sign-
    normalized by dir) across every skill's attempts this window, from
    remediation_delta.compute(). A window with no attempts scored on both sides yet
    is a data gap (nothing to rate), never a fabricated 0. OBSERVATIONAL — see
    insights.METRIC_CONFIG["Remediation_Delta"].

    2026-08-08 (seq 9d): the detail no longer reports one lumped "N pending". Most of
    that number was never drainable — attempts missing PRE-firing history, which is
    permanent — and attempts with no METRIC_RULES entry were dropped in silence. The
    three buckets are reported separately: pending / unscoreable / unrated."""
    try:
        d = remediation_delta.compute()
        val = d.get("overall")
        counts = (f"{d.get('pending', 0)} pending, {d.get('unscoreable', 0)} unscoreable "
                  f"(no pre-firing history — permanent), {d.get('unrated', 0)} unrated "
                  f"(no metric rule)")
        if val is None:
            return {"val": None, "data_gap": True, "calibrated": True,
                    "detail": f"no attempt scored on both sides — {counts}"}
        return {"val": val, "calibrated": True,
                "detail": (f"median delta {val}% of pre-firing median across "
                           f"{len(d.get('by_skill', {}))} skill(s); {counts}")}
    except Exception as e:
        return {"val": None, "error": f"source unavailable: {str(e)}", "calibrated": False}


def _verifier_falsifiability(records: list[dict]) -> dict:  # noqa: ARG001
    """Verifier_Falsifiability (2026-08-01, metric-gap remediation, phase C2): % of
    registered falsifiability checks (Order Samurai/execution/verify_falsifiability.py)
    proven to fail on a known-bad fixture AND pass on a known-clean one over this
    session's process lifetime. A verifier that never sees a deliberately-bad input
    can silently CLEAN forever -- this closes exactly that gap. `records` is ignored
    (this reads fixture/script state, not telemetry). OBSERVATIONAL: see
    insights.METRIC_CONFIG["Verifier_Falsifiability"]."""
    try:
        if str(_ORDER_SAMURAI_ROOT) not in sys.path:
            sys.path.insert(0, str(_ORDER_SAMURAI_ROOT))
        from execution.verify_falsifiability import run_falsifiability
        r = run_falsifiability()
        total = r.get("total", 0) or 0
        falsifiable = r.get("falsifiable", 0)
        if not total:
            return {"val": None, "data_gap": True, "calibrated": True}
        return {"val": round(100 * falsifiable / total, 1), "calibrated": True,
                "detail": f"{falsifiable}/{total} checks proven falsifiable"}
    except Exception as e:
        return {"val": None, "error": f"source unavailable: {str(e)}", "calibrated": False}


def _record_set_week(records: list[dict]) -> str | None:
    """The latest ISO week ('%G-W%V') actually present in `records`, or None when the
    set carries no parseable timestamp. Zero-padded %G-W%V strings order
    lexicographically, so max() is the newest week."""
    weeks = [w for w in (iso_week(r.get("timestamp", "")) for r in records) if w]
    return max(weeks) if weeks else None


def _target_week_anchor(records: list[dict]) -> datetime:
    """The instant a WEEKLY reducer should treat as "now" — the Monday of the newest
    ISO week in `records`, falling back to wall-clock when the set is empty or carries
    no parseable timestamp.

    2026-08-08 metric-honesty class sweep. Every weekly reducer here opened with
    `now = datetime.now(timezone.utc)`. That is correct for a live refresh (the newest
    record IS today, so this helper returns the same week) but wrong for a REPLAY:
    backfill_history.py feeds build_pillars() one historical ISO week of telemetry at a
    time, and a wall-clock `now` made every one of those rows describe the CURRENT week
    instead — re-stamping today's state across the whole rebuilt series and fabricating
    a flat history. Anchoring on Monday keeps `anchor - 7d` inside the prior ISO week
    regardless of which weekday the records fall on.

    Kill switch: AGENTICA_METRIC_WEEK_FROM_RECORDS=false|0|no restores the wall-clock
    week for every reducer that calls this. Default is the fixed behaviour. (Shared
    house idiom — remediation._kill_switch_off.)
    """
    if remediation._kill_switch_off("AGENTICA_METRIC_WEEK_FROM_RECORDS"):
        return datetime.now(timezone.utc)
    week = _record_set_week(records)
    if week is None:
        return datetime.now(timezone.utc)
    return datetime.strptime(f"{week}-1", "%G-W%V-%u").replace(tzinfo=timezone.utc)


#: (monotonic stamp, result) — see the memo note in _unbounded_wait_count.
_unbounded_wait_memo: tuple[float, dict] | None = None
_UNBOUNDED_WAIT_TTL_S = 300.0


def _unbounded_wait_count(records: list[dict]) -> dict:  # noqa: ARG001
    """Unbounded_Wait_Count (2026-08-04, metric-gap remediation, phase D2): count of
    RUNTIME code sites that can block forever -- a `while`+`sleep` loop with no
    deadline, or a remote/subprocess call with no `timeout=` -- from
    Order Samurai/execution/timeout_audit_scan.py. Instruments Core Principle #8
    (every remote call gets an explicit timeout), whose only prior enforcement was
    honor-system. `records` is ignored (this reads source, not telemetry).

    Counts runtime sites only; test-fixture findings stay visible in `detail` but
    are excluded (the scanner's module docstring explains why). OBSERVATIONAL --
    see insights.METRIC_CONFIG["Unbounded_Wait_Count"].

    MEMOIZED with a short TTL (M6.2, 2026-08-16). build_pillars() runs every
    reducer, and aggregate() calls build_pillars() per view -- profiled at 22
    calls per aggregate(), each re-running this AST scan of ~200 files: 43s of a
    68s build spent recomputing one deterministic value. The scan reads source
    code, which cannot change mid-aggregate, so within-run reuse is exact; the
    TTL (not @lru_cache -- Anti-Pattern #6) bounds cross-run staleness for
    long-lived processes to a window this OBSERVATIONAL metric tolerates."""
    global _unbounded_wait_memo
    now = time.monotonic()
    if _unbounded_wait_memo is not None:
        stamped_at, cached = _unbounded_wait_memo
        if now - stamped_at < _UNBOUNDED_WAIT_TTL_S:
            return dict(cached)  # copy: envelopes downstream may be mutated per view
    try:
        if str(_ORDER_SAMURAI_ROOT) not in sys.path:
            sys.path.insert(0, str(_ORDER_SAMURAI_ROOT))
        from execution.timeout_audit_scan import scan_tree
        r = scan_tree(_ORDER_SAMURAI_ROOT.parent)
        runtime = r.get("runtime_count", 0)
        result = {"val": runtime, "calibrated": True,
                  "detail": f"{runtime} runtime site(s) with no deadline "
                            f"({r.get('test_count', 0)} more in test fixtures, not counted)"}
        _unbounded_wait_memo = (now, result)
        return dict(result)
    except Exception as e:
        # Failures are NOT memoized: a transient read error must not suppress the
        # next attempt for a whole TTL window.
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
    if repo_root is None:
        repo_root = _ORDER_SAMURAI_ROOT

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
        if v2 and _ACTIVE_WINDOW_DAYS < 7:
            return {
                "val": None, "week_delta": 0.0, "calibrated": False,
                "estimate_by_design": True, "data_gap": True,
                "detail": (f"suppressed — a {_ACTIVE_WINDOW_DAYS}d telemetry window cannot "
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


def _vibe_alignment_score(records: list[dict], repo_root: Path) -> float:  # noqa: ARG001
    """Anti-slop vibe alignment score (0-100) from state/vibe_alignment.json.

    Written by scouts/vibe_alignment_scout.py (local gemma-4-e4b pass).
    Returns 0.0 when the file is absent or the last run failed (score=null).
    """
    vibe_path = repo_root / "state" / "vibe_alignment.json"
    if not vibe_path.exists():
        return 0.0
    try:
        d = json.loads(vibe_path.read_text(encoding="utf-8", errors="ignore"))
        score = d.get("score")
        if not isinstance(score, (int, float)):
            return 0.0
        return float(score)
    except Exception:
        return 0.0


def _doc_parity_latency_days(records: list[dict], repo_root: Path) -> float:  # noqa: ARG001
    """Days between the most recently modified source file and the oldest charter doc.

    Compares the newest .py/.sh/.ts file mtime in execution/, scouts/, bin/, agentica_core/
    against the oldest .md mtime in state/charters/. A large gap means code changed
    significantly since the charters were last updated. Returns 0.0 when all docs
    are at least as fresh as the newest source change.
    """
    source_dirs = ["execution", "scouts", "bin", "agentica_core"]
    source_exts = {".py", ".sh", ".ts", ".js"}
    charter_dir = repo_root / "state" / "charters"

    # Newest source file mtime
    newest_src_mt: float = 0.0
    for sdir in source_dirs:
        d = repo_root / sdir
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if p.suffix in source_exts and p.is_file():
                try:
                    newest_src_mt = max(newest_src_mt, p.stat().st_mtime)
                except OSError:
                    pass

    # Oldest charter doc mtime
    oldest_doc_mt: float = float("inf")
    if charter_dir.exists():
        for p in charter_dir.glob("*.md"):
            try:
                oldest_doc_mt = min(oldest_doc_mt, p.stat().st_mtime)
            except OSError:
                pass

    if newest_src_mt == 0.0 or oldest_doc_mt == float("inf"):
        return 0.0

    gap_seconds = max(0.0, newest_src_mt - oldest_doc_mt)
    return round(gap_seconds / 86400, 1)  # days


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
    if repo_root is None:
        repo_root = _ORDER_SAMURAI_ROOT
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
        vibe_str = (f"Vibe {vibe_now:g}" if prior_vibe is None
                    else f"Vibe {round(vibe_now - prior_vibe, 1):+g}")

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
    if repo_root is None:
        repo_root = _ORDER_SAMURAI_ROOT
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
        vibe_gain = max(vibe_now - prior_vibe, 0.0) if prior_vibe is not None else 0.0
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


def _pending_chain_proposals(records: list[dict], repo_root: Path | None = None) -> dict:  # noqa: ARG001
    if repo_root is None:
        repo_root = _ORDER_SAMURAI_ROOT
    path = repo_root / "state" / "proposed_kill_chains.json"
    if not path.exists():
        return {"val": 0, "week_delta": 0, "calibrated": True}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        proposals = data.get("proposals", [])
        val = sum(1 for p in proposals if p.get("status") == "proposed")
        return {"val": val, "week_delta": 0, "calibrated": True}
    except Exception as e:
        return {"val": None, "error": f"source unavailable: {str(e)}", "calibrated": False}

# 60s TTL cache: vault health is a point-in-time scout (no window/platform input)
# but build_pillars runs ~15x per aggregate and refresh_dashboard aggregates 3
# windows — without this, one refresh full-scans the vault ~45 times (~12k file
# reads each). A short TTL (not @lru_cache) so long-lived callers stay fresh.
_VAULT_CACHE: dict = {"t": 0.0, "v": None}
_SCOUT_CACHE_TTL_SEC = 60.0


def _vault_health_metrics() -> dict | None:
    """Load Knowledge/vault/_scripts/vault_health.py dynamically and return current vault metrics.
    Returns None if the script is unavailable (caller emits SIMULATED)."""
    import importlib.util
    now = time.monotonic()
    if _VAULT_CACHE["v"] is not None and now - _VAULT_CACHE["t"] < _SCOUT_CACHE_TTL_SEC:
        return _VAULT_CACHE["v"]
    if not _VAULT_HEALTH_SCRIPT.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location("agentica_vault_health", _VAULT_HEALTH_SCRIPT)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        pending = module.check_raw_pending()
        counts = module.wiki_article_counts()
        stale = module.find_stale_articles()
        orphans = module.find_orphaned_wiki()
        score = module.compute_score(pending, counts, stale, orphans) if hasattr(module, "compute_score") else None
        result = {
            "Wiki_Health_Score": score,
            "Wiki_Article_Count": sum(counts.values()),
            "Raw_Pending": len(pending),
            "Wiki_Orphans": len(orphans),
        }
        _VAULT_CACHE.update(t=now, v=result)
        return result
    except Exception:
        return None


def _mechanism_liveness(records: list[dict]) -> dict:
    """AUTO-019: Mechanism Liveness — real count of mechanism_run events (registered
    mechanisms that ran AND had their output consumed, the 3-step Mechanism Rule) in
    the CANONICAL cross-platform stream (Data/telemetry/autonomic_events.jsonl).

    Two real producers feed this stream, no fabrication in either:
      - scouts/autonomic_events_scout.py (bow pillar): bridges the mechanism_audit
        health-check's real ~/.claude/data/mechanism_audit.json output — a mechanism_run
        is only emitted when that file exists with a genuine generated_at + counts shape.
      - Governance/refresh_dashboard.py `_emit_mechanism_runs()`: bridges real
        ReflexEngine autonomous runs recorded in Order Samurai's state/exec_log.jsonl.

    A window with zero mechanism_run events anywhere in the stream is a genuine data
    gap (nothing observed yet), never a fabricated zero — mirrors _kill_chains_open's
    honesty rule for a dead/unwired emitter.

    2026-08-08 class sweep: `records` is not this metric's data source but it does say
    WHICH week is being asked about (see _target_week_anchor), so a backfill replay of
    week N counts week N's mechanism_run events rather than the current week's."""
    path = default_events_path()
    if not path.exists():
        return {"val": None, "data_gap": True, "calibrated": True}
    now = _target_week_anchor(records)
    this_week = now.strftime("%G-W%V")
    last_week = (now - timedelta(days=7)).strftime("%G-W%V")
    this_count = last_count = 0
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict) or rec.get("event") != "mechanism_run":
                continue
            wk = iso_week(rec.get("timestamp"))
            if wk == this_week:
                this_count += 1
            elif wk == last_week:
                last_count += 1
    except OSError as e:
        return {"val": None, "error": f"source unavailable: {e}", "calibrated": False}
    if this_count == 0 and last_count == 0:
        return {"val": 0, "week_delta": 0, "calibrated": True, "data_gap": True}
    return {"val": this_count, "week_delta": this_count - last_count, "calibrated": True}


def _lesson_graduation_rate(records: list[dict]) -> dict:  # noqa: ARG001
    """AUTO-017 (Lesson Graduation Rate): fraction of skills ever added to the real
    "lesson ledger" (`~/.claude/data/skill_improve_queue.jsonl` — reflex_eureka's
    per-invocation queue, one entry per skill use awaiting a 24h post-hoc
    effectiveness review) that have GRADUATED into a durable, reusable RULE — a
    skill classified >= 70% autonomous-improvement effective across >= 5 runs in
    the "RULE — High-Effectiveness Skills" section of `~/.claude/data/
    auto_eureka_skills.md` (the report reflex_eureka.py regenerates from
    exec_log.jsonl).

    A skill "graduates" once its own real usage history earns reflex_eureka's
    trust as a proven autonomous remediator; everything else still sitting in the
    ledger (never classified, or classified GOTCHA/CONTEXT/insufficient-data) has
    NOT graduated yet.

    Ignores `records` — this metric's real source lives outside the canonical
    telemetry schema. Returns a data_gap envelope (never a fabricated rate) when
    the ledger is missing or has never captured a lesson."""
    home = Path(os.environ.get("USERPROFILE", str(Path.home())))
    data_dir = home / ".claude" / "data"
    queue_path = data_dir / "skill_improve_queue.jsonl"
    eureka_path = data_dir / "auto_eureka_skills.md"

    if not queue_path.exists():
        return {"val": None, "data_gap": True, "calibrated": True}

    queued_skills: set[str] = set()
    try:
        for line in queue_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            skill = rec.get("skill") if isinstance(rec, dict) else None
            if isinstance(skill, str) and skill.strip():
                queued_skills.add(skill.strip().lower())
    except OSError as e:
        return {"val": None, "error": f"source unavailable: {e}", "calibrated": False}

    if not queued_skills:
        # Ledger file exists but has never captured a single lesson — genuine
        # data gap, not a fabricated 0%.
        return {"val": None, "data_gap": True, "calibrated": True}

    graduated_skills: set[str] = set()
    if eureka_path.exists():
        try:
            md = eureka_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            md = ""
        section_match = re.search(
            r"## RULE — High-Effectiveness Skills(.*?)(?:\n## |\Z)", md, re.DOTALL)
        section = section_match.group(1) if section_match else ""
        for m in re.finditer(r"\*\*`/?([\w-]+)`\*\*", section):
            graduated_skills.add(m.group(1).strip().lower())

    graduated_in_ledger = queued_skills & graduated_skills
    rate = round(100 * len(graduated_in_ledger) / len(queued_skills), 1)
    return {
        "val": rate,
        "calibrated": True,
        "detail": f"{len(graduated_in_ledger)}/{len(queued_skills)} ledger skills graduated to RULE",
    }


_CACHE_HIT_CACHE: dict = {"t": 0.0, "v": None}
_CACHE_HIT_SCAN_FILES = 60  # bounded recent-transcript sample (mirrors _agent_spawn_events)


def _cache_hit_rate(records: list[dict]) -> dict:  # noqa: ARG001
    """AUTO-009 (Cache Hit Rate): prompt-cache reuse on INPUT tokens, read directly from
    real Claude Code session transcripts (~/.claude/projects/**/*.jsonl).

    The `records` telemetry every other reducer here consumes (the SessionEnd emitter's
    tokens_prompt/tokens_completion) never carries cache_read_input_tokens or
    cache_creation_input_tokens — those only exist on the raw assistant message.usage
    block inside each transcript line. So, like _agent_spawn_events, this reducer
    ignores `records` and scans the transcripts directly, bounded to the
    _CACHE_HIT_SCAN_FILES most recently touched session JSONLs (never the full
    multi-hundred-file history) with a short TTL cache so repeated aggregate() calls
    in one refresh don't re-scan.

    Cache_Hit_Rate = cache_read_input_tokens
                     / (cache_read_input_tokens + cache_creation_input_tokens + input_tokens)
    expressed as a percentage. Cache-read input tokens are ~10x cheaper than fresh/
    creation input tokens, so a higher rate is a direct Brush token-economics win.

    Returns a data_gap envelope (never a fabricated 0%) when no transcripts or no
    usage blocks are found in the scanned window."""
    now = time.monotonic()
    if (_CACHE_HIT_CACHE["v"] is not None
            and now - _CACHE_HIT_CACHE["t"] < _SCOUT_CACHE_TTL_SEC):
        return _CACHE_HIT_CACHE["v"]
    projects_dir = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".claude" / "projects"
    if not projects_dir.exists():
        result = {"val": None, "data_gap": True, "calibrated": True}
        _CACHE_HIT_CACHE.update(t=now, v=result)
        return result
    jsonls = sorted(projects_dir.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime)[-_CACHE_HIT_SCAN_FILES:]
    read_sum = creation_sum = input_sum = 0
    for jl in jsonls:
        try:
            with open(jl, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if '"usage"' not in line:  # cheap pre-filter before json.loads
                        continue
                    try:
                        entry = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(entry, dict) or entry.get("type") != "assistant":
                        continue
                    usage = (entry.get("message") or {}).get("usage")
                    if not isinstance(usage, dict):
                        continue
                    r = usage.get("cache_read_input_tokens")
                    c = usage.get("cache_creation_input_tokens")
                    i = usage.get("input_tokens")
                    if isinstance(r, (int, float)) and not isinstance(r, bool):
                        read_sum += r
                    if isinstance(c, (int, float)) and not isinstance(c, bool):
                        creation_sum += c
                    if isinstance(i, (int, float)) and not isinstance(i, bool):
                        input_sum += i
        except OSError:
            continue
    denom = read_sum + creation_sum + input_sum
    if denom <= 0:
        # No usage blocks in the scanned window — a genuine data gap (no transcripts
        # instrumented yet, or none touched recently), never a fabricated 0%.
        result = {"val": None, "data_gap": True, "calibrated": True}
    else:
        result = {"val": round(100 * read_sum / denom, 1), "calibrated": True}
    _CACHE_HIT_CACHE.update(t=now, v=result)
    return result


def r_skill_routing_adherence(recs):  # noqa: ARG001
    """Sword — % of critical-work prompts routed through their governing skill.

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


_DEAD_RULE_CACHE: dict = {"t": 0.0, "v": None}


def _dead_rule_count(records: list[dict]) -> dict:  # noqa: ARG001
    """AUTO-014 (Dead-Rule Detection): how many governance anti-pattern rules have NOT
    fired within the retirement window — cruft rules that scan every session but never
    catch anything. A direct Brush architecture-hygiene signal (dead rules are dead
    weight to retire).

    Reads the canonical rule set and the real firing log straight from the config
    tier's principle_audit.py (~/.claude/scripts): its PATTERNS list is the full
    denominator and ~/.claude/data/principle_violations.jsonl records every hit with a
    `ts`. A rule absent from the last RETIREMENT_WINDOW_DAYS of hits is "dead" — exactly
    the retirement-candidate set `principle_audit.py --retirement` prints. This mirrors
    cmd_retirement byte-for-byte (same cutoff, same `ts`>=cutoff test, same skip-on-
    error) so the metric can never disagree with the canonical tool. Importing the
    module for PATTERNS (rather than hardcoding the rule ids) means the count tracks the
    audit's own rule set and can never silently drift when a rule is added or removed.

    Ignores `records` (the telemetry stream carries no rule-firing data) and reads the
    source directly with a short TTL cache. Returns a data_gap envelope — never a
    fabricated 0 — when the config tier, its rule set, or its violation log is
    unreadable, so an absent source grades SIMULATED instead of faking "0 dead rules"."""
    now = time.monotonic()
    if (_DEAD_RULE_CACHE["v"] is not None
            and now - _DEAD_RULE_CACHE["t"] < _SCOUT_CACHE_TTL_SEC):
        return _DEAD_RULE_CACHE["v"]

    def _gap() -> dict:
        r = {"val": None, "data_gap": True, "calibrated": True}
        _DEAD_RULE_CACHE.update(t=now, v=r)
        return r

    scripts_dir = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".claude" / "scripts"
    if not scripts_dir.exists():
        return _gap()
    import sys
    # Put scripts_dir on sys.path only for this import, then take it back off.
    # Leaving ~/.claude/scripts (and its sibling ~/.claude) behind permanently
    # shadows other imports for the rest of the process: ~/.claude/execution is a
    # REGULAR package, so it beats a platform's namespace-portion `execution` even
    # when that platform's root is inserted at sys.path[0] -- which broke
    # load_verifiers("claude") with a bogus ModuleNotFoundError.
    saved_path = list(sys.path)
    sys.path.insert(0, str(scripts_dir))
    try:
        import principle_audit as pa  # canonical rule set + violation log paths
    except Exception:
        return _gap()
    finally:
        sys.path[:] = saved_path
    patterns = getattr(pa, "PATTERNS", None)
    violations_file = getattr(pa, "VIOLATIONS_FILE", None)
    window_days = getattr(pa, "RETIREMENT_WINDOW_DAYS", 90)
    if not patterns or violations_file is None:
        return _gap()
    rule_ids = [p.get("rule_id") for p in patterns if isinstance(p, dict) and p.get("rule_id")]
    if not rule_ids:
        return _gap()
    if not Path(violations_file).exists():
        return _gap()
    cutoff = datetime.now() - timedelta(days=window_days)
    seen: set[str] = set()
    try:
        with open(violations_file, encoding="utf-8", errors="ignore") as f:
            for line in f:
                try:
                    v = json.loads(line)
                    if datetime.fromisoformat(v["ts"]) >= cutoff:
                        seen.add(v["rule_id"])
                except Exception:
                    continue
    except OSError:
        return _gap()
    dead = sorted(rid for rid in rule_ids if rid not in seen)
    result = {"val": len(dead), "calibrated": True,
              "detail": f"{len(dead)}/{len(rule_ids)} rules dead ({window_days}d, 0 hits): "
                        + (", ".join(dead) or "none")}
    _DEAD_RULE_CACHE.update(t=now, v=result)
    return result


_MTTH_CACHE: dict = {"t": 0.0, "v": None}


def _mean_time_to_heal(records: list[dict]) -> dict:  # noqa: ARG001
    """AUTO-018 (Mean Time to Heal): mean elapsed seconds from a reflex's FIRST
    autonomous remediation fire to the FIRST fire that actually healed its target
    (improved=true), read directly from Order Samurai's state/exec_log.jsonl.

    This measures the REMEDIATION-effort span, NOT detection-to-heal latency: exec_log
    records the moment a remediation ran, not the moment the metric first went bad (no
    such detection timestamp is logged anywhere on this host), so the clock starts at
    the first remediation attempt for a reflex_id, not at the degradation itself. The
    metric is named for what it truly measures.

    Per reflex_id: sort its events by timestamp, take the first event's time as the
    start and the first event with improved==True as the heal; the span is their
    difference in seconds (0 when a reflex heals on its first attempt). Reflexes that
    never reached improved==True are STILL OPEN and are EXCLUDED from the mean (never
    counted as 0 — that would fabricate a heal that never happened). If no reflex_id
    has ever healed, returns a data_gap envelope, never a fabricated duration.

    Mirrors _self_correction_rate / _cache_hit_rate: it ignores the `records` arg
    (which carries no per-event reflex_id/improved timing) and reads the source file
    directly with a short TTL cache so repeated aggregate() calls don't re-scan."""
    now = time.monotonic()
    if (_MTTH_CACHE["v"] is not None
            and now - _MTTH_CACHE["t"] < _SCOUT_CACHE_TTL_SEC):
        return _MTTH_CACHE["v"]
    exec_log = _ORDER_SAMURAI_ROOT / "state" / "exec_log.jsonl"
    if not exec_log.exists():
        result = {"val": None, "data_gap": True, "calibrated": True}
        _MTTH_CACHE.update(t=now, v=result)
        return result
    by_reflex: dict[str, list[tuple[datetime, bool]]] = defaultdict(list)
    try:
        with open(exec_log, encoding="utf-8", errors="ignore") as f:
            for line in f:
                if '"reflex_id"' not in line:  # cheap pre-filter before json.loads
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if not isinstance(entry, dict):
                    continue
                rid = entry.get("reflex_id")
                ts = entry.get("timestamp")
                if not rid or not ts or "improved" not in entry:
                    continue
                try:
                    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    continue
                by_reflex[rid].append((dt, entry.get("improved") is True))
    except OSError:
        result = {"val": None, "data_gap": True, "calibrated": True}
        _MTTH_CACHE.update(t=now, v=result)
        return result
    spans: list[float] = []
    for events in by_reflex.values():
        events.sort(key=lambda e: e[0])
        start = events[0][0]
        heal = next((dt for dt, improved in events if improved), None)
        if heal is not None:
            spans.append(max(0.0, (heal - start).total_seconds()))
    if not spans:
        # No reflex has ever reached improved=true — nothing has healed yet, so there
        # is no real duration to report (never a fabricated 0).
        result = {"val": None, "data_gap": True, "calibrated": True}
    else:
        result = {"val": round(sum(spans) / len(spans)), "calibrated": True,
                  "detail": f"{len(spans)} reflex(es) healed; mean first-fire→first-heal span (s)"}
    _MTTH_CACHE.update(t=now, v=result)
    return result


_COMPACTION_CACHE: dict = {"t": 0.0, "v": None}


def _compaction_events(records: list[dict]) -> dict:  # noqa: ARG001
    """AUTO-010 (Compaction Events): count of REAL context-compaction events, read
    directly from Claude Code session transcripts (~/.claude/projects/**/*.jsonl).

    Compaction has no telemetry emitter on this host — the `records` stream every
    other reducer here consumes never carries it — so, like _cache_hit_rate, this
    reducer ignores `records` and scans the transcripts directly, with a short TTL
    cache so repeated aggregate() calls in one refresh don't re-scan.

    UNLIKE _cache_hit_rate/_agent_spawn_events, this scans the FULL transcript
    corpus rather than a bounded recent-file window: compact_boundary is a RARE
    event (measured 2026-07-16: exactly 2 real events across 843 transcripts, at
    recency ranks 93 and 254 — a 40-60 file recent-window, the house convention for
    high-frequency signals like cache reads, would silently miss BOTH and always
    read 0, which is worse than a small honest count). A cheap substring
    pre-filter keeps the full scan fast, so the TTL cache exists only to dedupe
    repeat calls within one refresh, not to bound cost.

    The pre-filter runs over the WHOLE FILE AS BYTES, not per decoded line, and
    that distinction is essentially this reducer's entire cost. The corpus is
    append-only and unbounded: it grew from the 843 files / ~0.36s originally
    recorded here to 2,440 files / 1.4 GB / 4.76s by 2026-08-16 — a 10x regression
    with no code change, purely data growth. Decoding 1.4 GB of UTF-8 into lines to
    find a string that occurs 5 times is the expensive part; `needle in blob` is one
    C-level scan that skips almost every file whole, and only the few that match get
    split and decoded. Measured on this host 2026-08-16: 4.76s -> 0.65s, identical
    count. Keep the byte-level pre-filter if you touch this.

    A compaction event is a STRUCTURAL record: {"type": "system", "subtype":
    "compact_boundary", ...}. This must NOT be a substring/prose match — a naive
    regex for "compact" hits hundreds of unrelated lines where sessions merely
    DISCUSS compaction (this very metric's own name, quoted back in past
    governance transcripts). Only the structural type+subtype pair is counted.

    Returns a data_gap envelope (never a fabricated 0) only when the projects
    directory itself is missing/unreadable. A genuine full-corpus count of 0 (or
    2, or any small number) is an honest real measurement, not a data gap."""
    now = time.monotonic()
    if (_COMPACTION_CACHE["v"] is not None
            and now - _COMPACTION_CACHE["t"] < _SCOUT_CACHE_TTL_SEC):
        return _COMPACTION_CACHE["v"]
    projects_dir = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".claude" / "projects"
    if not projects_dir.exists():
        result = {"val": None, "data_gap": True, "calibrated": True}
        _COMPACTION_CACHE.update(t=now, v=result)
        return result
    jsonls = list(projects_dir.rglob("*.jsonl"))
    needle = b'"compact_boundary"'
    count = 0
    for jl in jsonls:
        try:
            with open(jl, "rb") as f:
                blob = f.read()
        except OSError:
            continue
        if needle not in blob:
            # One C-level scan rejects the whole file. The vast majority of the
            # corpus exits here without ever being decoded or split.
            continue
        for raw in blob.split(b"\n"):
            if needle not in raw:
                continue
            try:
                entry = json.loads(raw.decode("utf-8", errors="ignore"))
            except Exception:
                continue
            if (isinstance(entry, dict) and entry.get("type") == "system"
                    and entry.get("subtype") == "compact_boundary"):
                count += 1
    result = {"val": count, "calibrated": True,
              "detail": f"{count} compact_boundary event(s) across {len(jsonls)} scanned transcripts"}
    _COMPACTION_CACHE.update(t=now, v=result)
    return result
_DAEMON_LOG_LINE_RE = re.compile(r"^(?P<ts>\S+)\s+(?P<service>[\w.-]+):\s*(?P<message>.*)$")


def _daemon_restart_count(records: list[dict]) -> dict:  # noqa: ARG001
    """AUTO-012 (Daemon Restart Count): count of successful autonomic-daemon restarts,
    read from ~/.claude/scripts/service_supervisor.py's own log
    (~/.claude/data/service_supervisor.log). Prior cycles only scanned
    state/autonomic_events.jsonl, which the supervisor never writes to — this is the
    real, previously-unwired source (BLOCKER LIFTED 2026-07-16).

    Each line is `<ISO-ts> <service>: <message>`. A restart counts when the message
    is "restart successful"; lines that don't match the expected shape are skipped
    as malformed rather than crashing the reducer. Failed-spawn attempts ("spawn
    failed — ...") are real supporting signal surfaced in the detail string, but are
    NOT folded into the graded count — a successful restart and a failed spawn are
    different events.

    Returns a data_gap envelope (never a fabricated 0) when the log file doesn't
    exist yet. An existing log with zero restart lines is an honest 0, not a gap."""
    home = Path(os.environ.get("USERPROFILE", str(Path.home())))
    log_path = home / ".claude" / "data" / "service_supervisor.log"
    if not log_path.exists():
        return {"val": None, "data_gap": True, "calibrated": True}
    try:
        text = log_path.read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        return {"val": None, "error": f"source unavailable: {e}", "calibrated": False}

    restarts = 0
    failed_spawns = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _DAEMON_LOG_LINE_RE.match(line)
        if not m:
            continue  # malformed line — skipped, not counted either way
        message = m.group("message")
        if "restart successful" in message:
            restarts += 1
        elif "spawn failed" in message:
            failed_spawns += 1

    return {
        "val": restarts,
        "calibrated": True,
        "detail": f"{restarts} successful restarts, {failed_spawns} failed spawn attempts",
    }


REGISTRY: list[tuple[str, str, str, Callable | None, str, bool, bool]] = [
    ("bow", "Activity", "Error_Rate", r_error_rate, "DERIVED", True, False),
    # Latency_P50 consolidated into Latency_P95 (2026-07-08 audit): the median was
    # dominated by the claude emitter's constant zeros (S2) and measured platform
    # mix, not speed. Re-add only after the claude emitter reports real latency.
    ("bow", "Activity", "Latency_P95", r_lat(95), "DERIVED", False, False),
    ("bow", "Activity", "Complexity_Weighted_Throughput", r_complexity_weighted_throughput, "DERIVED", False, False),
    ("bow", "Activity", "Tool_Calls", r_tool_volume, "DERIVED", False, True),
    # RETIRED 2026-07-08 audit (removal, never faking — Anti_Slop_Score precedent):
    # - Fallback_Recovery_Rate: gateway_falls.jsonl never emitted on this host; the
    #   S1 bug had it grading as live perfect health. Re-add when the gateway
    #   emits falls again.
    # - Agent_Autonomy_Ratio: only the reflex engine writes exec_log.jsonl, so
    #   numerator ≡ denominator — a structural 100.0 carrying zero information.
    #   Rebuild as autonomous-vs-interactive *sessions* from telemetry if wanted.
    # - Processes_Reaped: no reaper ported to Mac; permanently simulated.
    ("bow", "Activity", "Session_Count", r_session_count, "DERIVED", False, True),
    ("bow", "Activity", "Avg_Session_Turns", r_avg_session_turns, "DERIVED", False, False),
    # Per=session counters sum over the SAME record window the Session_Count
    # denominator uses (audit S6): a weekly-only numerator divided by the 7d/30d/
    # all-time session count graded identical behavior up to 4.7x differently
    # across the dashboard's window toggle. The weekly radar still gets weekly
    # sums naturally — build_pillars receives week-scoped records there.
    # (The *_Lifetime twins were misnamed window sums duplicating the window
    # toggle — consolidated away, 2026-07-08 audit.)
    ("sword", "Governance", "Rule_Violations", r_sum_field("rule_violations"), "DERIVED", False, True),
    # Skill_Routing_Adherence: % of critical-work prompts (router-hook detections)
    # whose governing skill was actually invoked that session. The honor-system
    # "use the skill, don't hand-roll it" rule made measurable. SIMULATED until the
    # UserPromptSubmit router hook logs its first detection. Target >= 80%.
    ("sword", "Governance", "Skill_Routing_Adherence", r_skill_routing_adherence, "AUTO", True, False),
    # Governance_Work_Volume: the paired VOLUME signal (backlog P1, 2026-07-19) —
    # critical-work detections counted whether or not the skill fired, so
    # hand-rolled/cross-runtime work is credited. Direction-neutral activity
    # count (no dir rule -> never graded); reads together with the adherence %.
    ("sword", "Governance", "Governance_Work_Volume", r_governance_work_volume, "AUTO", False, True),
    # Vulnerability_MTTR retired 2026-07-11 — Open_CVEs (scout-injected, graded)
    # is the honest CVE-exposure metric; see the reducer-site comment.
    ("sword", "Code Security", "Boundary_Violations", None, "AUTO", False, True),
    ("brush", "Token Efficiency", "Total_Cost", r_total_cost, "DERIVED", False, False),
    ("brush", "Token Efficiency", "Token_Spend", r_token_spend, "DERIVED", False, True),
    ("brush", "Token Efficiency", "Cost_Per_Task", r_cost_per_task, "DERIVED", False, False),
    ("brush", "Token Efficiency", "Cost_Per_Outcome", _cost_per_outcome, "AUTO", False, False),
    ("brush", "Token Efficiency", "Token_Execution_Density", r_token_density, "DERIVED", False, False),
    ("brush", "Token Efficiency", "Local_Routing_Share", r_local_routing, "DERIVED", True, False),
    # AUTO-009: Cache Hit Rate — prompt-cache reuse read directly from Claude Code
    # session transcripts (usage.cache_read_input_tokens); see _cache_hit_rate.
    ("brush", "Token Efficiency", "Cache_Hit_Rate", _cache_hit_rate, "AUTO", True, False),
    # Context pressure — sessions whose max context exceeded ~140k tokens (absolute, window-
    # agnostic; models here are ~1M-window). Reads transcripts directly like Cache_Hit_Rate.
    ("brush", "Token Efficiency", "Context_Cliff_Events", r_context_cliff_events, "DERIVED", True, False),
    # AUTO-010: Compaction Events — count of real compact_boundary records read
    # directly from session transcripts (structural match, never a prose regex);
    # see _compaction_events. A waste signal (more compaction = more context lost).
    ("brush", "Token Efficiency", "Compaction_Events", _compaction_events, "AUTO", False, True),
    ("brush", "Code Health", "Revision_Ratio", r_revision_ratio, "DERIVED", True, False),
    ("brush", "Code Health", "Dead_Rule_Count", _dead_rule_count, "AUTO", False, True),
    ("brush", "Code Health", "Unbounded_Wait_Count", _unbounded_wait_count, "AUTO", False, True),
    ("brush", "Orchestration", "Subagent_Efficiency_Index", _subagent_efficiency_index, "DERIVED", False, False),
    ("brush", "Orchestration", "MCP_vs_CLI_Ratio", r_mcp_vs_cli, "DERIVED", True, False),
    ("brush", "Architecture", "Architecture_Scorecard_Grade", None, "AUTO", False, False),
    ("arts", "Output Quality", "Slop_Density", r_slop_density, "DERIVED", False, False),
    # Tool-use quality triad — 3 SEPARATE llm-judged scores (never blended). Values come from
    # tool_quality_scout.py (offline); SIMULATED until the scout first writes state/tool_quality.json.
    ("arts", "Output Quality", "Tool_Selection_Accuracy", r_tool_selection, "LLM-JUDGED", True, False),
    ("arts", "Output Quality", "Tool_Arg_Correctness", r_tool_args, "LLM-JUDGED", True, False),
    ("arts", "Output Quality", "Tool_Response_Utilization", r_tool_util, "LLM-JUDGED", True, False),
    # Faithfulness / refusal (M3) — same offline scout + state file; SIMULATED until it runs.
    ("arts", "Output Quality", "Faithfulness_Score", r_faithfulness, "LLM-JUDGED", True, False),
    ("arts", "Output Quality", "Refusal_Appropriateness", r_refusal_appropriateness, "LLM-JUDGED", True, False),
    # Retrieval relevance (M4) — seed-set Qdrant benchmark; SIMULATED until the scout runs.
    ("arts", "Knowledge", "Retrieval_Relevance", r_retrieval_relevance, "LLM-JUDGED", True, False),
    ("arts", "Interaction", "Frustration_Signals", r_sum_field("frustration_signals"), "DERIVED", False, True),
    ("arts", "Interaction", "Rework_Loops", r_sum_field("rework_turns"), "DERIVED", False, True),
    # Stop_Hook_Loops: assistant self-repetition against a stuck Stop/goal hook (the 2026-07-07
    # repetition-waste failure mode). Source field emitted by the stop-hook-breaker hook (CP-9,
    # staged) — no-signal (None) until that emitter lands, exactly like other pending-emitter rows.
    ("arts", "Interaction", "Stop_Hook_Loops", r_sum_field("stop_hook_refires"), "DERIVED", False, True),
    # Simplify_Runs consolidated into Simplify_Age (2026-07-08 audit): runs counted
    # its own remediation's invocations (passed with a single run — information-
    # free); age is the sharper recency signal for the same practice.
    ("arts", "Process", "Simplify_Age", r_simplify_age, "DERIVED", False, False),
    ("arts", "Docs", "Doc_Parity_Issues", None, "AUTO", False, True),
    # Tool_Failure_Rate removed — "tool_failure_count" is not in the telemetry schema so this
    # was always SIMULATED. Re-add when the emitter populates tool_failure_count.
    # Guardrail_Blocks RETIRED 2026-07-19: its emitter (security_gate_log.jsonl) has no
    # writer on this host — Windows-era gate log that never migrated. Re-introduce only
    # together with a real block-logger in the live guardrails hook (release lane).
    ("brush", "Orchestration", "Chain_Depth_Avg", r_chain_depth_avg, "DERIVED", False, False),
    ("sword", "Governance", "Governance_Review_Findings", None, "AUTO", False, True),
    ("sword", "Governance", "Kill_Chains_Disrupted", _kill_chains_disrupted, "AUTO", False, True),
    # Kill_Chains_Detected consolidated into Kill_Chains_Open (2026-07-08 audit):
    # detected/disrupted were two ungraded siblings of one concept; the actionable
    # graded number is their gap (chains detected but not yet disrupted).
    ("sword", "Governance", "Kill_Chains_Open", _kill_chains_open, "AUTO", False, True),
    ("bow", "Activity", "Estimated_Agent_Time_Saved", _estimated_agent_time_saved, "AUTO", False, False),
    ("bow", "Autonomic", "Self_Correction_Rate", _self_correction_rate, "AUTO", True, False),
    ("bow", "Autonomic", "Remediation_Delta", _remediation_delta, "AUTO", False, False),
    ("bow", "Autonomic", "Mitigation_Route_Validity", _mitigation_route_validity, "AUTO", True, False),
    # AUTO-018: Mean Time to Heal — mean seconds from a reflex's first remediation
    # fire to its first improved=true fire, read from state/exec_log.jsonl; still-open
    # reflexes excluded. Lower is better. See _mean_time_to_heal.
    ("bow", "Autonomic", "Mean_Time_To_Heal", _mean_time_to_heal, "AUTO", False, False),
    ("bow", "Autonomic", "Mechanism_Liveness", _mechanism_liveness, "AUTO", False, True),
    # AUTO-012: Daemon Restart Count — real source is service_supervisor.py's own
    # log (~/.claude/data/service_supervisor.log), never state/autonomic_events.jsonl.
    # See _daemon_restart_count.
    ("bow", "Autonomic", "Daemon_Restart_Count", _daemon_restart_count, "AUTO", False, True),
    # AUTO-017: Lesson Graduation Rate — real skill-lesson ledger
    # (~/.claude/data/skill_improve_queue.jsonl) vs. proven-effective graduation
    # classification (~/.claude/data/auto_eureka_skills.md RULE section); see
    # _lesson_graduation_rate.
    ("bow", "Agent Operation", "Lesson_Graduation_Rate", _lesson_graduation_rate, "AUTO", True, False),
    ("brush", "Token Efficiency", "Estimated_Cost_Savings", _estimated_cost_savings, "AUTO", False, False),
    ("arts", "Craft", "Craft_Improvements", _craft_improvements, "AUTO", False, True),
    # Arts hero (estimate by design — real counts x seed coefficients, see reducer)
    ("arts", "Craft", "Estimated_Human_Time_Saved", _estimated_human_time_saved, "AUTO", False, False),
    ("sword", "Governance", "Pending_Chain_Proposals", _pending_chain_proposals, "AUTO", False, True),
    ("sword", "Governance", "Verifier_Falsifiability", _verifier_falsifiability, "AUTO", True, False),
]


def _env(val, tier, *, is_percent=False, is_count=False, simulated=False):
    calibrated = True
    delta = "0"
    data_gap = False
    detail = None
    estimate_by_design = False

    if isinstance(val, dict):
        calibrated = val.get("calibrated", True)
        delta = str(val.get("week_delta", "0"))
        data_gap = val.get("data_gap", False)
        detail = val.get("detail")
        estimate_by_design = val.get("estimate_by_design", False)
        if val.get("error"):
            simulated = True
            val = None
        else:
            val = val.get("val")

    trend = "neutral"
    if delta != "0":
        try:
            d_val = float(delta)
            if d_val > 0:
                trend = "up"
            elif d_val < 0:
                trend = "down"
        except ValueError:
            pass

    env = {
        "val": ("—" if val is None else str(val)),
        "delta": delta,
        "trend": trend,
        "history": [],
        "is_percent": is_percent,
        "is_count": is_count,
        "is_simulated": simulated,
        "tier": tier,
        "timestamp": "",
        "calibrated": calibrated,
    }
    if data_gap:
        env["data_gap"] = True
    if detail:
        env["detail"] = detail
    if estimate_by_design:
        # permanent honest "est." badge (real counts x asserted coefficients,
        # no sample source) — distinct from calibrated=False "awaiting samples"
        env["estimate_by_design"] = True
    return env


def _set(pillars, pillar, group, key, env):
    validate_metric(env)
    pillars[pillar].setdefault(group, {})[key] = env


def derive_verifier_metrics(results: list[dict]) -> dict:
    """Real, AUTO metrics from a platform's verifier results (label substring mapping)."""
    total = len(results)
    ok = sum(1 for r in results if r["status"] == "OK")

    def fails(*subs):
        return sum(1 for r in results if r["status"] == "FAIL"
                   and any(s in r["label"].lower() for s in subs))

    return {
        "Governance_Pass_Rate": round(100 * ok / total, 1) if total else None,
        "Verifier_Failures": sum(1 for r in results if r["status"] == "FAIL"),
        "Boundary_Violations": fails("boundary", "archive"),
        "Hardcoded_Path_Incidents": fails("path-authority", "hardcoded"),
        "Root_Hygiene_Issues": sum(1 for r in results if r["status"] != "OK" and "hygiene" in r["label"].lower()),
        "Config_Drift": fails("drift", "anti_drift"),
    }


def build_pillars(records: list[dict], *, verifier_results: list[dict] | None = None,
                  orphan_count: int | None = None, secret_fails: int | None = None,
                  security_signals: dict | None = None,
                  knowledge_signals: dict | None = None) -> dict:
    pillars: dict[str, dict] = {p: {} for p in PILLARS}
    for pillar, group, key, fn, live_tier, is_pct, is_cnt in REGISTRY:
        val = fn(records) if fn else None
        # Tier honesty for dict reducers (audit S1): unwrap BEFORE the simulated
        # check. A dead-source reducer returning {"val": None} without an "error"
        # key must grade SIMULATED — never as a live metric, where _health(None)
        # would inject a perfect 100 into the pillar score.
        inner = val.get("val") if isinstance(val, dict) else val
        simulated = inner is None
        env = _env(val, "SIMULATED" if simulated else live_tier,
                   is_percent=is_pct, is_count=is_cnt, simulated=simulated)
        validate_metric(env)  # tier-honesty contract
        pillars[pillar].setdefault(group, {})[key] = env

    # verifier-derived (real, AUTO) — overwrite SIMULATED placeholders where we have data.
    # Verifier_Failures consolidated into Governance_Pass_Rate (2026-07-08 audit):
    # both rows read the same verifier results (rate = OK/total); the failing-
    # platform drill-down now rides on the survivor's envelope.
    if verifier_results:
        vm = derive_verifier_metrics(verifier_results)
        _set(pillars, "bow", "Governance", "Governance_Pass_Rate", _env(vm["Governance_Pass_Rate"], "AUTO", is_percent=True))
        failing_platforms = sorted({
            str(r.get("platform")) for r in verifier_results
            if r.get("status") == "FAIL" and r.get("platform")
        })
        if failing_platforms:
            gpr = pillars["bow"]["Governance"]["Governance_Pass_Rate"]
            gpr["failure_platforms"] = failing_platforms
            gpr["mitigation_command"] = f"python -m agentica_core.doctor {failing_platforms[0]}"
            gpr["mitigation_skill"] = "doctor"

        _set(pillars, "sword", "Code Security", "Boundary_Violations", _env(vm["Boundary_Violations"], "AUTO", is_count=True))
        _set(pillars, "brush", "Code Health", "Hardcoded_Path_Incidents", _env(vm["Hardcoded_Path_Incidents"], "AUTO", is_count=True))
        _set(pillars, "brush", "Code Health", "Root_Hygiene_Issues", _env(vm["Root_Hygiene_Issues"], "AUTO", is_count=True))
    if orphan_count is not None:
        _set(pillars, "bow", "Autonomic", "Agent_Process_Count", _env(orphan_count, "AUTO", is_count=True))
    if secret_fails is not None:
        _set(pillars, "sword", "Code Security", "Secrets_Detected", _env(secret_fails, "AUTO", is_count=True))
    # security telemetry the hooks already emit (read from <runtime>/data); converts SIMULATED -> AUTO
    # knowledge-layer scouts (platform-independent, AUTO) — arts/Knowledge group
    if knowledge_signals:
        k = knowledge_signals
        if "okf_conformance_pct" in k:
            _set(pillars, "arts", "Knowledge", "OKF_Conformance", _env(k["okf_conformance_pct"], "AUTO", is_percent=True))
        if "orphan_concepts" in k:
            _set(pillars, "arts", "Knowledge", "Orphan_Concepts", _env(k["orphan_concepts"], "AUTO", is_count=True))
        if "archive_ratio_pct" in k:
            _set(pillars, "arts", "Knowledge", "Archive_Ratio", _env(k["archive_ratio_pct"], "AUTO", is_percent=True))
        if "index_drift" in k:
            _set(pillars, "arts", "Knowledge", "Index_Drift", _env(k["index_drift"], "AUTO", is_count=True))
        if "knowledge_staleness_days" in k:
            _set(pillars, "arts", "Knowledge", "Knowledge_Staleness_Days", _env(k["knowledge_staleness_days"], "AUTO"))
        # SOJI: vault link-integrity counts (Execution/soji_scan.py -> Data/soji/memory.findings.json)
        if "soji_broken_links" in k:
            _set(pillars, "arts", "Knowledge", "Soji_Broken_Links", _env(k["soji_broken_links"], "AUTO", is_count=True))
        if "soji_orphan_notes" in k:
            _set(pillars, "arts", "Knowledge", "Soji_Orphan_Notes", _env(k["soji_orphan_notes"], "AUTO", is_count=True))

    if security_signals:
        s = security_signals
        # Rule_Violations is now DERIVED from per-session telemetry (see REGISTRY).
        # Removed scout injection: per-session source enables tier/project breakdown.
        # Canary_Failures + Security_Scorecard RETIRED 2026-07-11 (C/D/F plan
        # step 5): dead emitters (no scheduled canary on this host; the Windows
        # scripts-tier scorecard job is gone) whose content is covered by
        # Gate_Canary_Fault, Guardrail_Blocks, and Secrets_Detected.
        if "gate_canary_fault" in s:
            _set(pillars, "sword", "Audit Trail", "Gate_Canary_Fault", _env(s["gate_canary_fault"], "AUTO", is_count=True))
        # Loop_Breaker_Fires RETIRED 2026-07-19 (metric-surface review Part E
        # item 3): loop_breaker_state.json is never written on this host — the
        # scout emitter never fired, so both injections (sword/Reliability and
        # bow/Reliability) were dead. Removal, never faking.
        if "mechanism_orphans" in s:
            _set(pillars, "bow", "Autonomic", "Mechanism_Orphans", _env(s["mechanism_orphans"], "AUTO", is_count=True))
        # Scheduled_Job_Failures: "is the fleet actually succeeding?", which no other
        # metric asked. Mechanism_Orphans covers wiring, Mechanism_Liveness counts runs,
        # and the launchd_stale check uses log mtime — blind to a job that runs on time
        # and fails, because that job writes its log too. See insights.METRIC_CONFIG.
        if "scheduled_job_failures" in s:
            _set(pillars, "bow", "Autonomic", "Scheduled_Job_Failures", _env(s["scheduled_job_failures"], "AUTO", is_count=True))
        if "doc_parity_issues" in s:
            _set(pillars, "arts", "Docs", "Doc_Parity_Issues", _env(s["doc_parity_issues"], "AUTO", is_count=True))
        if "scorecard_grade" in s:
            _set(pillars, "brush", "Architecture", "Architecture_Scorecard_Grade", _env(s["scorecard_grade"], "AUTO"))
        # Sword additions
        if "open_cves" in s:
            _set(pillars, "sword", "Vulnerability", "Open_CVEs", _env(s["open_cves"], "AUTO", is_count=True))
        elif s.get("dependency_scanner_failures", 0):
            _set(
                pillars, "sword", "Vulnerability", "Open_CVEs",
                _env(
                    {"val": None, "data_gap": True,
                     "detail": "Dependency vulnerability scan incomplete; zero is not established."},
                    "SIMULATED", is_count=True, simulated=True,
                ),
            )
        if "deprecated_deps" in s:
            _set(pillars, "sword", "Supply Chain", "Deprecated_Deps", _env(s["deprecated_deps"], "AUTO", is_count=True))
        elif s.get("dependency_scanner_failures", 0):
            _set(
                pillars, "sword", "Supply Chain", "Deprecated_Deps",
                _env(
                    {"val": None, "data_gap": True,
                     "detail": "Dependency outdated scan incomplete; zero is not established."},
                    "SIMULATED", is_count=True, simulated=True,
                ),
            )
        # Arts additions
        # Skills_Optimized + Skill_Promotions RETIRED 2026-07-19 (metric-surface
        # review Part E item 3): their JSONL sources are never written on this
        # host — the scout emitters never fired. Removal, never faking.
        if "skill_conflicts" in s:
            _set(pillars, "arts", "Craft", "Skill_Conflicts", _env(s["skill_conflicts"], "AUTO", is_count=True))
        # AUTO-016: Knowledge Prompted — memory_recall autonomic events (knowledge/lessons/
        # context docs surfaced this run), read from Data/telemetry/autonomic_events.jsonl.
        if "knowledge_prompted" in s:
            _set(pillars, "arts", "Knowledge", "Knowledge_Prompted", _env(s["knowledge_prompted"], "AUTO", is_count=True))
        # Secret_Scrubs RETIRED 2026-07-19 (metric-surface review Part E item 3):
        # secret_scrubber.jsonl is absent on this host — Secrets_Detected is the
        # live secrets metric. Removal, never faking.
        if "mcp_smoke_fails" in s:
            _set(pillars, "bow", "Activity", "MCP_Smoke_Fails", _env(s["mcp_smoke_fails"], "AUTO", is_count=True))
        # guardrail_blocks injection removed 2026-07-19 (metric retired — dead emitter).
        # GOVERNANCE-001: adversarial governance code review findings (CRITICAL+HIGH)
        if "governance_findings_total_ch" in s:
            _set(pillars, "sword", "Governance", "Governance_Review_Findings",
                 _env(s["governance_findings_total_ch"], "AUTO", is_count=True))
        # AUTO-001: Config Drift Rate — weekly count of config-file changes
        if "config_drift_rate" in s:
            _set(pillars, "bow", "Governance", "Config_Drift_Rate",
                 _env(s["config_drift_rate"], "AUTO", is_count=True))
        # AUTO-003 (Loop_Breaker_Fires in bow/Reliability) RETIRED 2026-07-19 —
        # see the sword/Reliability retirement note above.
        # AUTO-007: Vulnerability Window — days the system has been exposed to known CVEs
        if "vulnerability_window_days" in s:
            _set(pillars, "sword", "Vulnerability", "Vulnerability_Window_Days",
                 _env(s["vulnerability_window_days"], "AUTO", is_count=False))
        # Kill_Chain_Candidates consolidated into Pending_Chain_Proposals
        # (2026-07-08 audit): the discovery scout WRITES its candidates to
        # proposed_kill_chains.json, so the proposals count already covers them —
        # one "awaiting triage" number. The scout still runs (side effect) via
        # scouts.security_signals; only the duplicate metric row is gone.

    # Knowledge vault health — cross-component integration (Knowledge → arts pillar)
    vault = _vault_health_metrics()
    if vault:
        _set(pillars, "arts", "Knowledge", "Wiki_Health_Score",
             _env(vault["Wiki_Health_Score"], "AUTO"))
        _set(pillars, "arts", "Knowledge", "Wiki_Article_Count",
             _env(vault["Wiki_Article_Count"], "AUTO", is_count=True))
        _set(pillars, "arts", "Knowledge", "Raw_Pending",
             _env(vault["Raw_Pending"], "AUTO", is_count=True))
        _set(pillars, "arts", "Knowledge", "Wiki_Orphans",
             _env(vault["Wiki_Orphans"], "AUTO", is_count=True))
    else:
        for key in ("Wiki_Health_Score", "Wiki_Article_Count", "Raw_Pending", "Wiki_Orphans"):
            _set(pillars, "arts", "Knowledge", key, _env(None, "SIMULATED", is_count=(key != "Wiki_Health_Score"), simulated=True))

    return pillars


# Real project roster lives here; telemetry uses short codes, so a few need aliases.
# Root is configurable, not hardcoded: env override first, else derive from the (already
# configurable) Order Samurai root — Order Samurai is itself a project under Desktop/Projects,
# so its parent IS the roster root. The roster itself stays dynamic (iterdir, below).
_PROJECTS_ROOT = Path(os.environ.get("AGENTICA_PROJECTS_ROOT", str(_ORDER_SAMURAI_ROOT.parent)))
_PROJECT_ALIASES: dict[str, list[str]] = {
    "Jarvis-Intelligence-Hub": ["HUB", "HUD"],
    # "History and read list app" was the old folder name before it was renamed
    # to "Dendrite app". All Codex sessions from May 2026 used the old name.
    "Dendrite app": ["History and read list app"],
}
# Tool-agnostic git telemetry — written by the global post-commit hook.
# These records carry presence + commit stats but NOT session metrics (tokens/cost/latency
# are 0). They are fed to build_project_scores() only — not to build_pillars() — so that
# zeros don't pollute cross-platform metric averages.
_GIT_TELEMETRY = Path.home() / ".agentica" / "git_telemetry.jsonl"


def load_git_records() -> list[dict]:
    """Load git-hook telemetry records. Returns [] if the file does not exist."""
    if not _GIT_TELEMETRY.exists():
        return []
    out: list[dict] = []
    for line in _GIT_TELEMETRY.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = normalize_entry(json.loads(line), platform="git")
            validate_entry(rec)
            out.append(rec)
        except Exception:
            continue
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def build_project_scores(all_records: list[dict], proj_platform: dict[str, str],
                         root: Path = _PROJECTS_ROOT) -> dict:
    """Roster the real project folders in Desktop/Projects, match each to telemetry
    (alias or normalized name match), and return its four itemized pillar scores."""
    by_tproj: dict[str, list] = {}
    for r in all_records:
        pr = r.get("project")
        if pr:
            by_tproj.setdefault(pr, []).append(r)

    folders = sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []
    out: dict[str, dict] = {}
    for f in folders:
        nf = _norm(f)
        aliases = set(_PROJECT_ALIASES.get(f, []))
        recs: list[dict] = []
        plats: Counter = Counter()
        for tp, rs in by_tproj.items():
            ntp = _norm(tp)
            # Exact normalized match or alias-only. Substring was too broad:
            # "api" matched "apify", "hub" matched "github". Require the SHORTER
            # (substring/needle) name to be at least 6 chars in each direction —
            # guarding only ntp let a short folder like "api" still absorb a long
            # telemetry project like "apifyscraper" via `nf in ntp`.
            match = (tp in aliases or ntp == nf
                     or (len(ntp) >= 6 and ntp in nf)
                     or (len(nf) >= 6 and nf in ntp))
            if match:
                recs.extend(rs)
                plats[proj_platform.get(tp, "")] += len(rs)
        if recs:
            pillars = build_pillars(recs)
            scores = insights.annotate(pillars)
            metrics: dict[str, float] = {}
            for groups in pillars.values():
                for group in groups.values():
                    for mk, env in group.items():
                        if env.get("is_simulated"):
                            continue
                        n = insights._num(env.get("val"))
                        if n is not None:
                            metrics[mk] = n
            out[f] = {
                "platform": plats.most_common(1)[0][0] if plats else "",
                "records": len(recs),
                "has_data": True,
                # Pass rate (100 × passing/graded), not a weighted mean — decomposable
                # count ratio per the de-aggregation doctrine (score field removed 2026-07-19).
                "scores": {
                    k: round(100 * v["rollup"]["passing"] / v["rollup"]["graded"], 1)
                    if v["rollup"]["graded"] else 0
                    for k, v in scores.items()
                },
                "metrics": metrics,
                "tier_mix": build_tier_mix(recs),
            }
        else:
            out[f] = {"platform": "", "records": 0, "has_data": False,
                      "scores": {"bow": 0, "sword": 0, "brush": 0, "arts": 0}, "metrics": {}}
        # Read meditation state if present (Order Samurai and future meditation-enabled projects)
        meditation_path = root / f / "state" / "MEDITATION_STATE.json"
        if meditation_path.exists():
            try:
                raw = json.loads(meditation_path.read_text(encoding="utf-8"))
                rpillars = raw.get("pillars", {})
                out[f]["meditation_state"] = {
                    pk: {
                        "ronin_mode": rpillars[pk].get("ronin_mode", "dormant"),
                        "live_current": rpillars[pk].get("live_current"),
                        "live_baseline": rpillars[pk].get("live_baseline"),
                    }
                    for pk in ("bow", "sword", "brush", "arts")
                    if pk in rpillars
                }
            except Exception:
                pass
    return out


def _within_days(ts: str, days: int) -> bool:
    t = parse_ts(ts)
    if t is None:
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t) <= timedelta(days=days)


# Signals that must NOT be summed across platforms when merging security_signals:
# scores (non-additive by nature) and platform-independent scouts that return the
# same value for every platform (summing triple-counts them — Doc_Parity_Issues
# showed 30 while the scout said 10). knowledge_prompted is the same class of bug:
# it reads the single shared autonomic_events.jsonl stream via
# scouts._count_autonomic_events, which ignores runtime_root/platform.
_NON_ADDITIVE_SIG = frozenset({
    "scorecard_grade",
    "doc_parity_issues",
    "governance_findings_critical", "governance_findings_high", "governance_findings_total_ch",
    "knowledge_prompted",
})

# Signals that are genuinely per-platform (unlike _NON_ADDITIVE_SIG) but whose combined
# value across platforms is the worst case, not a total: vulnerability_window_days is
# "age of the longest-open unpatched CVE" (scouts/vulnerability_window.py) — summing two
# platforms' windows (e.g. 10 + 15 = 25) produces a number with no real-world meaning,
# where the true combined exposure window is max(10, 15) = 15.
_MAX_SIG = frozenset({
    "vulnerability_window_days",
})


def aggregate(platforms: list[str] | None = None, timestamp: str | None = None,
              window_days: int = 30,
              write_history: bool = False) -> dict:
    platforms = platforms if platforms is not None else list_platforms()
    # Publish this build's window for the REGISTRY reducers that cannot take it as an
    # argument (see _ACTIVE_WINDOW_DAYS). Set first, before any build_pillars() call.
    global _ACTIVE_WINDOW_DAYS  # noqa: PLW0603
    _ACTIVE_WINDOW_DAYS = window_days
    this_week = iso_week(timestamp) if timestamp else None
    if not this_week:
        this_week = datetime.now(timezone.utc).strftime("%G-W%V")
    per_platform: dict[str, dict] = {}
    per_platform_week: dict[str, dict] = {}   # current-week window → weekly radar
    week_counts: dict[str, int] = {}
    counts: dict[str, int] = {}
    all_records: list[dict] = []
    all_verifier: list[dict] = []
    merged_sig: dict[str, int] = {}
    proj_platform: dict[str, str] = {}
    for p in platforms:
        recs = load_records(p)
        counts[p] = len(recs)
        try:
            vres = [dict(r, platform=p) for r in run_all(load_verifiers(p))]
        except PlatformUnavailable:
            vres = []
        except Exception as exc:
            vres = [{"label": f"verifier-load-error:{type(exc).__name__}", "status": "FAIL", "platform": p}]
        try:
            sig = scouts.security_signals(resolve_platform(p).runtime_root, p)
        except (PlatformUnavailable, OSError):
            sig = {}
        sc_path = _SCORECARDS.get(p)
        if sc_path:
            grade = scouts.score_architecture(vres, sc_path)
            if grade is not None:
                sig["scorecard_grade"] = grade
        for k, v in sig.items():
            if k in _NON_ADDITIVE_SIG:
                merged_sig[k] = v  # scores & platform-independent scouts — never sum
            elif k in _MAX_SIG:
                merged_sig[k] = max(merged_sig[k], v) if k in merged_sig else v
            else:
                merged_sig[k] = merged_sig.get(k, 0) + v
        per_platform[p] = build_pillars(recs, verifier_results=vres, security_signals=sig)
        # weekly radar: telemetry windowed to the current ISO week + current security/governance
        wrecs = [r for r in recs if iso_week(r.get("timestamp", "")) == this_week]
        week_counts[p] = len(wrecs)
        per_platform_week[p] = build_pillars(wrecs, verifier_results=vres, security_signals=sig)
        all_records.extend(recs)
        all_verifier.extend(vres)
        for r in recs:
            pr = r.get("project")
            if pr:
                proj_platform.setdefault(pr, p)

    fails = sum(1 for r in verify_secrets.run_checks() if r["status"] == "FAIL")
    orphans = scouts.agent_process_count()
    # knowledge health is repo-level (identical for every platform) — computed once and
    # passed only to the merged builds, so cross-platform merging can never double-count it.
    ksig = scouts.knowledge_signals()
    # Primary view = trailing-window telemetry + CURRENT security/governance snapshot
    # (security signals are point-in-time, not windowable). Lifetime kept for the UI toggle.
    windowed = [r for r in all_records if _within_days(r.get("timestamp", ""), window_days)]
    combined = build_pillars(windowed, verifier_results=all_verifier,
                             orphan_count=orphans, secret_fails=fails, security_signals=merged_sig,
                             knowledge_signals=ksig)
    lifetime = build_pillars(all_records, verifier_results=all_verifier,
                             orphan_count=orphans, secret_fails=fails, security_signals=merged_sig,
                             knowledge_signals=ksig)
    category_scores_lifetime = insights.annotate(lifetime)

    # analytical layer: scores, remediation, trend history, summaries.
    # populate_history first so summaries can discuss real deltas/outliers.
    category_scores = insights.annotate(combined)
    current = insights.populate_history(combined)
    summaries = insights.build_summaries(combined, category_scores)
    if write_history:
        insights.append_snapshot(
            insights.default_history_path(),
            timestamp or datetime.now(timezone.utc).isoformat(),  # never a week:null row
            current)
        # Anti-gaming guard: record any threshold/METRIC_CONFIG change against the baseline
        # snapshot so a silently-loosened threshold (which would drop the needs-attention count)
        # can never go unrecorded. Gated on write_history so test/dry runs don't mutate state.
        threshold_audit.audit_threshold_changes(insights.METRIC_RULES, now=timestamp or None)

    # per-platform pillar scores for the per-model health radar — WEEKLY window
    # (telemetry scoped to the current ISO week; security/governance are current snapshot)
    by_platform_scores = {p: insights.annotate(pillars) for p, pillars in per_platform_week.items()}

    # per-project pillar scores for the "By Project" section — rostered from the real
    # project folders in Desktop/Projects, matched to telemetry by name/alias.
    # Git records supplement presence detection but are NOT mixed into build_pillars()
    # (their zeros for tokens/cost/latency would skew cross-platform metric averages).
    # proj_platform was already populated during the per-platform loop above — no second read.
    git_recs = load_git_records()
    for r in git_recs:
        pr = r.get("project")
        if pr:
            proj_platform.setdefault(pr, "git")
    windowed_git = [r for r in git_recs if _within_days(r.get("timestamp", ""), window_days)]
    by_project = build_project_scores(windowed + windowed_git, proj_platform)

    # per-tier pillar breakdown — telemetry-only (scouts are point-in-time, not tier-specific
    # so they are SIMULATED in tier views, which is honest).
    tier_names = sorted({r.get("model_tier") for r in windowed if r.get("model_tier")})
    by_tier: dict[str, dict] = {
        tier: build_pillars([r for r in windowed if r.get("model_tier") == tier])
        for tier in tier_names
    }
    by_tier_scores: dict[str, dict] = {
        tier: insights.annotate(pillars)
        for tier, pillars in by_tier.items()
    }

    # Two channels, two keys. `reflexes` is the dispatch channel (dashboard run button,
    # ReflexEngine auto-fire); `advisory_reflexes` carries the real breaches that have no
    # auto-remediation, for investigation-only consumers (sensei). Deliberately NOT merged:
    # a merged list would restore the mis-routed run button SENSEI-3/4 removed.
    live_reflexes, advisory_reflexes = reflexes.build_reflexes(
        combined, category_scores, by_project)

    return {
        "schema_version": SCHEMA_VERSION,
        "timestamp": timestamp or "",
        "platforms": list(platforms),
        "record_counts": counts,
        "window": {"days": window_days, "records": len(windowed)},
        "category_scores": category_scores,
        "category_scores_lifetime": category_scores_lifetime,
        "summaries": summaries,
        "tier_mix": build_tier_mix(windowed),
        "pillars": combined,
        "by_platform": per_platform,
        "by_platform_scores": by_platform_scores,
        "radar_week": {"week": this_week, "records": week_counts},
        "by_project": by_project,
        "by_tier": by_tier,
        "by_tier_scores": by_tier_scores,
        "reflexes": live_reflexes,
        "advisory_reflexes": advisory_reflexes,
        # Windowed (not lifetime) so these sections track the payload's window and the
        # dashboard's 7d/30d/all-time filter instead of showing month-old events forever.
        "remediation_efficacy": remediation.efficacy(records=windowed, window_days=window_days),
        "top_usage": _top_usage(windowed, window_days),
        "architecture": architecture_breakdown(_SCORECARDS.get("claude")),
        # The one legitimate composite — count + decomposed list (never a hero KPI). Built from
        # the same env["status"] the badges use, so the count can't disagree with the surfaces.
        "needs_attention": insights.needs_attention(combined),
    }


def default_payload_path() -> Path:
    return _THIS.parents[2] / "Data" / "wid_payload.json"


# P4: versioned contract for the Python⇄TS seam. The TS reflex-engine validates
# the SAME schema on startup, so both ends enforce one authoritative shape.
_WID_PAYLOAD_SCHEMA_PATH = _THIS.parents[1] / "schema" / "wid_payload.schema.json"


def validate_payload(payload: dict) -> None:
    """Validate `payload` against schema/wid_payload.schema.json.

    Raises jsonschema.ValidationError on the first violation so a malformed
    envelope never reaches disk (fail fast on write).
    """
    schema = json.loads(_WID_PAYLOAD_SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(instance=payload, schema=schema)


def write_payload(payload: dict, path: Path | None = None) -> Path:
    # P4: never persist an envelope that violates the typed contract.
    validate_payload(payload)
    target = path or default_payload_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    # H1: atomic write so the TS reflex-engine (chokidar watcher on this file)
    # cannot read a torn payload mid-write — torn maturity/reflex_ready keys would
    # make the grant decision flip mid-cycle.
    atomic_json_write(target, payload)
    return target


def _summary(payload: dict) -> str:
    live, sim = insights.count_live_sim(payload)
    return f"metrics: {live} LIVE / {sim} SIMULATED | records: {payload['record_counts']}"


def main() -> int:
    # History writes are opt-in (--snapshot). `python -m agentica_core.aggregate`
    # is the documented operator command; when it unconditionally appended a live
    # history row per run, casual invocations crowded the 7-point trend window
    # with intra-day rows — every sparkline, delta, and σ-reflex trigger ended up
    # measuring minutes instead of weeks (2026-07-26 audit). refresh_dashboard.py
    # owns the snapshot cadence (backfill → calibrate → one live row + stamp).
    import sys
    write_history = "--snapshot" in sys.argv
    payload = aggregate(timestamp=datetime.now(timezone.utc).isoformat(),
                        write_history=write_history)
    path = write_payload(payload)
    print(f"Agentica Aggregator -> {path}")
    print(_summary(payload))
    if not write_history:
        print("history: untouched (pass --snapshot to append a history row)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
