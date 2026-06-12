"""Order Samurai metric aggregator.

FROZEN repo-local kernel (2026-06-12). A second, larger agentica_core kernel
lives in the Agentica OS Governance layer and produces the dashboard payload.
Rules to prevent silent drift between the two:
  - New DASHBOARD reducers/metrics go in the Governance copy
    (C:/Users/jemak/Desktop/Agentica OS/Governance/agentica_core/aggregate.py).
  - Shared logic (_parse_iso, _calibrate_coefficients) must stay semantically
    identical in both copies — enforced by tests/test_kernel_drift.py.
  - Repo-local reducers (DOJO_STATE, kill-chain, hub state files) stay here.
Full merge is deferred to its own project; see SENSEI plan 2026-06-12.

REGISTRY: the single source of truth for all LIVE metrics.
Each entry declares a reducer that reads from a real source — never invented.

Build order (METRICS.md):
  1. telemetry.py  — schema  (done)
  2. aggregate.py  — REGISTRY + load_telemetry_records  (this file)
  3. autonomic_events emitter  — future
"""
from __future__ import annotations

from collections import defaultdict
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path bootstrap — agentica_core may be imported stand-alone or from the repo
# root.  We add the repo root to sys.path so execution.* verifiers are
# importable without hard-coding an absolute path.
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Telemetry loader
# ---------------------------------------------------------------------------

def load_telemetry_records(repo_root: Path) -> list[dict]:
    """Read all state/logs/*.json files and return a flat list of record dicts.

    Each log file may be:
      - JSON Lines  (one JSON object per line, as the harness emits)
      - A JSON array  (legacy or future format)

    Records that are not dicts are silently skipped.
    Files that fail to parse are silently skipped (log format may change).
    """
    logs_dir = repo_root / "state" / "logs"
    if not logs_dir.exists():
        return []

    records: list[dict] = []
    for log_path in sorted(logs_dir.glob("*.json")):
        try:
            text = log_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        if not text.strip():
            continue

        # Try JSON array first, then JSON Lines.
        stripped = text.strip()
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    records.extend(item for item in parsed if isinstance(item, dict))
                    continue
            except json.JSONDecodeError:
                pass

        # JSON Lines
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    records.append(obj)
            except json.JSONDecodeError:
                continue

    return records


# ---------------------------------------------------------------------------
# Verifier-backed reducers (Root_Hygiene_Issues, Hardcoded_Path_Incidents)
# These call the real verifier functions so the metric reads from the live
# source, not from telemetry logs.
# ---------------------------------------------------------------------------

def _count_root_hygiene_fails(records: list[dict], repo_root: Path) -> int:  # noqa: ARG001
    from execution.verify_root_hygiene import run_checks
    results = run_checks(repo_root=repo_root)
    return sum(1 for r in results if r.get("status") == "FAIL")


def _count_hardcoded_path_fails(records: list[dict], repo_root: Path) -> int:  # noqa: ARG001
    from execution.verify_path_authority import run_checks
    results = run_checks(repo_root=repo_root)
    return sum(1 for r in results if r.get("status") == "FAIL")


# ---------------------------------------------------------------------------
# Telemetry-backed reducers
# ---------------------------------------------------------------------------

def _ratio_by_field(records: list[dict], field: str, value: str) -> float:
    """Fraction of records where `field == value`, among those that have the field set."""
    values = [r.get(field) for r in records if r.get(field) is not None]
    if not values:
        return 0.0
    return sum(1 for v in values if v == value) / len(values)


def _mcp_vs_cli_ratio(records: list[dict], repo_root: Path) -> float:  # noqa: ARG001
    """Fraction of tool-routed records that went via MCP (not CLI).

    Returns 0.0 when no records carry the mcp_or_cli field (no data yet).
    """
    return _ratio_by_field(records, "mcp_or_cli", "mcp")


def _local_routing_share(records: list[dict], repo_root: Path) -> float:  # noqa: ARG001
    return _ratio_by_field(records, "model_tier", "LOCAL")


def _read_jsonl(path: Path) -> list[dict]:
    """Read a JSONL file and return valid dict records. Returns [] if absent."""
    if not path.exists():
        return []
    records = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                records.append(obj)
        except json.JSONDecodeError:
            continue
    return records


def _load_autonomic_events(repo_root: Path) -> list[dict]:
    """Read state/autonomic_events.jsonl and return event dicts.

    Not cached: the file is written by scouts mid-run, so a cached result from
    the start of the process would produce stale Hook_Failure_Rate / Zombie_Process_Count
    metrics on every subsequent aggregate() call within the same process.
    """
    return _read_jsonl(repo_root / "state" / "autonomic_events.jsonl")


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


def _tool_failure_rate(records: list[dict], repo_root: Path) -> float:  # noqa: ARG001
    """Fraction of tool invocations that returned ok=False across all sessions.

    Only counts records that carry the tool_latencies field. Returns 0.0 when
    no records have latency data — no data yet, not a fake zero.
    """
    all_entries = [
        e
        for r in records
        if isinstance(r.get("tool_latencies"), list)
        for e in r["tool_latencies"]
        if isinstance(e, dict)
    ]
    if not all_entries:
        return 0.0
    return sum(1 for e in all_entries if not e.get("ok", True)) / len(all_entries)


