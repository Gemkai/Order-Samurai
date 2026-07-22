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
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Any

import jsonschema

_THIS = Path(__file__).resolve()
_local_root = _THIS.parents[1]
if (_local_root / "config").exists() and not (_local_root / "Order Samurai").exists():
    _default_root = _local_root
else:
    _default_root = _local_root / "Order Samurai"
_ORDER_SAMURAI_ROOT = Path(os.environ.get("ORDER_SAMURAI_ROOT", str(_default_root)))

# Optional integration with an external Agentica-OS knowledge vault. Set AGENTICA_OS_ROOT
# to point at it; otherwise resolve relative to this repo (absent on most installs, which the
# tier-honesty layer reports as SIMULATED rather than faking a value).
_agentica_os_env = os.environ.get("AGENTICA_OS_ROOT")
_agentica_os = Path(_agentica_os_env) if _agentica_os_env else None
if _agentica_os and _agentica_os.exists():
    _VAULT_HEALTH_SCRIPT = _agentica_os / "Knowledge" / "vault" / "_scripts" / "vault_health.py"
else:
    _VAULT_HEALTH_SCRIPT = _THIS.parents[2] / "Knowledge" / "vault" / "_scripts" / "vault_health.py"

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

from . import insights, reflexes, remediation, scouts, threshold_audit, verify_secrets
from .atomic import atomic_json_write
from .adapter import PlatformUnavailable, list_platforms, resolve_platform
from .telemetry import (SCHEMA_VERSION, iso_week, normalize_entry, parse_ts,
                        validate_entry, validate_metric)
from .verifiers import load_verifiers, run_all

_THIS = Path(__file__).resolve()
PILLARS = ("bow", "sword", "brush", "arts")
# per-platform architecture scorecard (weighted category rubric)
_local_scorecard = _THIS.parents[1] / "config" / "architecture_scorecard.json"
if _local_scorecard.exists():
    _SCORECARDS = {
        "claude": _local_scorecard,
    }
