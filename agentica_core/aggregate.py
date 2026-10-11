"""The unified aggregator — the "senses" half of Governance. Reads canonical telemetry from
BOTH platforms via the adapter, computes the LIVE metrics from the METRICS.md registry, and
emits one cross-platform WIDPayload. Metrics whose source isn't wired yet are emitted as
SIMULATED (tier honesty) — never faked as live. Supersedes Jarvis's aggregator (HARVEST §7).

Registry-driven: each metric is an entry with a reducer; add a metric = an entry + a reducer fn.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re  # noqa: F401  baseline name kept on the façade (W2: every baseline attribute stays importable)
import subprocess
import sys  # noqa: F401  baseline name kept on the façade (W2: every baseline attribute stays importable)
import time
from collections import Counter, defaultdict  # noqa: F401  baseline name kept on the façade (W2: every baseline attribute stays importable)
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Any  # noqa: F401  baseline name kept on the façade (W2: every baseline attribute stays importable)

import jsonschema

_ORDER_SAMURAI_ROOT = Path(os.environ.get(
    "ORDER_SAMURAI_ROOT", str(Path(__file__).resolve().parents[1] / "Order Samurai")))
# The reflex loop's per-cycle heartbeat ("[starved] 0 eligible ..." when nothing is
# eligible). Same file refresh_dashboard._check_engine_freshness reads; here it lets
# Mechanism_Liveness tell "loop alive, correctly idle" from "loop dead".
_REFLEX_HEARTBEAT_LOG = _ORDER_SAMURAI_ROOT / "state" / "reflex_output.jsonl"
# The engine's run log: a same-week row here with no mechanism_run means the bridge
# broke, so the week is NOT idle.
_REFLEX_EXEC_LOG = _ORDER_SAMURAI_ROOT / "state" / "exec_log.jsonl"
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

from . import (display_evidence, harness_config, insights, knowledge_metrics, operator_attention, reflexes,  # noqa: F401  baseline name kept on the façade (W2: every baseline attribute stays importable)
               remediation, remediation_delta, scouts, threshold_audit, verify_secrets)  # noqa: F401  baseline name kept on the façade (W2: every baseline attribute stays importable)
from .atomic import atomic_json_write, file_write_lock  # noqa: F401  baseline name kept on the façade (W2: every baseline attribute stays importable)
from .model_tiers import model_tier  # noqa: F401  baseline name kept on the façade (W2: every baseline attribute stays importable)
from .adapter import PlatformUnavailable, list_platforms, resolve_platform
from .telemetry import (SCHEMA_VERSION, default_events_path, iso_week, normalize_entry,  # noqa: F401  baseline name kept on the façade (W2: every baseline attribute stays importable)
                        parse_ts, validate_entry, validate_metric)  # noqa: F401  baseline name kept on the façade (W2: every baseline attribute stays importable)
from .verifiers import load_verifiers, run_all
from .aggregate_records import (  # noqa: F401  re-exported: the façade keeps every moved name importable here
    _nums, _PLACEHOLDER_SIDS, _real_sid, _dedup_field, _count_success_sessions, _pctile, _w_num,
    _int_vals, _sum_per_session, _has_field, _newest_jsonls, _file_signature, _parse_iso,
    _get_weekly_promotions_count, _get_prior_week_val, _MCP_UUID_NAMES, _MCP_SERVER_DISPLAY,
    _mcp_server_label, _AGENT_SPAWN_FILE_CAP, _agent_spawn_events, _count_agent_types,
    _record_set_week, _target_week_anchor, _SCOUT_CACHE_TTL_SEC, _week_record_count, _within_days,
)
from .aggregate_metrics import (  # noqa: F401  re-exported: the façade keeps every moved name importable here
    r_count, ERROR_STATUSES, MIN_ERROR_SAMPLE, error_rate_stats, r_error_rate, r_lat,
    r_tool_volume, r_tool_diversity, r_session_count, r_avg_session_turns, r_total_cost,
    r_token_spend, _cpt_and_n, r_cost_per_task, _cost_per_outcome, r_token_density,
    r_model_tier_mix, _routing_tier_votes, r_local_routing, r_mcp_vs_cli, _MIX_FIELDS,
    _model_usage_parts, _tier_mix_records, _tier_mix_weighted, build_tier_mix, r_revision_ratio,
    r_slop_density, r_sum_field, r_simplify_age, _JUDGE_SAMPLED_METRICS, _tool_quality,
    r_tool_selection, r_tool_args, r_tool_util, r_faithfulness, r_refusal_appropriateness,
    r_retrieval_relevance, _CLIFF_SCAN_FILES, _CLIFF_THRESHOLD, _cliff_threshold,
    r_chain_depth_avg, r_complexity_weighted_throughput, _subagent_efficiency_index,
    _cache_hit_rate, r_skill_routing_adherence, r_governance_work_volume,
)
from .aggregate_signals import (  # noqa: F401  re-exported: the façade keeps every moved name importable here
    _self_correction_rate, _mitigation_route_validity, _remediation_delta,
    _verifier_falsifiability, _unbounded_wait_count, _vibe_alignment_score,
    _doc_parity_latency_days, _mechanism_liveness, recorded_idle_still_valid, _starved_heartbeats,
    _lesson_graduation_rate, _dead_rule_count, _mean_time_to_heal,
)
from .aggregate_counters import (  # noqa: F401  re-exported: the façade keeps every moved name importable here
    _DISRUPT_ACTIONS, _kill_chain_week_sets, _KC_PRUNE, _kill_chains_disrupted, _kill_chains_open,
    _pending_chain_proposals, _compaction_events, _DAEMON_LOG_LINE_RE, _daemon_restart_count,
    POLL_SECONDS, PROTECTED_BRANCHES, PROTECTED_PREFIXES, PENDING_STATES, _agentica_root,
    _ledger_path, _queue_path, _now, _parse_ts, _git, _open_pr_branches_from_report,
    _latest_ticket_states, _hygiene_envelope, _STALE_BRANCH_TTL_S, r_stale_branch_count,
    r_lane_pending_age, r_approved_unexecuted_hitl,
)
from .aggregate_estimates import (  # noqa: F401  re-exported: the façade keeps every moved name importable here
    _calibrate_coefficients, _calibrate_coefficients_locked, _CALIBRATION_MIN_SAMPLES,
    CalibrationPolicyError, _calibration_threshold, _calibration_min_samples,
    _coef_block_calibrated, _estimated_agent_time_saved, _estimated_cost_savings,
    _craft_improvements, _estimated_human_time_saved,
)
from .aggregate_scoring import (  # noqa: F401  re-exported: the façade keeps every moved name importable here
    _env, _set, derive_verifier_metrics, _PROJECT_ALIASES, _norm, _score_project_bucket,
    _registry_pillars, _apply_measurement_signals, _annotate_failing_platforms, _apply_knowledge_signals,
    _apply_security_signals, _apply_vault_health,
)

_THIS = Path(__file__).resolve()
PILLARS = ("bow", "sword", "brush", "arts")
# 2026-08-25: was `parent.parent / "config"` (= Governance/config/), which holds only
# scorecard_rubric.json — every _SCORECARDS path resolved to a nonexistent file, so
# score_architecture() returned None and Architecture_Scorecard_Grade (weight 3.0) sat
# permanently SIMULATED while the real configs lived one directory over.
_SCORECARD_DIR = Path(__file__).resolve().parent.parent / "Order Samurai" / "config"
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


def _judge_min_n() -> int:
    """Declared min accepted judgments from the editable surface; 30 is the fallback literal."""
    try:
        from agentica_core import harness_config
        return int(harness_config.get_value("judge_min_n"))
    except Exception:
        return 30


_CONTEXT_CLIFF_MEMO: tuple[float, tuple, float | None] | None = None
_CONTEXT_CLIFF_TTL_S = 60.0


def r_context_cliff_events(recs):  # noqa: ARG001
    """Count recent sessions whose max single-message input context
    (input + cache_read + cache_creation) exceeded ~140k tokens — a context-pressure signal
    (agents degrade in very large contexts). Reads transcripts directly (like _cache_hit_rate);
    SessionEnd telemetry lacks per-msg usage. ABSOLUTE cutoff, window-agnostic: this machine's
    models are ~1M-window (contexts reach 562k with no 1M marker), so a 70%-of-window rule would
    never fire. Returns None (-> SIMULATED) when no usage data is present."""
    global _CONTEXT_CLIFF_MEMO
    projects_dir = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".claude" / "projects"
    if not projects_dir.exists():
        return None
    jsonls = _newest_jsonls(projects_dir, _CLIFF_SCAN_FILES)
    threshold = _cliff_threshold()
    signature = (threshold, _file_signature(jsonls))
    now = time.monotonic()
    if _CONTEXT_CLIFF_MEMO is not None:
        stamped_at, cached_signature, cached = _CONTEXT_CLIFF_MEMO
        if now - stamped_at < _CONTEXT_CLIFF_TTL_S and signature == cached_signature:
            return cached

    scanned = cliffs = 0
    read_failed = False
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
            read_failed = True
            continue
        if max_ctx > 0:
            scanned += 1
            if max_ctx > threshold:
                cliffs += 1
    # Share, not count (2026-07-19 recalibration): the absolute weekly count was
    # volume-coupled — a busy week 'failed' automatically. 100×cliffs/scanned reads
    # as 'what fraction of recent sessions ran hot'. (History rows before this date
    # are counts; the σ window flushes within ~a week.)
    result = round(100.0 * cliffs / scanned, 1) if scanned else None
    if not read_failed:
        _CONTEXT_CLIFF_MEMO = (now, signature, result)
    return result


# ---------------------------------------------------------------- registry
# (pillar, group, key, reducer|None, live_tier, is_percent, is_count)
# ---------------------------------------------------------------------------
# Helper functions for new reducers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Reducer implementations
# ---------------------------------------------------------------------------


# Vulnerability_MTTR retired 2026-07-11 (C/D/F remediation plan step 3): the name
# promised CVE mean-time-to-resolution but the reducer read kill-chain events, and
# after the 2026-07-08 default-removal it was permanently SIMULATED (no chains most
# weeks = no measurement). Kill-chain response is covered by Kill_Chains_Open /
# Kill_Chains_Disrupted; CVE exposure is now graded directly via Open_CVEs (the
# dependency_audit.json emitter is live again). Re-add a real MTTR only when a
# first-seen→resolved CVE ledger exists.


# 60s TTL cache of parsed (timestamp, subagent_type) spawn events — the transcript
# scan is window-independent; the per-window filter happens on the parsed list, so
# a 3-window refresh parses the transcripts once instead of three times.
_AGENT_SPAWN_CACHE: dict = {"t": 0.0, "v": None}


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
            if isinstance(tool, str) and tool.startswith("mcp__"):
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


_KILL_CHAIN_WEEK_MEMO: tuple[
    float, tuple, tuple[frozenset, frozenset, frozenset, frozenset]
] | None = None
_KILL_CHAIN_WEEK_TTL_S = 60.0


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


# Self_Correction_Rate is a REGISTRY reducer: build_pillars() hands it `records` and
# nothing else, so aggregate()'s window_days cannot reach it as an argument. aggregate()
# publishes the window it is currently building here (the refresh is single-threaded);
# direct build_pillars() callers get this default. Without a window the rate counted the
# whole exec_log lifetime — one frozen number that 18 days of zero autonomous attempts
# could not move, which is precisely the failure this metric exists to surface.
_ACTIVE_WINDOW_DAYS = 30


#: (monotonic stamp, result) — see the memo note in _unbounded_wait_count.
_unbounded_wait_memo: tuple[float, dict] | None = None
_UNBOUNDED_WAIT_TTL_S = 300.0


# 60s TTL cache: vault health is a point-in-time scout (no window/platform input)
# but build_pillars runs ~15x per aggregate and refresh_dashboard aggregates 3
# windows — without this, one refresh full-scans the vault ~45 times (~12k file
# reads each). A short TTL (not @lru_cache) so long-lived callers stay fresh.
_VAULT_CACHE: dict = {"t": 0.0, "v": None}


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


_DEAD_RULE_CACHE: dict = {"t": 0.0, "v": None}


_MTTH_CACHE: dict = {"t": 0.0, "v": None}


_COMPACTION_CACHE: dict = {"t": 0.0, "v": None}


def _in_monorepo_layout() -> bool:
    """True only when THIS module sits at <checkout>/Governance/agentica_core of a checkout that
    has Execution/factory (the marker `_agentica_root` and `claude_runtime_target.agentica_repo_root`
    already use for "inside the AgenticaOS repo"), Governance/Order Samurai beside it (an export
    flattens the pack) and no `.export-withdrawn` manifest (so an install folder that merely happens
    to be named "Governance" is still an export).

    The hygiene metrics walk two levels above the Order Samurai root. In the public export and in
    a buyer install this module sits at <install>/agentica_core, so that walk lands on the install's
    PARENT: whatever git repo or factory ledger is there belongs to someone else, and reading it
    reports a stranger's branches as the buyer's own. Owner decision 2026-10-07: outside the
    monorepo layout these metrics read SIMULATED (the -1 missing-source envelope), never another repo.
    """
    governance = Path(__file__).resolve().parents[1]
    if (governance / ".export-withdrawn").exists():  # the marker bin/extract_public.py writes into every export
        return False
    return (governance.name == "Governance" and (governance / "Order Samurai").is_dir()
            and (governance.parent / "Execution" / "factory").is_dir())


def _stale_branch_count(records: list[dict], repo_root: Path, now: _dt.datetime | None = None) -> int:  # noqa: ARG001
    """Local branches that are not ancestors of the integration branch and carry no OPEN PR."""
    if not _in_monorepo_layout():
        return -1
    root = _agentica_root(repo_root)
    rc, _ = _git(root, "rev-parse", "--git-dir")
    if rc:
        return -1
    base = "origin/work" if _git(root, "rev-parse", "--verify", "origin/work")[0] == 0 else "work"
    if _git(root, "rev-parse", "--verify", base)[0]:
        return -1
    rc, out = _git(root, "for-each-ref", "--format=%(refname:short)", "refs/heads/")
    if rc:
        return -1
    open_pr = _open_pr_branches_from_report(root, _now(now))
    count = 0
    for branch in out.splitlines():
        if not branch or branch in PROTECTED_BRANCHES or branch.startswith(PROTECTED_PREFIXES):
            continue
        if branch in open_pr:
            continue
        if _git(root, "merge-base", "--is-ancestor", branch, base)[0] != 0:
            count += 1
    return count


def _lane_pending_age(records: list[dict], repo_root: Path, now: _dt.datetime | None = None) -> int:  # noqa: ARG001
    """Factory tickets whose LAST ledger ticket_state is lane-pending for longer than one poll."""
    ledger = _ledger_path(repo_root)
    if not _in_monorepo_layout() or not ledger.is_file():
        return -1
    cutoff = _now(now) - _dt.timedelta(seconds=POLL_SECONDS)
    count = 0
    for row in _latest_ticket_states(ledger).values():
        if row.get("state") not in PENDING_STATES:
            continue
        ts = _parse_ts(row.get("ts", ""))
        if ts is None or ts <= cutoff:
            count += 1  # an unparseable timestamp is treated as old, not as fresh
    return count


def _approved_unexecuted_hitl(records: list[dict], repo_root: Path, now: _dt.datetime | None = None) -> int:  # noqa: ARG001
    """HITL queue rows source=factory, status=approved, executing_at unset, approved older than one poll.

    Factory approvals exist only in the monorepo layout. Elsewhere (an export or a buyer
    install, whose own HITL queue holds no factory rows) the metric reads SIMULATED (-1),
    never a measured 0. A queue without an `items` list is malformed, also -1."""
    if not _in_monorepo_layout():
        return -1
    queue = _queue_path(repo_root)
    try:
        data = json.loads(queue.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return -1
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return -1
    cutoff = _now(now) - _dt.timedelta(seconds=POLL_SECONDS)
    count = 0
    for it in items:
        if not isinstance(it, dict) or it.get("source") != "factory" or it.get("status") != "approved":
            continue
        if it.get("executing_at"):
            continue
        approved = _parse_ts(it.get("approved_at") or "")
        if approved is None or approved <= cutoff:
            count += 1
    return count


_STALE_BRANCH_CACHE: dict = {"t": 0.0, "v": None}


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
    # Skill_Routing_Adherence: % of router-hook (category, skill) detections whose
    # governing skill was invoked in the same session at or after the detection;
    # the hiring-review nudge's own recommendations are excluded (2026-09-30). The honor-system
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
    # Git / factory hygiene trio (wired 2026-09-23; intake approved 2026-09-21). Each
    # reads its own source outside telemetry; -1 (source missing) grades SIMULATED via
    # the r_* wrapper, never a zero. See the reducer block above the REGISTRY.
    ("bow", "Autonomic", "Stale_Branch_Count", r_stale_branch_count, "AUTO", False, True),
    ("bow", "Autonomic", "Lane_Pending_Age", r_lane_pending_age, "AUTO", False, True),
    ("bow", "Autonomic", "Approved_Unexecuted_HITL", r_approved_unexecuted_hitl, "AUTO", False, True),
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


def build_pillars(records: list[dict], *, verifier_results: list[dict] | None = None,
                  orphan_count: int | None = None, secret_fails: int | None = None,
                  security_signals: dict | None = None,
                  knowledge_signals: dict | None = None,
                  measurement_signals: dict | None = None) -> dict:
    pillars = _registry_pillars(records, PILLARS, REGISTRY)
    if measurement_signals:
        _apply_measurement_signals(pillars, measurement_signals, knowledge_metrics)
    if verifier_results:
        # Literal _set calls stay here: test_kernel_drift reads the injected keys from this file.
        vm = derive_verifier_metrics(verifier_results)
        _set(pillars, "bow", "Governance", "Governance_Pass_Rate", _env(vm["Governance_Pass_Rate"], "AUTO", is_percent=True))
        _annotate_failing_platforms(pillars, verifier_results)
        _set(pillars, "sword", "Code Security", "Boundary_Violations", _env(vm["Boundary_Violations"], "AUTO", is_count=True))
        _set(pillars, "brush", "Code Health", "Hardcoded_Path_Incidents", _env(vm["Hardcoded_Path_Incidents"], "AUTO", is_count=True))
        _set(pillars, "brush", "Code Health", "Root_Hygiene_Issues", _env(vm["Root_Hygiene_Issues"], "AUTO", is_count=True))
    if orphan_count is not None:
        _set(pillars, "bow", "Autonomic", "Agent_Process_Count", _env(orphan_count, "AUTO", is_count=True))
    if secret_fails is not None:
        _set(pillars, "sword", "Code Security", "Secrets_Detected", _env(secret_fails, "AUTO", is_count=True))
    if knowledge_signals:
        _apply_knowledge_signals(pillars, knowledge_signals)
    if security_signals:
        _apply_security_signals(pillars, security_signals)
    _apply_vault_health(pillars, _vault_health_metrics())
    return pillars


# Real project roster lives here; telemetry uses short codes, so a few need aliases.
# Root is configurable, not hardcoded: env override first, else derive from the (already
# configurable) Order Samurai root — Order Samurai is itself a project under Desktop/Projects,
# so its parent IS the roster root. The roster itself stays dynamic (iterdir, below).
_PROJECTS_ROOT = Path(os.environ.get("AGENTICA_PROJECTS_ROOT", str(_ORDER_SAMURAI_ROOT.parent)))
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


def build_project_scores(all_records: list[dict], proj_platform: dict[str, str],
                         root: Path = _PROJECTS_ROOT) -> dict:
    """Roster the real project folders in Desktop/Projects, match each to telemetry
    (alias or normalized name match), and return its four itemized pillar scores.

    Telemetry `project` tags that never match a real folder (e.g. Antigravity's
    "HUD"/"HUB" self-reporting on its own dashboard-generation work — legitimately
    fleet-wide, not any one project's code) used to be silently dropped, which made
    e.g. Local_Routing_Share read 0.0 in every real project bucket even when local-
    model work was genuinely happening under one of those tags. They're now
    collected into an explicit `_meta` bucket instead of vanishing (2026-08-18)."""
    by_tproj: dict[str, list] = {}
    for r in all_records:
        pr = r.get("project")
        if pr:
            by_tproj.setdefault(pr, []).append(r)

    folders = sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []
    out: dict[str, dict] = {}
    matched_tproj: set[str] = set()

    # Assign each telemetry tag to AT MOST ONE folder. Exact/alias matches are
    # resolved in a first pass before any substring match is considered, so a
    # tag that exactly names one folder (e.g. "AgenticaOS-bot") can't also be
    # absorbed by another folder whose normalized name happens to be a
    # substring of it (e.g. "AgenticaOS") — that used to double-count the
    # tag's records into both folders' Session_Count, since `matched_tproj`
    # was tracked but never consulted while matching (2026-09-08 fix).
    tag_to_folder: dict[str, str] = {}
    for f in folders:
        nf = _norm(f)
        aliases = set(_PROJECT_ALIASES.get(f, []))
        for tp in by_tproj:
            if tp in tag_to_folder:
                continue
            if tp in aliases or _norm(tp) == nf:
                tag_to_folder[tp] = f
    for f in folders:
        nf = _norm(f)
        for tp in by_tproj:
            if tp in tag_to_folder:
                continue
            ntp = _norm(tp)
            # Substring was too broad on its own: "api" matched "apify", "hub"
            # matched "github". Require the SHORTER (substring/needle) name to
            # be at least 6 chars in each direction — guarding only ntp let a
            # short folder like "api" still absorb a long telemetry project
            # like "apifyscraper" via `nf in ntp`.
            if (len(ntp) >= 6 and ntp in nf) or (len(nf) >= 6 and nf in ntp):
                tag_to_folder[tp] = f

    for f in folders:
        recs: list[dict] = []
        plats: Counter = Counter()
        for tp, folder in tag_to_folder.items():
            if folder == f:
                rs = by_tproj[tp]
                recs.extend(rs)
                plats[proj_platform.get(tp, "")] += len(rs)
                matched_tproj.add(tp)
        if recs:
            out[f] = _score_project_bucket(recs, plats)
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

    # Tags that never matched a real folder (e.g. "HUD"/"HUB") — surfaced as one
    # aggregate bucket instead of silently dropped; `tags` names each source tag
    # and its record count for traceability back to the emitter.
    unmatched_recs: list[dict] = []
    unmatched_plats: Counter = Counter()
    unmatched_tags: Counter = Counter()
    for tp, rs in by_tproj.items():
        if tp not in matched_tproj:
            unmatched_recs.extend(rs)
            unmatched_plats[proj_platform.get(tp, "")] += len(rs)
            unmatched_tags[tp] += len(rs)
    if unmatched_recs:
        out["_meta"] = {
            **_score_project_bucket(unmatched_recs, unmatched_plats),
            "is_meta": True,
            "tags": dict(unmatched_tags),
        }
    return out


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
    collection_start = datetime.now(timezone.utc)
    window_end = collection_start
    window_start = window_end - timedelta(days=window_days)
    platforms = platforms if platforms is not None else list_platforms()
    # Publish this build's window for the REGISTRY reducers that cannot take it as an
    # argument (see _ACTIVE_WINDOW_DAYS). Set first, before any build_pillars() call.
    global _ACTIVE_WINDOW_DAYS  # noqa: PLW0603
    _ACTIVE_WINDOW_DAYS = window_days
    this_week = iso_week(timestamp) if timestamp else None
    if not this_week:
        this_week = datetime.now(timezone.utc).strftime("%G-W%V")
    # The running release may use a separate checkout but canonical live state.
    measurement_repo = Path(os.environ.get("AGENTICA_REPO_ROOT", str(_ORDER_SAMURAI_ROOT.parents[1])))
    measurements = knowledge_metrics.collect(measurement_repo, Path.home(), window_start,
                                             window_end, platforms)
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
        per_platform[p] = build_pillars(recs, verifier_results=vres, security_signals=sig,
                                       measurement_signals=measurements["by_platform"].get(p))
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
                             knowledge_signals=ksig, measurement_signals=measurements["combined"])
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

    return display_evidence.snapshot({
        "schema_version": SCHEMA_VERSION,
        "timestamp": timestamp or "",
        "platforms": list(platforms),
        "record_counts": counts,
        "window": {"days": window_days, "records": len(windowed),
                   "start": window_start.isoformat(), "end": window_end.isoformat()},
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
        # What needs a HUMAN (coverage review 2026-09-02, W6): doctor rows, fleet escalation
        # tiers, HITL items expired without decision, auto-disabled jobs, backlog/goal SLAs —
        # read from the same files the banner and digest read. build_safe never raises: a
        # reader that cannot answer contributes a data_gap line, never an empty list.
        "operator_attention": operator_attention.build_safe(
            os_root=_ORDER_SAMURAI_ROOT, repo_root=_AGENTICA_REPO_ROOT),
        # Unlocks the dashboard's Pro metrics. Tier only, read from ~/.samurai/license.json.
        "license": _license_summary(),
    }, window_start, window_end, collection_start)


def _license_summary() -> dict[str, str]:
    """Tier-only Pro entitlement for the payload. licensing.py is owned by the public
    product repo and absent from a bare export (extract_public.PUBLIC_OWNED_MODULES), so
    a missing module fails closed to Free instead of breaking aggregation."""
    try:
        from . import licensing  # noqa: PLC0415
    except ImportError:
        return {"tier": "free"}
    return licensing.dashboard_summary()


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
    # `python -m agentica_core.aggregate` executes this file as `__main__`. Reducers moved
    # to aggregate_* modules reach shared state through `from . import aggregate`, which
    # would import a SECOND copy of this module, so state aggregate() publishes (the
    # active window) would never reach them. Delegate to the package module so exactly
    # one copy runs.
    from agentica_core import aggregate as _pkg
    raise SystemExit(_pkg.main())