def _security_score(records: list[dict], repo_root: Path) -> float:  # noqa: ARG001
    """Claude platform security score (0-100) from ~/.claude/data/security_scorecard.json.

    Returns 0.0 when the scorecard is absent or unreadable — no fake value.
    Reads the pre-computed cache instead of invoking score_security.py at runtime.
    """
    scorecard = Path.home() / ".claude" / "data" / "security_scorecard.json"
    if not scorecard.exists():
        return 0.0
    try:
        d = json.loads(scorecard.read_text(encoding="utf-8", errors="ignore"))
        return float(d["platforms"]["claude"]["total"] or 0.0)
    except (KeyError, ValueError, TypeError):
        return 0.0


def _canary_health(records: list[dict], repo_root: Path) -> float:  # noqa: ARG001
    """Fault indicator for the security gate canary: 1.0 = faulted, 0.0 = healthy.

    Reads ~/.claude/data/security_gate_canary.json.
    Returns 1.0 (fault) when:
      - canary file is missing (gate has never run)
      - gate_working is False
      - last_run timestamp is missing or older than max_age_days (default 7)
    Returns 0.0 (no fault) when the gate ran recently and reported working.
    """
    canary_path = Path.home() / ".claude" / "data" / "security_gate_canary.json"
    if not canary_path.exists():
        return 1.0  # never run → fault
    try:
        d = json.loads(canary_path.read_text(encoding="utf-8", errors="ignore"))
        if not d.get("gate_working", False):
            return 1.0  # gate explicitly not working → fault
        last_run_str = d.get("last_run", "")
        if not last_run_str:
            return 1.0  # no timestamp → fault
        last_run = datetime.fromisoformat(last_run_str.replace("Z", "+00:00"))
        if last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - last_run).days
        max_age = d.get("max_age_days", 7)
        return 1.0 if age_days > max_age else 0.0  # stale → fault; fresh → healthy
    except Exception:
        return 1.0  # parse error → treat as fault


def _count_jsonl_records(path: Path) -> int:
    """Count valid non-empty JSON objects in a JSONL file. Returns 0 if absent."""
    return sum(1 for r in _read_jsonl(path) if r)


def _secret_scrub_count(records: list[dict], repo_root: Path) -> int:  # noqa: ARG001
    """Total secrets auto-redacted across all scrubber runs from secret_scrubber.jsonl."""
    log = Path.home() / ".claude" / "data" / "secret_scrubber.jsonl"
    total = 0
    for obj in _read_jsonl(log):
        try:
            total += int(obj.get("findings_count") or 0)
        except (ValueError, TypeError):
            pass
    return total


def _opus_share(records: list[dict], repo_root: Path) -> float:  # noqa: ARG001
    """Fraction of cloud model calls that used Opus (goal: < 0.20 per CLAUDE.md).

    Returns 0.0 when no records carry the model field — no data yet, not a fake zero.
    """
    eligible = [r for r in records if r.get("model") and r.get("model_tier") == "CLOUD"]
    if not eligible:
        return 0.0
    opus_count = sum(1 for r in eligible if "opus" in str(r.get("model", "")).lower())
    return opus_count / len(eligible)


def _subagent_cost_multiplier(records: list[dict], repo_root: Path) -> float:  # noqa: ARG001
    """Mean subagent_spawns per session (proxy for context multiplication factor).

    Returns 0.0 when no records carry the subagent_spawns field.
    Sessions with 0 subagents are included to avoid over-optimistic averages.
    """
    eligible = [r for r in records if r.get("subagent_spawns") is not None]
    if not eligible:
        return 0.0
    return sum(int(r.get("subagent_spawns", 0)) for r in eligible) / len(eligible)


def _hook_failure_rate(records: list[dict], repo_root: Path) -> float:  # noqa: ARG001
    """Fraction of autonomic events that are hook failures (0.0 when no events)."""
    events = _load_autonomic_events(repo_root)
    if not events:
        return 0.0
    return sum(1 for e in events if e.get("event") == "hook_failure") / len(events)


def _zombie_process_count(records: list[dict], repo_root: Path) -> int:  # noqa: ARG001
    """Count of zombie_killed events in autonomic stream (0 = no zombies detected)."""
    events = _load_autonomic_events(repo_root)
    return sum(1 for e in events if e.get("event") == "zombie_killed")


def _daily_ronin_spend(records: list[dict], repo_root: Path) -> float:  # noqa: ARG001
    """Dollars spent by the Ronin daemon today from state/budget_ledger.json.

    Resets to 0.0 each calendar day. Returns 0.0 when the ledger is absent or
    the date doesn't match today — no stale carry-over.
    """
    ledger = repo_root / "state" / "budget_ledger.json"
    if not ledger.exists():
        return 0.0
    try:
        d = json.loads(ledger.read_text(encoding="utf-8", errors="ignore"))
        if d.get("date") != str(date.today()):
            return 0.0
        return round(float(d.get("spent_usd", 0.0)), 4)
    except Exception:
        return 0.0