else:
    _SCORECARDS = {
        "claude": _THIS.parents[2] / "Governance" / "Order Samurai" / "config" / "architecture_scorecard.json",
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
# The real fix is the MIN-SAMPLE GUARD: a window with fewer than MIN_ERROR_SAMPLE records is
# uncalibrated (return None) rather than a false FAIL on noise — otherwise 1 error of 2 records
# reads 50.0 and trips fail=5. None is treated as uncalibrated by the health layer.
ERROR_STATUSES = frozenset({"error"})
MIN_ERROR_SAMPLE = 10

def error_rate_stats(recs) -> tuple[float | None, int, int]:
    """Return (rate_pct | None, error_count, total). rate is None (uncalibrated) when
    total < MIN_ERROR_SAMPLE. Canonical Error_Rate computation — shared with bin/error_triage.py
    (kept in lockstep by tests/test_error_triage.py::test_error_rate_classification_no_drift)."""
    total = len(recs)
    errors = sum(1 for r in recs if str(r.get("status", "")).lower() in ERROR_STATUSES)
    if total < MIN_ERROR_SAMPLE:
        return None, errors, total
    return round(100 * errors / total, 1), errors, total

def r_error_rate(recs): return error_rate_stats(recs)[0]
def r_lat(p): return lambda recs: _pctile(_nums(recs, "latency_ms"), p)
def r_tool_volume(recs):
    vals = _nums(recs, "tool_calls")
    return int(sum(vals)) if vals else None
def r_tool_diversity(recs):
    s = {t for r in recs for t in (r.get("tool_calls_list") or [])}
    return len(s) if s else None
def r_session_count(recs):
    s = {r.get("session_id") for r in recs if r.get("session_id")}
    return len(s) if s else None
def r_avg_session_turns(recs):
    c = Counter(r.get("session_id") for r in recs if r.get("session_id"))
    return round(sum(c.values()) / len(c), 1) if c else None
def r_total_cost(recs):
    vals = _nums(recs, "total_cost")
    return round(sum(vals), 4) if vals else None
def r_token_spend(recs):
    vals_p = _nums(recs, "tokens_prompt")
    vals_c = _nums(recs, "tokens_completion")
    return int(sum(vals_p) + sum(vals_c)) if (vals_p or vals_c) else None
def r_cost_per_task(recs):
    # Only positive-cost records count toward the average. A logged total_cost of 0.0
    # means cost was not attributed for that record (emitter task types like
    # wid_pulse_gen / session), not a genuinely free task — including those zeros in
    # the denominator systematically understates the metric (the documented intent).
    vals = [v for v in _nums(recs, "total_cost") if v > 0]
    return round(sum(vals) / len(vals), 4) if vals else None
def r_token_density(recs):
    succ = sum(1 for r in recs if r.get("status") == "success")
    tot = sum(_nums(recs, "tokens_prompt")) + sum(_nums(recs, "tokens_completion"))
    return round(tot / succ, 1) if succ else None
def r_model_tier_mix(recs):
    c = Counter(r.get("model_tier") for r in recs if r.get("model_tier"))
    if not c:
        return None
    n = sum(c.values())
    return " ".join(f"{k}:{round(100 * v / n)}%" for k, v in c.most_common())


def r_local_routing(recs):
    """Percent of tasks routed to the LOCAL model tier (Ollama). Higher = more work kept
    local/cheap/private per the local-LLM routing policy. Real efficiency signal, not a guess."""
    tiers = [r.get("model_tier") for r in recs if r.get("model_tier")]
    if not tiers:
        return None
    return round(100 * sum(1 for t in tiers if str(t).upper() == "LOCAL") / len(tiers), 1)


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
    sw = sum(_int_vals(recs, "slop_markers"))
    ow = sum(_int_vals(recs, "output_words"))
    return round(sw / ow * 1000, 2) if ow else None


def _sum_per_session(recs, field) -> int:
    """Sum `field` counting each session once (max value seen per session_id).

    These are per-session counters (rule_violations, frustration_signals, …). When a
    session is logged to telemetry more than once the static count is re-emitted, so
    summing raw records double-counts it. Records without a session_id can't be
    deduplicated, so each is counted individually."""
    by_sess: dict = {}
    loose: list[int] = []
    for r in recs:
        v = r.get(field)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        sid = r.get("session_id")
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


def r_sum_field_weekly(field):
    """Current-ISO-week sum. Lifetime-cumulative counters can only grow, which
    makes their reflexes permanently red; the weekly window lets a clean week
    clear them. Records without a parseable timestamp are excluded — never
    guessed into the current week."""
    def f(recs):
        this_week = datetime.now(timezone.utc).strftime("%G-W%V")
        wk_recs = [r for r in recs if iso_week(r.get("timestamp", "")) == this_week]
        # 0 is meaningful here (a clean week), but only when records exist at
        # all — no records this week means "no signal", not "zero violations".
        if _has_field(wk_recs, field):
            return _sum_per_session(wk_recs, field)
        return 0 if wk_recs else None
    return f


def r_simplify_runs(recs):
    if not any("skills_used" in r for r in recs):
        return None
    return sum(1 for r in recs if "simplify" in (r.get("skills_used") or []))


def _last_simplify_commit_ts():
    """Timestamp of the most recent simplify/refactor/cleanup commit in the repo,
    or None if git is unavailable or none exist. Runtime-agnostic OUTCOME signal:
    any agent or a human landing such a commit counts —
    unlike /simplify skill telemetry, which only sees the Claude channel. Mirrors
    _cost_per_outcome's git-log pattern (repo root, timeout, fail-safe). Matches
    conventional-commit subjects starting with refactor/simplif/cleanup."""
    try:
        out = subprocess.run(
            ["git", "-C", str(_ORDER_SAMURAI_ROOT), "log", "-1", "--format=%cI",
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
    commit in git. The commit signal credits hand-rolled work and any other
    runtime that emits no Claude skill telemetry — previously this counted only
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
                samples_by_kind[item.get("kind")].append(duration)
                if earliest_start is None or start < earliest_start:
                    earliest_start = start
                
    total_samples = sum(len(v) for v in samples_by_kind.values())
    thresholds = coef.get("calibration_threshold", {})
    sample_threshold = thresholds.get("samples", 20)
    week_threshold = thresholds.get("weeks")

    enough_samples = total_samples >= sample_threshold
    # Time-bounded fallback: once real samples have been collecting for `weeks`,
    # calibrate from whatever exists rather than waiting for the full sample count.
    # Never fabricates — a kind with zero samples stays on its seed benchmark.
    enough_time = (
        week_threshold is not None
        and total_samples > 0
        and earliest_start is not None
        and (datetime.now(timezone.utc) - earliest_start) >= timedelta(weeks=week_threshold)
    )

    if enough_samples or enough_time:
        via = "samples" if enough_samples else "time"
        # Calibrate operations coefficients
        for kind, values in samples_by_kind.items():
            if kind in coef.get("operations", {}):
                avg = sum(values) / len(values)
                coef["operations"][kind]["benchmark_min"] = avg
                coef["operations"][kind]["calibrated"] = True
                coef["operations"][kind]["sample_count"] = len(values)
                coef["operations"][kind]["calibrated_via"] = via
        
        # Save coefficients back to file atomically (temp + replace)
        try:
            temp_path = coef_path.with_suffix(".tmp")
            temp_path.write_text(json.dumps(coef, indent=2), encoding="utf-8")
            temp_path.replace(coef_path)
        except Exception:
            pass

# ---------------------------------------------------------------------------
# Reducer implementations
# ---------------------------------------------------------------------------

def r_complexity_weighted_throughput(records: list[dict]) -> float | None:
    if not records:
        return None
    total = 0.0
    has_success = False
    for r in records:
        if r.get("status") == "success":
            has_success = True
            tool_calls = r.get("tool_calls", 0)
            if not isinstance(tool_calls, (int, float)) or isinstance(tool_calls, bool):
                tool_calls = 0
            tokens_comp = r.get("tokens_completion", 0)
            if not isinstance(tokens_comp, (int, float)) or isinstance(tokens_comp, bool):
                tokens_comp = 0
            total += 1.0 + (tool_calls * 0.5) + (tokens_comp / 1000.0)
    return round(total, 1) if has_success else 0.0

def _agent_autonomy_ratio(records: list[dict], repo_root: Path | None = None) -> dict:  # noqa: ARG001
    if repo_root is None:
        repo_root = _ORDER_SAMURAI_ROOT
    path = repo_root / "state" / "exec_log.jsonl"
    if not path.exists():
        return {"val": 0.0, "week_delta": 0.0, "calibrated": True}
    try:
        now = datetime.now(timezone.utc)
        this_week = now.strftime("%G-W%V")
        last_week = (now - timedelta(days=7)).strftime("%G-W%V")
        
        this_week_total = 0
        this_week_reflex = 0
        last_week_total = 0
        last_week_reflex = 0
        
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                ts_str = obj.get("timestamp")
                if not ts_str:
                    continue
                dt = _parse_iso(ts_str)
                if not dt:
                    continue
                wk = dt.strftime("%G-W%V")
                
                is_reflex = obj.get("source") == "reflex_engine"
                if wk == this_week:
                    this_week_total += 1
                    if is_reflex:
                        this_week_reflex += 1
                elif wk == last_week:
                    last_week_total += 1
                    if is_reflex:
                        last_week_reflex += 1
            except Exception:
                continue
        
        val = round(100.0 * this_week_reflex / this_week_total, 1) if this_week_total > 0 else 0.0
        last_val = round(100.0 * last_week_reflex / last_week_total, 1) if last_week_total > 0 else 0.0
        week_delta = round(val - last_val, 1)
        return {"val": val, "week_delta": week_delta, "calibrated": True}
    except Exception as e:
        return {"val": None, "error": str(e), "calibrated": False}

def _fallback_recovery_rate(records: list[dict], repo_root: Path | None = None) -> dict:  # noqa: ARG001
    falls_log = Path.home() / ".claude" / "data" / "gateway_falls.jsonl"
    if not falls_log.exists():
        # No fallback log = no evidence, not proven-perfect. Report uncalibrated
        # (consistent with every other absent-source reducer) rather than a false
        # 100% PASS that can never raise an alarm.
        return {"val": None, "week_delta": 0.0, "calibrated": False}
    try:
        now = datetime.now(timezone.utc)
        this_week = now.strftime("%G-W%V")
        last_week = (now - timedelta(days=7)).strftime("%G-W%V")
        
        this_week_success = 0
        this_week_total = 0
        last_week_success = 0
        last_week_total = 0
        
        for line in falls_log.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                ts = obj.get("timestamp")
                if not ts:
                    continue
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                wk = dt.strftime("%G-W%V")
                
                success = obj.get("success", False)
                if wk == this_week:
                    this_week_total += 1
                    if success:
                        this_week_success += 1
                elif wk == last_week:
                    last_week_total += 1
                    if success:
                        last_week_success += 1
            except Exception:
                continue
        
        val = round(100.0 * this_week_success / this_week_total, 1) if this_week_total > 0 else 100.0
        last_val = round(100.0 * last_week_success / last_week_total, 1) if last_week_total > 0 else 100.0
        week_delta = round(val - last_val, 1)
        return {"val": val, "week_delta": week_delta, "calibrated": True}
    except Exception as e:
        return {"val": None, "error": str(e), "calibrated": False}

def _vulnerability_mttr(records: list[dict], repo_root: Path | None = None) -> dict:  # noqa: ARG001
    if repo_root is None:
        repo_root = _ORDER_SAMURAI_ROOT
    paths = _kill_chain_paths(repo_root)
    default_mttr = 1.2

    if not any(p.exists() for p in paths):
        # No event source — the default is a placeholder, never a calibrated reading
        return {"val": default_mttr, "week_delta": 0.0, "calibrated": False}

    try:
        now = datetime.now(timezone.utc)
        this_week = now.strftime("%G-W%V")
        last_week = (now - timedelta(days=7)).strftime("%G-W%V")

        from collections import defaultdict
        chains = defaultdict(list)
        for path in paths:
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    obj = json.loads(line)
                    chain_id = obj.get("chain_id")
                    if chain_id is not None:
                        chains[chain_id].append(obj)
                except Exception:
                    continue
                
        this_week_offsets = []
        last_week_offsets = []
        
        for chain_id, events in chains.items():
            events_sorted = []
            for ev in events:
                ts_str = ev.get("ts") or ev.get("timestamp")
                if ts_str:
                    dt = _parse_iso(ts_str)
                    if dt:
                        events_sorted.append((dt, ev))
            events_sorted.sort(key=lambda x: x[0])
            if not events_sorted:
                continue
                
            detect_time = events_sorted[0][0]
            patch_time = None
            for dt, ev in events_sorted:
                action = ev.get("remediation_action", "").lower()
                ev_type = ev.get("event_type", "").lower()
                if "patch" in action or "remediat" in action or "block" in action or "patch" in ev_type:
                    patch_time = dt
                    break
            if patch_time:
                offset_days = (patch_time - detect_time).total_seconds() / 86400.0
            else:
                # Still open: MTTR grows with time
                offset_days = (now - detect_time).total_seconds() / 86400.0
                
            wk = detect_time.strftime("%G-W%V")
            if wk == this_week:
                this_week_offsets.append(offset_days)
            elif wk == last_week:
                last_week_offsets.append(offset_days)
                
        val = round(sum(this_week_offsets) / len(this_week_offsets), 1) if this_week_offsets else default_mttr
        last_val = round(sum(last_week_offsets) / len(last_week_offsets), 1) if last_week_offsets else default_mttr
        week_delta = round(val - last_val, 1)
        return {"val": val, "week_delta": week_delta, "calibrated": True}
    except Exception as e:
        return {"val": None, "error": str(e), "calibrated": False}

def _subagent_efficiency_index(records: list[dict]) -> float | None:
    if not records:
        return None
    spawn_records = [r for r in records
                     if isinstance(r.get("subagent_spawns"), (int, float))
                     and not isinstance(r.get("subagent_spawns"), bool)
                     and r.get("subagent_spawns", 0) > 0]
    if not spawn_records:
        return 100.0

    # Success rate among sessions that used subagents
    successful = sum(1 for r in spawn_records if r.get("status") == "success")
    ratio = successful / len(spawn_records)

    # Cost penalty: benchmark $5.00 per spawning session (heavy sessions are warranted).
    # Sessions above $5.00 proportionally scale down the score.
    total_cost = sum(r.get("total_cost", 0.0) for r in spawn_records
                     if isinstance(r.get("total_cost"), (int, float))
                     and not isinstance(r.get("total_cost"), bool))
    avg_cost = total_cost / len(spawn_records) if spawn_records else 0.0
    cost_penalty_factor = 1.0
    if avg_cost > 5.0:
        cost_penalty_factor = 5.0 / avg_cost

    return min(100.0, round(100.0 * ratio * cost_penalty_factor, 1))


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


def _count_agent_types() -> Counter:
    """Scan recent session JSONLs to count Agent subagent_type invocations."""
    import json as _json
    projects_dir = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".claude" / "projects"
    counts: Counter = Counter()
    if not projects_dir.exists():
        return counts
    jsonls = sorted(projects_dir.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime)[-40:]
    for jl in jsonls:
        try:
            with open(jl, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    try:
                        entry = _json.loads(line)
                        if entry.get("type") != "assistant":
                            continue
                        for c in (entry.get("message") or {}).get("content") or []:
                            if (isinstance(c, dict) and c.get("type") == "tool_use"
                                    and c.get("name") == "Agent"):
                                st = (c.get("input") or {}).get("subagent_type")
                                if st:
                                    counts[st] += 1
                    except Exception:
                        pass
        except Exception:
            pass
    return counts


def _top_usage(all_records: list[dict]) -> dict:
    skill_counts: Counter = Counter()
    conn_counts: Counter = Counter()
    for r in all_records:
        for s in (r.get("skills_used") or []):
            skill_counts[s] += 1
        for tool in (r.get("tool_calls_list") or []):
            if tool.startswith("mcp__"):
                parts = tool.split("__")
                server = parts[1] if len(parts) > 1 else tool
                conn_counts[_mcp_server_label(server)] += 1

    agent_counts = _count_agent_types()

    return {
        "skills": [{"name": k, "count": v} for k, v in skill_counts.most_common(5)],
        "connections": [{"name": k, "count": v} for k, v in conn_counts.most_common(5)],
        "agents": [{"name": k, "count": v} for k, v in agent_counts.most_common(5)],
    }


# Remediation actions that count as actually disrupting a chain — must match the
# resolution logic in _vulnerability_mttr, or "disrupted" and "still open" contradict.
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
        # (Security_Scorecard) instead of presenting a confident — possibly false — 0.
        return {"val": 0, "week_delta": 0, "calibrated": True, "data_gap": True}
    try:
        _, _, this_dis, last_dis = _kill_chain_week_sets(paths)
        return {"val": len(this_dis), "week_delta": len(this_dis) - len(last_dis), "calibrated": True}
    except Exception as e:
        return {"val": None, "error": f"source unavailable: {str(e)}", "calibrated": False}


def _kill_chains_detected(records: list[dict], repo_root: Path | None = None) -> dict:  # noqa: ARG001
    if repo_root is None:
        repo_root = _ORDER_SAMURAI_ROOT
    paths = _kill_chain_paths(repo_root)
    if not any(p.exists() for p in paths):
        return {"val": 0, "week_delta": 0, "calibrated": True}
    try:
        this_det, last_det, _, _ = _kill_chain_week_sets(paths)
        return {"val": len(this_det), "week_delta": len(this_det) - len(last_det), "calibrated": True}
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


def _calibration_min_samples(coef_data: dict) -> int:
    """Display-gate bar = the single calibration_threshold.samples from
    calibration_coefficients.json — the SAME value the write gate
    (_calibrate_coefficients) uses. Unifies the two so the dashboard never hides a
    threshold that disagrees with the calculation. Falls back to the constant only
    when no coef/threshold is present."""
    try:
        return int(coef_data.get("calibration_threshold", {}).get("samples", _CALIBRATION_MIN_SAMPLES))
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


def _estimated_agent_time_saved(records: list[dict], repo_root: Path | None = None) -> dict:  # noqa: ARG001
    if repo_root is None:
        repo_root = _ORDER_SAMURAI_ROOT
    state_file = repo_root / "state" / "DOJO_STATE.json"
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
        
        now = datetime.now(timezone.utc)
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
        min_samples = _calibration_min_samples(coef_data)
        week_kinds = {item.get("kind", "skill") for item in week_done_items(this_week)}
        calibrated = bool(week_kinds) and all(
            _coef_block_calibrated({k: ops_coef.get(k, {})}, min_samples) for k in week_kinds
        )
            
        val = calculate_week_hours(this_week)
        last_val = calculate_week_hours(last_week)
        week_delta = val - last_val
        return {"val": round(val, 1), "week_delta": round(week_delta, 1), "calibrated": calibrated}
    except Exception as e:
        return {"val": None, "error": f"source unavailable: {str(e)}", "calibrated": False}

def _estimated_cost_savings(records: list[dict], repo_root: Path | None = None) -> dict:
    if repo_root is None:
        repo_root = _ORDER_SAMURAI_ROOT

    # Resolve history path relative to repo_root
    history_path = repo_root.parent.parent / "Data" / "telemetry" / "metrics_history.jsonl"
        
    try:
        now = datetime.now(timezone.utc)
        this_week = now.strftime("%G-W%V")
        last_week = (now - timedelta(days=7)).strftime("%G-W%V")
        
        # Component 1: cost-per-task improvement x this week's task volume.
        # A raw spend drop vs last week is NOT savings — it also falls when less
        # work happens. Efficiency gain per task at this week's volume is.
        wk_recs = [r for r in records if iso_week(r.get("timestamp", "")) == this_week]
        n_tasks = len(wk_recs)
        this_cpt = None
        if n_tasks:
            wk_cost = sum(r.get("total_cost", 0.0) for r in wk_recs
                          if isinstance(r.get("total_cost"), (int, float)) and not isinstance(r.get("total_cost"), bool))
            this_cpt = wk_cost / n_tasks
        prior_cpt = _get_prior_week_val(history_path, "brush/Token Efficiency/Cost_Per_Task",
                                        before_week=this_week)

        comp1_savings = 0.0
        comp1_calibrated = this_cpt is not None and prior_cpt is not None
        if comp1_calibrated and this_cpt < prior_cpt:
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
        last_n_tasks = len(last_wk_recs)
        last_comp1_savings = 0.0
        if last_n_tasks:
            last_wk_cost = sum(r.get("total_cost", 0.0) for r in last_wk_recs
                               if isinstance(r.get("total_cost"), (int, float)) and not isinstance(r.get("total_cost"), bool))
            last_cpt = last_wk_cost / last_n_tasks
            prior_prior_cpt = _get_prior_week_val(history_path, "brush/Token Efficiency/Cost_Per_Task",
                                                  before_week=last_week)
            if prior_prior_cpt is not None and last_cpt < prior_prior_cpt:
                last_comp1_savings = (prior_prior_cpt - last_cpt) * last_n_tasks
        week_delta = val - last_comp1_savings

        # Data gap = no real cost-per-task baseline to measure savings from (no
        # prior-week CPT in history, or no tasks this week). When true the hero falls
        # back to the measured Cost-per-Task rather than show a confident $0 saving.
        # Once comp1 is calibrated the dollar figure is genuinely measured.
        data_gap = not comp1_calibrated

        calibrated = comp1_calibrated
        return {
            "val": round(val, 2),
            "week_delta": round(week_delta, 2),
            "calibrated": calibrated,
            "data_gap": data_gap
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


def _craft_improvements(records: list[dict], repo_root: Path | None = None) -> dict:  # noqa: ARG001
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
    """
    if repo_root is None:
        repo_root = _ORDER_SAMURAI_ROOT
    state_file = repo_root / "state" / "DOJO_STATE.json"

    history_path = repo_root.parent.parent / "Data" / "telemetry" / "metrics_history.jsonl"

    try:
        now = datetime.now(timezone.utc)
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

        return {
            "val": val,
            "week_delta": week_delta,
            "calibrated": True,
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

def _vault_health_metrics() -> dict | None:
    """Load Knowledge/vault/_scripts/vault_health.py dynamically and return current vault metrics.
    Returns None if the script is unavailable (caller emits SIMULATED)."""
    import importlib.util
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
        return {
            "Wiki_Health_Score": score,
            "Wiki_Article_Count": sum(counts.values()),
            "Raw_Pending": len(pending),
            "Wiki_Orphans": len(orphans),
        }
    except Exception:
        return None


REGISTRY: list[tuple[str, str, str, Callable | None, str, bool, bool]] = [
    ("bow", "Activity", "Error_Rate", r_error_rate, "DERIVED", True, False),
    ("bow", "Activity", "Latency_P50", r_lat(50), "DERIVED", False, False),
    ("bow", "Activity", "Latency_P95", r_lat(95), "DERIVED", False, False),
    ("bow", "Activity", "Complexity_Weighted_Throughput", r_complexity_weighted_throughput, "DERIVED", False, False),
    ("bow", "Activity", "Tool_Calls", r_tool_volume, "DERIVED", False, True),
    ("bow", "Activity", "Fallback_Recovery_Rate", _fallback_recovery_rate, "AUTO", True, False),
    ("bow", "Activity", "Session_Count", r_session_count, "DERIVED", False, True),
    ("bow", "Activity", "Avg_Session_Turns", r_avg_session_turns, "DERIVED", False, False),
    ("bow", "Autonomic", "Processes_Reaped", None, "DERIVED", False, True),
    ("bow", "Autonomic", "Agent_Autonomy_Ratio", _agent_autonomy_ratio, "AUTO", True, False),
    ("sword", "Governance", "Rule_Violations", r_sum_field_weekly("rule_violations"), "DERIVED", False, True),
    ("sword", "Governance", "Rule_Violations_Lifetime", r_sum_field("rule_violations"), "DERIVED", False, True),
    ("sword", "Vulnerability", "Vulnerability_MTTR", _vulnerability_mttr, "AUTO", False, False),
    ("sword", "Code Security", "Boundary_Violations", None, "AUTO", False, True),
    ("brush", "Token Efficiency", "Total_Cost", r_total_cost, "DERIVED", False, False),
    ("brush", "Token Efficiency", "Token_Spend", r_token_spend, "DERIVED", False, True),
    ("brush", "Token Efficiency", "Cost_Per_Task", r_cost_per_task, "DERIVED", False, False),
    ("brush", "Token Efficiency", "Token_Execution_Density", r_token_density, "DERIVED", False, False),
    ("brush", "Token Efficiency", "Local_Routing_Share", r_local_routing, "DERIVED", True, False),
    ("brush", "Code Health", "Revision_Ratio", r_revision_ratio, "DERIVED", True, False),
    ("brush", "Orchestration", "Subagent_Efficiency_Index", _subagent_efficiency_index, "DERIVED", False, False),
    ("brush", "Architecture", "Architecture_Scorecard_Grade", None, "AUTO", False, False),
    ("arts", "Output Quality", "Slop_Density", r_slop_density, "DERIVED", False, False),
    ("arts", "Interaction", "Frustration_Signals", r_sum_field_weekly("frustration_signals"), "DERIVED", False, True),
    ("arts", "Interaction", "Frustration_Signals_Lifetime", r_sum_field("frustration_signals"), "DERIVED", False, True),
    ("arts", "Interaction", "Rework_Loops", r_sum_field_weekly("rework_turns"), "DERIVED", False, True),
    ("arts", "Interaction", "Rework_Loops_Lifetime", r_sum_field("rework_turns"), "DERIVED", False, True),
    ("arts", "Process", "Simplify_Runs", r_simplify_runs, "DERIVED", False, True),
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
    ("sword", "Governance", "Kill_Chains_Detected", _kill_chains_detected, "AUTO", False, True),
    ("bow", "Activity", "Estimated_Agent_Time_Saved", _estimated_agent_time_saved, "AUTO", False, False),
    ("brush", "Token Efficiency", "Estimated_Cost_Savings", _estimated_cost_savings, "AUTO", False, False),
    ("arts", "Craft", "Craft_Improvements", _craft_improvements, "AUTO", False, True),
    ("sword", "Governance", "Pending_Chain_Proposals", _pending_chain_proposals, "AUTO", False, True),
]


def _env(val, tier, *, is_percent=False, is_count=False, simulated=False):
    calibrated = True
    delta = "0"
    data_gap = False
    detail = None

    if isinstance(val, dict):
        calibrated = val.get("calibrated", True)
        delta = str(val.get("week_delta", "0"))
        data_gap = val.get("data_gap", False)
        detail = val.get("detail")
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
                  security_signals: dict | None = None) -> dict:
    pillars: dict[str, dict] = {p: {} for p in PILLARS}
    for pillar, group, key, fn, live_tier, is_pct, is_cnt in REGISTRY:
        val = fn(records) if fn else None
        simulated = val is None
        env = _env(val, "SIMULATED" if simulated else live_tier,
                   is_percent=is_pct, is_count=is_cnt, simulated=simulated)
        validate_metric(env)  # tier-honesty contract
        pillars[pillar].setdefault(group, {})[key] = env

    # verifier-derived (real, AUTO) — overwrite SIMULATED placeholders where we have data
    if verifier_results:
        vm = derive_verifier_metrics(verifier_results)
        if records and vm["Governance_Pass_Rate"] is not None:
            _set(pillars, "bow", "Governance", "Governance_Pass_Rate", _env(vm["Governance_Pass_Rate"], "AUTO", is_percent=True))
        _set(pillars, "bow", "Governance", "Verifier_Failures", _env(vm["Verifier_Failures"], "AUTO", is_count=True))
        failing_platforms = sorted({
            str(r.get("platform")) for r in verifier_results
            if r.get("status") == "FAIL" and r.get("platform")
        })
        if failing_platforms:
            vf = pillars["bow"]["Governance"]["Verifier_Failures"]
            vf["failure_platforms"] = failing_platforms
            vf["mitigation_command"] = f"python -m agentica_core.doctor {failing_platforms[0]}"
            vf["mitigation_skill"] = "doctor"

        _set(pillars, "sword", "Code Security", "Boundary_Violations", _env(vm["Boundary_Violations"], "AUTO", is_count=True))
        _set(pillars, "brush", "Code Health", "Hardcoded_Path_Incidents", _env(vm["Hardcoded_Path_Incidents"], "AUTO", is_count=True))
        _set(pillars, "brush", "Code Health", "Root_Hygiene_Issues", _env(vm["Root_Hygiene_Issues"], "AUTO", is_count=True))
    if orphan_count is not None:
        _set(pillars, "bow", "Autonomic", "Agent_Process_Count", _env(orphan_count, "AUTO", is_count=True))
    if secret_fails is not None:
        _set(pillars, "sword", "Code Security", "Secrets_Detected", _env(secret_fails, "AUTO", is_count=True))
    # security telemetry the hooks already emit (read from <runtime>/data); converts SIMULATED -> AUTO
    if security_signals:
        s = security_signals
        # Rule_Violations is now DERIVED from per-session telemetry (see REGISTRY).
        # Removed scout injection: per-session source enables tier/project breakdown.
        if "canary_failures" in s:
            _set(pillars, "sword", "Audit Trail", "Canary_Failures", _env(s["canary_failures"], "AUTO", is_count=True))
        if "gate_canary_fault" in s:
            _set(pillars, "sword", "Audit Trail", "Gate_Canary_Fault", _env(s["gate_canary_fault"], "AUTO", is_count=True))
        # Loop_Breaker_Fires RETIRED 2026-07-19 (metric-surface review Part E
        # item 3): loop_breaker_state.json is never written on this host — the
        # scout emitter never fired, so both injections (sword/Reliability and
        # bow/Reliability) were dead. Removal, never faking.
        if "mechanism_orphans" in s:
            _set(pillars, "bow", "Autonomic", "Mechanism_Orphans", _env(s["mechanism_orphans"], "AUTO", is_count=True))
        if "processes_reaped" in s:
            _set(pillars, "bow", "Autonomic", "Processes_Reaped", _env(s["processes_reaped"], "AUTO", is_count=True))
        if "doc_parity_issues" in s:
            _set(pillars, "arts", "Docs", "Doc_Parity_Issues", _env(s["doc_parity_issues"], "AUTO", is_count=True))
        if "scorecard_grade" in s:
            _set(pillars, "brush", "Architecture", "Architecture_Scorecard_Grade", _env(s["scorecard_grade"], "AUTO"))
        # Sword additions
        if "security_scorecard" in s:
            _set(pillars, "sword", "Posture", "Security_Scorecard", _env(s["security_scorecard"], "AUTO"))
        if "skill_safety_findings" in s:
            _set(pillars, "sword", "Supply Chain", "Skill_Safety_Findings", _env(s["skill_safety_findings"], "AUTO", is_count=True))
        if "open_cves" in s:
            _set(pillars, "sword", "Vulnerability", "Open_CVEs", _env(s["open_cves"], "AUTO", is_count=True))
        if "deprecated_deps" in s:
            _set(pillars, "sword", "Supply Chain", "Deprecated_Deps", _env(s["deprecated_deps"], "AUTO", is_count=True))
        # Arts additions
        # Skills_Optimized + Skill_Promotions RETIRED 2026-07-19 (metric-surface
        # review Part E item 3): their JSONL sources are never written on this
        # host — the scout emitters never fired. Removal, never faking.
        if "skill_conflicts" in s:
            _set(pillars, "arts", "Craft", "Skill_Conflicts", _env(s["skill_conflicts"], "AUTO", is_count=True))
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
        # SWORD-KC: untracked chain candidates from discovery scout
        if "kill_chain_candidates" in s:
            _set(pillars, "sword", "Governance", "Kill_Chain_Candidates",
                 _env(s["kill_chain_candidates"], "AUTO", is_count=True))

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


_INTERNAL_DIRS = {
    ".git", ".claude", ".tmp", ".impeccable", ".mex", ".pytest_cache",
    "node_modules", "__pycache__", "dist", "build", "venv", ".venv", "test_env",
    "bin", "config", "src", "schema", "docs", "api", "dashboard-ui", "execution",
    "state", "tests", "artifacts", "prompts", "scouts", "scratch", "soji",
    "platform_surfaces", "go-to-market", "Research", "Order Samurai", "agentica_core"
}

def build_project_scores(all_records: list[dict], proj_platform: dict[str, str],
                         root: Path = _PROJECTS_ROOT) -> dict:
    """Roster the real project folders in Desktop/Projects, match each to telemetry
    (alias or normalized name match), and return its four itemized pillar scores."""
    by_tproj: dict[str, list] = {}
    for r in all_records:
        pr = r.get("project")
        if pr:
            by_tproj.setdefault(pr, []).append(r)

    folders = sorted(
        p.name for p in root.iterdir()
        if p.is_dir() and not p.name.startswith(".") and p.name not in _INTERNAL_DIRS
    ) if root.exists() else []
    out: dict[str, dict] = {}
    for f in folders:
        nf = _norm(f)
        aliases = set(_PROJECT_ALIASES.get(f, []))
        recs: list[dict] = []
        plats: Counter = Counter()
        for tp, rs in by_tproj.items():
            ntp = _norm(tp)
            # Exact normalized match or alias-only. Substring was too broad:
            # "api" matched "apify", "hub" matched "github". Require at least 6
            # chars for substring fallback to reduce false-alias merging.
            match = (tp in aliases or ntp == nf
                     or (len(ntp) >= 6 and (ntp in nf or nf in ntp)))
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
        # Read dojo state if present (Order Samurai and future dojo-enabled projects)
        dojo_path = root / f / "state" / "DOJO_STATE.json"
        if dojo_path.exists():
            try:
                raw = json.loads(dojo_path.read_text(encoding="utf-8"))
                rpillars = raw.get("pillars", {})
                out[f]["dojo_state"] = {
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
    return t is not None and (datetime.now(timezone.utc) - t) <= timedelta(days=days)


# Signals that must NOT be summed across platforms when merging security_signals:
# scores (non-additive by nature) and platform-independent scouts that return the
# same value for every platform (summing triple-counts them — Doc_Parity_Issues
# showed 30 while the scout said 10).
_NON_ADDITIVE_SIG = frozenset({
    "scorecard_grade", "security_scorecard", "security_scorecard_total",
    "doc_parity_issues",
    "governance_findings_critical", "governance_findings_high", "governance_findings_total_ch",
})


def aggregate(platforms: list[str] | None = None, timestamp: str | None = None,
              window_days: int = 30,
              write_history: bool = False) -> dict:
    platforms = platforms if platforms is not None else list_platforms()
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
    # Primary view = trailing-window telemetry + CURRENT security/governance snapshot
    # (security signals are point-in-time, not windowable). Lifetime kept for the UI toggle.
    windowed = [r for r in all_records if _within_days(r.get("timestamp", ""), window_days)]
    combined = build_pillars(windowed, verifier_results=all_verifier,
                             orphan_count=orphans, secret_fails=fails, security_signals=merged_sig)
    lifetime = build_pillars(all_records, verifier_results=all_verifier,
                             orphan_count=orphans, secret_fails=fails, security_signals=merged_sig)
    category_scores_lifetime = insights.annotate(lifetime)

    # analytical layer: scores, remediation, trend history, summaries.
    # populate_history first so summaries can discuss real deltas/outliers.
    category_scores = insights.annotate(combined)
    current = insights.populate_history(combined)
    summaries = insights.build_summaries(combined, category_scores)
    if write_history:
        insights.append_snapshot(insights.default_history_path(), timestamp or "", current)
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
        "reflexes": reflexes.build_reflexes(combined, category_scores, by_project),
        "remediation_efficacy": remediation.efficacy(records=all_records),
        "top_usage": _top_usage(all_records),
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
    payload = aggregate(timestamp=datetime.now(timezone.utc).isoformat(), write_history=True)
    path = write_payload(payload)
    print(f"Agentica Aggregator -> {path}")
    print(_summary(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