def _backlog_velocity(records: list[dict], repo_root: Path) -> int:  # noqa: ARG001
    """Backlog items completed in the last 7 days (system self-improvement cadence).

    Reads DOJO_STATE.json backlog; counts items with status==done AND
    completed_at within the last 7 days. Returns 0 for items without
    completed_at (old items completed before timestamp tracking was added).
    """
    state_file = repo_root / "state" / "DOJO_STATE.json"
    if not state_file.exists():
        return 0
    try:
        state = json.loads(state_file.read_text(encoding="utf-8", errors="ignore"))
        cutoff = str(date.today() - timedelta(days=7))
        return sum(
            1 for item in state.get("backlog", [])
            if item.get("status") == "done" and item.get("completed_at", "") >= cutoff
        )
    except Exception:
        return 0


def _ronin_cycle_success_rate(records: list[dict], repo_root: Path) -> float:  # noqa: ARG001
    """Fraction of completed Ronin daemon cycles that succeeded (rc=0).

    Reads state/logs/cycle_*.json files. Each file is the raw claude --print
    stream; success = last JSON line has no error field and result is non-empty.
    Returns 0.0 when no cycle logs exist yet.
    """
    logs_dir = repo_root / "state" / "logs"
    if not logs_dir.exists():
        return 0.0
    cycle_files = sorted(logs_dir.glob("cycle_*.json"))
    if not cycle_files:
        return 0.0
    total = 0
    successes = 0
    for log_path in cycle_files:
        try:
            text = log_path.read_text(encoding="utf-8", errors="ignore").strip()
            if not text:
                continue
            last_line = text.splitlines()[-1]
            obj = json.loads(last_line)
            total += 1
            # Success: has a result field and no top-level error
            if obj.get("result") and not obj.get("error"):
                successes += 1
        except Exception:
            total += 1  # count unreadable logs as failures
    if total == 0:
        return 0.0
    return successes / total


_TOTAL_PLANNED_METRICS = 47  # from METRICS.md header — update when catalog grows


def _metric_live_fraction(records: list[dict], repo_root: Path) -> float:  # noqa: ARG001
    """Fraction of the planned metric catalog that is wired and LIVE.

    Rises each time a new reducer lands in REGISTRY. Primary self-improvement signal:
    'How complete is Order Samurai's own observability?'
    """
    return round(len(REGISTRY) / _TOTAL_PLANNED_METRICS, 3)


def _skill_promotions(records: list[dict], repo_root: Path) -> int:  # noqa: ARG001
    """Count of skill promotions logged to ~/.claude/data/skill_promotion_log.jsonl."""
    return _count_jsonl_records(Path.home() / ".claude" / "data" / "skill_promotion_log.jsonl")


def _governance_pass_rate(records: list[dict], repo_root: Path) -> float:  # noqa: ARG001
    """Fraction of verifier checks (root-hygiene + path-authority + runtime-contract) passing.

    Aggregates all three governance verifiers into a single 0-1 pass rate.
    Complements Root_Hygiene_Issues and Hardcoded_Path_Incidents which count failures;
    this provides the overall health percentage.
    """
    try:
        from execution.verify_root_hygiene import run_checks as rh
        from execution.verify_path_authority import run_checks as pa
        from execution.verify_runtime_contract import run_checks as rc
        all_results = rh(repo_root=repo_root) + pa(repo_root=repo_root) + rc(repo_root=repo_root)
        if not all_results:
            return 0.0
        passes = sum(1 for r in all_results if r.get("status") in ("OK", "PASS"))
        return round(passes / len(all_results), 3)
    except Exception:
        return 0.0


def _principle_violations(records: list[dict], repo_root: Path) -> int:  # noqa: ARG001
    """Count of recorded CLAUDE.md principle violations from principle_violations.jsonl."""
    return _count_jsonl_records(Path.home() / ".claude" / "data" / "principle_violations.jsonl")


def _loop_breaker_fires(records: list[dict], repo_root: Path) -> int:  # noqa: ARG001
    """Count of loop-breaker events (agent stuck repeating same error 3x).

    Checks ~/.claude/data/loop_breaker_state.json first (total_fires or fires field),
    then falls back to ~/.claude/data/loop_breaker_log.jsonl (count entries).
    Returns 0 when no data source exists.
    """
    state_file = Path.home() / ".claude" / "data" / "loop_breaker_state.json"
    log_file = Path.home() / ".claude" / "data" / "loop_breaker_log.jsonl"
    if state_file.exists():
        try:
            d = json.loads(state_file.read_text(encoding="utf-8", errors="ignore"))
            for k in ("total_fires", "fires", "count"):
                if k in d:
                    return int(d[k])
        except Exception:
            pass
    return _count_jsonl_records(log_file)


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


def _resolve_history_path(repo_root: Path) -> Path:
    path = repo_root.parent / "Governance" / "Data" / "telemetry" / "metrics_history.jsonl"
    if not path.exists():
        path = Path(r"C:\Users\jemak\Desktop\Agentica OS\Data\telemetry\metrics_history.jsonl")
    return path


def _get_weekly_promotions_count(now: datetime) -> int:
    log_path = Path.home() / ".claude" / "data" / "skill_promotion_log.jsonl"
    this_week = now.strftime("%G-W%V")
    count = 0
    for obj in _read_jsonl(log_path):
        ts_val = obj.get("timestamp") or obj.get("ts") or obj.get("created_at")
        if ts_val:
            try:
                dt = datetime.fromisoformat(ts_val.replace("Z", "+00:00"))
                if dt.strftime("%G-W%V") == this_week:
                    count += 1
            except Exception:
                continue
    return count


def _get_weekly_arts_effort(backlog: list[dict], now: datetime) -> float:
    this_week = now.strftime("%G-W%V")
    total_effort = 0.0
    for item in backlog:
        if item.get("status") == "done" and item.get("pillar") == "arts":
            comp_dt = _parse_iso(item.get("completed_at"))
            if comp_dt and comp_dt.strftime("%G-W%V") == this_week:
                try:
                    total_effort += float(item.get("effort", 1))
                except (ValueError, TypeError):
                    total_effort += 1.0
    return total_effort


def _get_prior_week_val(history_path: Path, metric_key: str) -> float | None:
    if not history_path.exists():
        return None
    try:
        lines = history_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        for line in reversed(lines):
            if not line.strip():
                continue
            obj = json.loads(line)
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
    
    samples_by_kind = defaultdict(list)
    for item in backlog:
        if item.get("status") == "done":
            start = _parse_iso(item.get("started_at"))
            comp = _parse_iso(item.get("completed_at"))
            if start and comp:
                duration = (comp - start).total_seconds() / 60
                samples_by_kind[item.get("kind")].append(duration)

    total_samples = sum(len(v) for v in samples_by_kind.values())
    threshold = coef.get("calibration_threshold", {}).get("samples", 20)

    if total_samples >= threshold:
        for kind, values in samples_by_kind.items():
            if kind in coef.get("operations", {}):
                avg = sum(values) / len(values)
                coef["operations"][kind]["benchmark_min"] = avg
                coef["operations"][kind]["calibrated"] = True
                coef["operations"][kind]["sample_count"] = len(values)
        
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

def _kill_chains_disrupted(records: list[dict], repo_root: Path) -> dict:  # noqa: ARG001
    path = repo_root / "state" / "kill_chain_events.jsonl"
    if not path.exists():
        return {"val": 0, "week_delta": 0, "calibrated": True}
    
    try:
        now = datetime.now(timezone.utc)
        this_week = now.strftime("%G-W%V")
        last_week = (now - timedelta(days=7)).strftime("%G-W%V")
        
        this_week_chains = set()
        last_week_chains = set()

        for obj in _read_jsonl(path):
            ts = obj.get("ts")
            chain_id = obj.get("chain_id")
            if ts is None or chain_id is None:
                continue
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                wk = dt.strftime("%G-W%V")
                if wk == this_week:
                    this_week_chains.add(chain_id)
                elif wk == last_week:
                    last_week_chains.add(chain_id)
            except Exception:
                continue

        val = len(this_week_chains)
        week_delta = val - len(last_week_chains)
        return {"val": val, "week_delta": week_delta, "calibrated": True}
    except Exception as e:
        return {"val": None, "error": f"source unavailable: {str(e)}", "calibrated": False}


def _estimated_agent_time_saved(records: list[dict], repo_root: Path) -> dict:  # noqa: ARG001
    state_file = repo_root / "state" / "DOJO_STATE.json"
    coef_path = repo_root / "state" / "calibration_coefficients.json"
    
    if not state_file.exists() or not coef_path.exists():
        return {"val": 0.0, "week_delta": 0.0, "calibrated": False}
        
    try:
        state = json.loads(state_file.read_text(encoding="utf-8", errors="ignore"))
        backlog = state.get("backlog", [])
        
        _calibrate_coefficients(backlog, coef_path)

        coef_data = json.loads(coef_path.read_text(encoding="utf-8", errors="ignore"))
        ops_coef = coef_data.get("operations", {})
        calibrated = all(v.get("calibrated", False) for v in ops_coef.values()) if ops_coef else False
        
        now = datetime.now(timezone.utc)
        this_week = now.strftime("%G-W%V")
        last_week = (now - timedelta(days=7)).strftime("%G-W%V")
        
        def calculate_week_hours(week_str: str) -> float:
            total_min = 0.0
            for item in backlog:
                if item.get("status") == "done":
                    comp_dt = _parse_iso(item.get("completed_at"))
                    if comp_dt and comp_dt.strftime("%G-W%V") == week_str:
                        kind = item.get("kind", "skill")
                        benchmark_min = ops_coef.get(kind, {}).get("benchmark_min", 30.0)
                        total_min += benchmark_min
            return total_min / 60.0
            
        val = calculate_week_hours(this_week)
        last_val = calculate_week_hours(last_week)
        week_delta = val - last_val
        return {"val": round(val, 1), "week_delta": round(week_delta, 1), "calibrated": calibrated}
    except Exception as e:
        return {"val": None, "error": f"source unavailable: {str(e)}", "calibrated": False}


def _estimated_cost_savings(records: list[dict], repo_root: Path) -> dict:  # noqa: ARG001
    ledger_file = repo_root / "state" / "budget_ledger.json"
    coef_path = repo_root / "state" / "calibration_coefficients.json"
    events_file = repo_root / "state" / "autonomic_events.jsonl"
    history_path = _resolve_history_path(repo_root)
        
    try:
        now = datetime.now(timezone.utc)
        this_week = now.strftime("%G-W%V")
        last_week = (now - timedelta(days=7)).strftime("%G-W%V")
        
        # Component 1: spent_usd delta
        this_week_spend = 0.0
        if ledger_file.exists():
            ledger = json.loads(ledger_file.read_text(encoding="utf-8", errors="ignore"))
            this_week_spend = float(ledger.get("spent_usd", 0.0))
            
        prior_week_spend = _get_prior_week_val(history_path, "brush/Token Efficiency/Total_Cost")
        
        comp1_savings = 0.0
        comp1_calibrated = False
        if prior_week_spend is not None:
            comp1_calibrated = True
            if this_week_spend < prior_week_spend:
                comp1_savings = prior_week_spend - this_week_spend
        
        # Component 2: routing efficiency
        coef_data = {}
        if coef_path.exists():
            coef_data = json.loads(coef_path.read_text(encoding="utf-8", errors="ignore"))
        arch_coef = coef_data.get("architecture", {}).get("routing_efficiency_usd_per_event", {})
        coef_val = arch_coef.get("benchmark", 0.05)
        comp2_calibrated = arch_coef.get("calibrated", False)
        
        this_week_efficient_runs = 0
        last_week_efficient_runs = 0
        this_week_all_runs = 0
        last_week_all_runs = 0
        
        for obj in _read_jsonl(events_file):
            if obj.get("event") == "mechanism_run":
                ts = obj.get("timestamp")
                if ts:
                    try:
                        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        wk = dt.strftime("%G-W%V")
                        if wk == this_week:
                            this_week_all_runs += 1
                            if obj.get("routing_efficient") is True:
                                this_week_efficient_runs += 1
                        elif wk == last_week:
                            last_week_all_runs += 1
                            if obj.get("routing_efficient") is True:
                                last_week_efficient_runs += 1
                    except Exception:
                        continue
                    
        comp2_savings = this_week_efficient_runs * coef_val
        last_week_comp2_savings = last_week_efficient_runs * coef_val
        
        val = comp1_savings + comp2_savings
        week_delta = val - last_week_comp2_savings
        
        # Stale-data guard
        data_gap = False
        if this_week_all_runs == 0 and last_week_all_runs > 0:
            data_gap = True
            
        calibrated = comp1_calibrated and comp2_calibrated
        return {
            "val": round(val, 2),
            "week_delta": round(week_delta, 2),
            "calibrated": calibrated,
            "data_gap": data_gap
        }
    except Exception as e:
        return {"val": None, "error": f"source unavailable: {str(e)}", "calibrated": False}


def _estimated_human_time_saved(records: list[dict], repo_root: Path) -> dict:  # noqa: ARG001
    state_file = repo_root / "state" / "DOJO_STATE.json"
    coef_path = repo_root / "state" / "calibration_coefficients.json"
    
    history_path = _resolve_history_path(repo_root)

    try:
        now = datetime.now(timezone.utc)
        this_week = now.strftime("%G-W%V")
        last_week = (now - timedelta(days=7)).strftime("%G-W%V")

        coef_data = {}
        if coef_path.exists():
            coef_data = json.loads(coef_path.read_text(encoding="utf-8", errors="ignore"))
        craft_coef = coef_data.get("craft", {})
        calibrated = all(v.get("calibrated", False) for v in craft_coef.values()) if craft_coef else False

        backlog = []
        if state_file.exists():
            backlog = json.loads(state_file.read_text(encoding="utf-8", errors="ignore")).get("backlog", [])
            
        def calculate_week_saved(week_str: str, check_time: datetime) -> float:
            if week_str == this_week:
                vibe_score = _vibe_alignment_score(records, repo_root)
            else:
                vibe_score = _get_prior_week_val(history_path, "arts/Output Quality/Vibe_Alignment") or 0.0
                
            prior_vibe_score = _get_prior_week_val(history_path, "arts/Output Quality/Vibe_Alignment") or 0.0
            vibe_delta = max(0.0, vibe_score - prior_vibe_score)
            vibe_coef = craft_coef.get("vibe_alignment_hrs_per_point", {}).get("benchmark", 0.5)
            vibe_saved = vibe_delta * vibe_coef
            
            if week_str == this_week:
                doc_lat = _doc_parity_latency_days(records, repo_root)
            else:
                doc_lat = _get_prior_week_val(history_path, "arts/Docs/Documentation_Parity_Latency") or 0.0
                
            prior_doc_lat = _get_prior_week_val(history_path, "arts/Docs/Documentation_Parity_Latency") or 0.0
            doc_reduction = max(0.0, prior_doc_lat - doc_lat)
            doc_coef = craft_coef.get("doc_parity_latency_hrs_per_day", {}).get("benchmark", 2.0)
            doc_saved = doc_reduction * doc_coef
            
            promo_count = _get_weekly_promotions_count(check_time)
            promo_coef = craft_coef.get("skill_promotion_hrs_per_promotion", {}).get("benchmark", 0.25)
            promo_saved = promo_count * promo_coef
            
            arts_effort = _get_weekly_arts_effort(backlog, check_time)
            arts_coef = craft_coef.get("arts_backlog_hrs_per_effort_point", {}).get("benchmark", 3.0)
            arts_saved = arts_effort * arts_coef
            
            return vibe_saved + doc_saved + promo_saved + arts_saved
            
        val = calculate_week_saved(this_week, now)
        last_val = calculate_week_saved(last_week, now - timedelta(days=7))
        week_delta = val - last_val
        
        val_str = f"~{int(round(val))} hrs"
        return {"val": val_str, "week_delta": round(week_delta, 1), "calibrated": calibrated}
    except Exception as e:
        return {"val": None, "error": f"source unavailable: {str(e)}", "calibrated": False}


def _pending_chain_proposals(records: list[dict], repo_root: Path) -> dict:  # noqa: ARG001
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


# ---------------------------------------------------------------------------
# REGISTRY
# ---------------------------------------------------------------------------
# Shape: {pillar, metric, source, reducer, tier}
# reducer signature: (records: list[dict], repo_root: Path) -> float | int | str

REGISTRY: list[dict[str, Any]] = [
    # ------------------------------------------------------------------
    # Brush — MCP_vs_CLI_Ratio  (BRUSH-001 — NEW)
    # ------------------------------------------------------------------
    {
        "pillar": "brush",
        "metric": "MCP_vs_CLI_Ratio",
        "source": "telemetry.mcp_or_cli",
        "reducer": _mcp_vs_cli_ratio,
        "tier": "DERIVED",
    },
    # ------------------------------------------------------------------
    # Brush — Local_Routing_Share  (already LIVE)
    # ------------------------------------------------------------------
    {
        "pillar": "brush",
        "metric": "Local_Routing_Share",
        "source": "telemetry.model_tier",
        "reducer": _local_routing_share,
        "tier": "DERIVED",
    },
    # ------------------------------------------------------------------
    # Brush — Root_Hygiene_Issues  (already LIVE — reads from verifier)
    # ------------------------------------------------------------------
    {
        "pillar": "brush",
        "metric": "Root_Hygiene_Issues",
        "source": "verifier.root_hygiene",
        "reducer": _count_root_hygiene_fails,
        "tier": "AUTO",
    },
    # ------------------------------------------------------------------
    # Brush — Hardcoded_Path_Incidents  (already LIVE — reads from verifier)
    # ------------------------------------------------------------------
    {
        "pillar": "brush",
        "metric": "Hardcoded_Path_Incidents",
        "source": "verifier.path_authority",
        "reducer": _count_hardcoded_path_fails,
        "tier": "AUTO",
    },
    # ------------------------------------------------------------------
    # Arts — Vibe_Alignment  (ARTS-002 — NEW)
    # Anti-slop score from local gemma-4-e4b pass (scouts/vibe_alignment_scout.py).
    # ------------------------------------------------------------------
    {
        "pillar": "arts",
        "metric": "Vibe_Alignment",
        "source": "state/vibe_alignment.json",
        "reducer": _vibe_alignment_score,
        "tier": "AUTO",
    },
    # ------------------------------------------------------------------
    # Arts — Documentation_Parity_Latency  (ARTS-001 — NEW)
    # Days between newest source change and oldest charter update. 0 = in sync.
    # ------------------------------------------------------------------
    {
        "pillar": "arts",
        "metric": "Documentation_Parity_Latency",
        "source": "file.mtime(state/charters/*.md, execution/**/*.py)",
        "reducer": _doc_parity_latency_days,
        "tier": "AUTO",
    },
    # ------------------------------------------------------------------
    # Bow — Tool_Failure_Rate  (BOW-002 — NEW)
    # Fraction of tool calls with ok=False; 0.0 until tool_latencies emitted.
    # ------------------------------------------------------------------
    {
        "pillar": "bow",
        "metric": "Tool_Failure_Rate",
        "source": "telemetry.tool_latencies",
        "reducer": _tool_failure_rate,
        "tier": "DERIVED",
    },
    # ------------------------------------------------------------------
    # Sword — Security_Score  (SWORD-002 — NEW)
    # Live Claude platform security score from the pre-computed scorecard.
    # ------------------------------------------------------------------
    {
        "pillar": "sword",
        "metric": "Security_Score",
        "source": "~/.claude/data/security_scorecard.json",
        "reducer": _security_score,
        "tier": "AUTO",
    },
    # ------------------------------------------------------------------
    # Sword — Canary_Health  (SWORD-002 — NEW)
    # 1.0 = faulted (gate missing, not working, or stale); 0.0 = healthy and fresh.
    # ------------------------------------------------------------------
    {
        "pillar": "sword",
        "metric": "Canary_Health",
        "source": "~/.claude/data/security_gate_canary.json",
        "reducer": _canary_health,
        "tier": "AUTO",
    },
    # ------------------------------------------------------------------
    # Sword — Secret_Scrub_Count  (SWORD-001 — NEW)
    # Total secrets auto-redacted by secret_scrubber_realtime across all runs.
    # ------------------------------------------------------------------
    {
        "pillar": "sword",
        "metric": "Secret_Scrub_Count",
        "source": "~/.claude/data/secret_scrubber.jsonl",
        "reducer": _secret_scrub_count,
        "tier": "AUTO",
    },
    # ------------------------------------------------------------------
    # Brush — Opus_Share  (BRUSH-003 — NEW)
    # CLAUDE.md rule: Opus for architecture only, keep Opus < 20% of cloud calls.
    # ------------------------------------------------------------------
    {
        "pillar": "brush",
        "metric": "Opus_Share",
        "source": "telemetry.model",
        "reducer": _opus_share,
        "tier": "DERIVED",
    },
    # ------------------------------------------------------------------
    # Brush — Subagent_Cost_Multiplier  (BRUSH-002 — NEW)
    # Mean subagent spawns per session; subagents cost 7-10x inline tokens.
    # ------------------------------------------------------------------
    {
        "pillar": "brush",
        "metric": "Subagent_Cost_Multiplier",
        "source": "telemetry.subagent_spawns",
        "reducer": _subagent_cost_multiplier,
        "tier": "DERIVED",
    },
    # ------------------------------------------------------------------
    # Bow — Hook_Failure_Rate  (BOW-001 — NEW)
    # ------------------------------------------------------------------
    {
        "pillar": "bow",
        "metric": "Hook_Failure_Rate",
        "source": "state/autonomic_events.jsonl",
        "reducer": _hook_failure_rate,
        "tier": "AUTO",
    },
    # ------------------------------------------------------------------
    # Bow — Zombie_Process_Count  (BOW-001 — NEW)
    # ------------------------------------------------------------------
    {
        "pillar": "bow",
        "metric": "Zombie_Process_Count",
        "source": "state/autonomic_events.jsonl",
        "reducer": _zombie_process_count,
        "tier": "AUTO",
    },
    # ------------------------------------------------------------------
    # Bow — Ronin_Cycle_Success_Rate
    # Fraction of daemon cycles that completed with rc=0.
    # Returns 0.0 until state/logs/cycle_*.json files exist.
    # ------------------------------------------------------------------
    {
        "pillar": "bow",
        "metric": "Ronin_Cycle_Success_Rate",
        "source": "state/logs/cycle_*.json",
        "reducer": _ronin_cycle_success_rate,
        "tier": "AUTO",
    },
    # ------------------------------------------------------------------
    # Bow — Backlog_Velocity
    # Backlog items completed in the last 7 days — system self-improvement cadence.
    # ------------------------------------------------------------------
    {
        "pillar": "bow",
        "metric": "Backlog_Velocity",
        "source": "state/DOJO_STATE.json",
        "reducer": _backlog_velocity,
        "tier": "AUTO",
    },
    # ------------------------------------------------------------------
    # Brush — Daily_Ronin_Spend
    # Dollars spent by the daemon today. Resets at midnight. Budget gate fires at $5.
    # ------------------------------------------------------------------
    {
        "pillar": "brush",
        "metric": "Daily_Ronin_Spend",
        "source": "state/budget_ledger.json",
        "reducer": _daily_ronin_spend,
        "tier": "AUTO",
    },
    # ------------------------------------------------------------------
    # Arts — Metric_Live_Fraction  (System Improver)
    # Fraction of the planned 47-metric catalog wired with real reducers.
    # Rises every time Ronin promotes a metric from SIMULATED to LIVE.
    # This is the primary signal that Order Samurai is improving itself.
    # ------------------------------------------------------------------
    {
        "pillar": "arts",
        "metric": "Metric_Live_Fraction",
        "source": "len(REGISTRY)/TOTAL_PLANNED",
        "reducer": _metric_live_fraction,
        "tier": "DERIVED",
    },
    # ------------------------------------------------------------------
    # Arts — Skill_Promotions  (Skill Optimizer signal)
    # Count of skills promoted to a higher priority tier in the skills matrix.
    # Returns 0 when ~/.claude/data/skill_promotion_log.jsonl is absent.
    # ------------------------------------------------------------------
    {
        "pillar": "arts",
        "metric": "Skill_Promotions",
        "source": "~/.claude/data/skill_promotion_log.jsonl",
        "reducer": _skill_promotions,
        "tier": "AUTO",
    },
    # ------------------------------------------------------------------
    # Bow — Governance_Pass_Rate
    # Combined pass rate across all three governance verifiers (0-1).
    # Complements Root_Hygiene_Issues and Hardcoded_Path_Incidents.
    # ------------------------------------------------------------------
    {
        "pillar": "bow",
        "metric": "Governance_Pass_Rate",
        "source": "verifier.root_hygiene+path_authority+runtime_contract",
        "reducer": _governance_pass_rate,
        "tier": "AUTO",
    },
    # ------------------------------------------------------------------
    # Bow — Principle_Violations
    # Count of CLAUDE.md principle violations recorded in the violation log.
    # ------------------------------------------------------------------
    {
        "pillar": "bow",
        "metric": "Principle_Violations",
        "source": "~/.claude/data/principle_violations.jsonl",
        "reducer": _principle_violations,
        "tier": "AUTO",
    },
    # ------------------------------------------------------------------
    # Bow — Loop_Breaker_Fires
    # Count of loop-breaker events (agent stuck on same error 3x).
    # ------------------------------------------------------------------
    {
        "pillar": "bow",
        "metric": "Loop_Breaker_Fires",
        "source": "~/.claude/data/loop_breaker_state.json|loop_breaker_log.jsonl",
        "reducer": _loop_breaker_fires,
        "tier": "AUTO",
    },
    # ------------------------------------------------------------------
    # SWORD — Kill_Chains_Disrupted  (NEW)
    # ------------------------------------------------------------------
    {
        "pillar": "sword",
        "metric": "Kill_Chains_Disrupted",
        "key": "Kill_Chains_Disrupted",
        "source": "state/kill_chain_events.jsonl",
        "reducer": _kill_chains_disrupted,
        "tier": "AUTO",
    },
    # ------------------------------------------------------------------
    # BOW — Estimated_Agent_Time_Saved  (NEW)
    # ------------------------------------------------------------------
    {
        "pillar": "bow",
        "metric": "Estimated_Agent_Time_Saved",
        "key": "Estimated_Agent_Time_Saved",
        "source": "state/DOJO_STATE.json",
        "reducer": _estimated_agent_time_saved,
        "tier": "AUTO",
    },
    # ------------------------------------------------------------------
    # BRUSH — Estimated_Cost_Savings  (NEW)
    # ------------------------------------------------------------------
    {
        "pillar": "brush",
        "metric": "Estimated_Cost_Savings",
        "key": "Estimated_Cost_Savings",
        "source": "state/budget_ledger.json",
        "reducer": _estimated_cost_savings,
        "tier": "AUTO",
    },
    # ------------------------------------------------------------------
    # ARTS — Estimated_Human_Time_Saved  (NEW)
    # ------------------------------------------------------------------
    {
        "pillar": "arts",
        "metric": "Estimated_Human_Time_Saved",
        "key": "Estimated_Human_Time_Saved",
        "source": "state/DOJO_STATE.json+vibe_alignment.json+doc_parity.json",
        "reducer": _estimated_human_time_saved,
        "tier": "AUTO",
    },
    # ------------------------------------------------------------------
    # SWORD — Pending_Chain_Proposals  (NEW)
    # ------------------------------------------------------------------
    {
        "pillar": "sword",
        "metric": "Pending_Chain_Proposals",
        "key": "Pending_Chain_Proposals",
        "source": "state/proposed_kill_chains.json",
        "reducer": _pending_chain_proposals,
        "tier": "AUTO",
    },
]

for _r in REGISTRY:
    _r["key"] = _r.get("key", _r["metric"])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_metric(
    name: str,
    records: list[dict],
    repo_root: Path,
) -> dict[str, Any]:
    """Compute a single metric by name and return a result envelope."""
    entry = next((e for e in REGISTRY if e["metric"] == name), None)
    if entry is None:
        return {
            "metric": name,
            "value": None,
            "source": "unknown",
            "tier": "unknown",
            "live": False,
            "error": f"metric '{name}' not found in REGISTRY",
        }
    try:
        value = entry["reducer"](records, repo_root)
    except Exception as exc:
        return {
            "metric": name,
            "value": None,
            "source": entry["source"],
            "tier": entry["tier"],
            "live": False,
            "error": str(exc),
        }
    
    calibrated = True
    if isinstance(value, dict):
        calibrated = value.get("calibrated", True)
        if value.get("error"):
            return {
                "metric": name,
                "value": None,
                "source": entry["source"],
                "tier": entry["tier"],
                "live": False,
                "error": value["error"],
            }
        value = value.get("val")

    return {
        "metric": name,
        "value": value,
        "source": entry["source"],
        "tier": entry["tier"],
        "live": True,
        "calibrated": calibrated,
    }


def main() -> int:
    records = load_telemetry_records(_REPO_ROOT)
    errors = []
    for entry in REGISTRY:
        result = compute_metric(entry["metric"], records, _REPO_ROOT)
        if not result.get("live"):
            errors.append(result)
            print(f"[FAIL] {entry['metric']}: {result.get('error', 'unknown')}")
        else:
            print(f"[OK] {entry['metric']}: {result['value']}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
