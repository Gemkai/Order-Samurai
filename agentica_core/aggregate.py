"""Order Samurai metric aggregator.

REGISTRY: the single source of truth for all LIVE metrics.
Each entry declares a reducer that reads from a real source — never invented.

Build order (METRICS.md):
  1. telemetry.py  — schema  (done)
  2. aggregate.py  — REGISTRY + load_telemetry_records  (this file)
  3. autonomic_events emitter  — future
"""
from __future__ import annotations

import functools
import json
import sys
from pathlib import Path
from typing import Any, Callable

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
                    for item in parsed:
                        if isinstance(item, dict):
                            records.append(item)
                    continue
            except json.JSONDecodeError:
                pass

        # JSON Lines
        for line in text.splitlines():
            line = line.strip()
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

def _mcp_vs_cli_ratio(records: list[dict], repo_root: Path) -> float:  # noqa: ARG001
    """Fraction of tool-routed records that went via MCP (not CLI).

    Returns 0.0 when no records carry the mcp_or_cli field (no data yet, not
    a fake zero — the metric is live but shows 0.0 until the emitter populates
    the field).
    """
    eligible = [r for r in records if r.get("mcp_or_cli") is not None]
    if not eligible:
        return 0.0
    mcp_count = sum(1 for r in eligible if r.get("mcp_or_cli") == "mcp")
    return mcp_count / len(eligible)


def _local_routing_share(records: list[dict], repo_root: Path) -> float:  # noqa: ARG001
    eligible = [r for r in records if r.get("model_tier") is not None]
    if not eligible:
        return 0.0
    local_count = sum(1 for r in eligible if r.get("model_tier") == "LOCAL")
    return local_count / len(eligible)


def _load_autonomic_events(repo_root: Path) -> list[dict]:
    """Read state/autonomic_events.jsonl and return event dicts.

    Not cached: the file is written by scouts mid-run, so a cached result from
    the start of the process would produce stale Hook_Failure_Rate / Zombie_Process_Count
    metrics on every subsequent aggregate() call within the same process.
    """
    events_path = repo_root / "state" / "autonomic_events.jsonl"
    if not events_path.exists():
        return []
    events: list[dict] = []
    for line in events_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                events.append(obj)
        except json.JSONDecodeError:
            continue
    return events


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
        if score is None or not isinstance(score, (int, float)):
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
    total = 0
    failed = 0
    for r in records:
        latencies = r.get("tool_latencies")
        if not isinstance(latencies, list):
            continue
        for entry in latencies:
            if not isinstance(entry, dict):
                continue
            total += 1
            if not entry.get("ok", True):
                failed += 1
    if total == 0:
        return 0.0
    return failed / total


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
    """1.0 if security gate canary is working and fresh; 0.0 on fault or staleness.

    Reads ~/.claude/data/security_gate_canary.json.
    Stale = last_run > max_age_days; gate_working=False = fault.
    """
    canary_path = Path.home() / ".claude" / "data" / "security_gate_canary.json"
    if not canary_path.exists():
        return 0.0
    try:
        from datetime import datetime, timezone
        d = json.loads(canary_path.read_text(encoding="utf-8", errors="ignore"))
        if not d.get("gate_working", False):
            return 0.0
        last_run_str = d.get("last_run", "")
        if not last_run_str:
            return 0.0
        last_run = datetime.fromisoformat(last_run_str.replace("Z", "+00:00"))
        if last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - last_run).days
        max_age = d.get("max_age_days", 7)
        return 0.0 if age_days > max_age else 1.0
    except Exception:
        return 0.0


def _secret_scrub_count(records: list[dict], repo_root: Path) -> int:  # noqa: ARG001
    """Total secrets auto-redacted across all scrubber runs from secret_scrubber.jsonl."""
    log = Path.home() / ".claude" / "data" / "secret_scrubber.jsonl"
    if not log.exists():
        return 0
    total = 0
    for line in log.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                total += int(obj.get("findings_count") or 0)
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
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
    return sum(1 for e in _load_autonomic_events(repo_root) if e.get("event") == "zombie_killed")


def _daily_ronin_spend(records: list[dict], repo_root: Path) -> float:  # noqa: ARG001
    """Dollars spent by the Ronin daemon today from state/budget_ledger.json.

    Resets to 0.0 each calendar day. Returns 0.0 when the ledger is absent or
    the date doesn't match today — no stale carry-over.
    """
    from datetime import date
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
    from datetime import date, timedelta
    state_file = repo_root / "state" / "DOJO_STATE.json"
    if not state_file.exists():
        return 0
    try:
        state = json.loads(state_file.read_text(encoding="utf-8", errors="ignore"))
        cutoff = str(date.today() - timedelta(days=7))
        count = 0
        for item in state.get("backlog", []):
            if item.get("status") != "done":
                continue
            completed = item.get("completed_at", "")
            if completed and completed >= cutoff:
                count += 1
        return count
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


def _metric_live_fraction(records: list[dict], repo_root: Path) -> float:  # noqa: ARG001
    """Fraction of the planned metric catalog that is wired and LIVE.

    TOTAL_PLANNED is the count of distinct metrics in METRICS.md (47 as of
    2026-06-07). Each time a new reducer lands in REGISTRY, this fraction rises.
    This is the primary system self-improvement signal — it answers:
    'How complete is Order Samurai's own observability?'
    """
    TOTAL_PLANNED = 47  # from METRICS.md header — update when catalog grows
    live_count = len(REGISTRY)  # evaluated lazily; at call time REGISTRY is fully built
    return round(live_count / TOTAL_PLANNED, 3)


def _skill_promotions(records: list[dict], repo_root: Path) -> int:  # noqa: ARG001
    """Count of skill promotions logged to ~/.claude/data/skill_promotion_log.jsonl.

    A promotion = a skill moved to a higher priority tier in the skills matrix.
    Returns 0 when the log is absent (no promotions yet).
    """
    log = Path.home() / ".claude" / "data" / "skill_promotion_log.jsonl"
    if not log.exists():
        return 0
    count = 0
    for line in log.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            if json.loads(line):
                count += 1
        except json.JSONDecodeError:
            continue
    return count


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
    # 1.0 = gate working + fresh; 0.0 = fault or stale (> max_age_days).
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
]


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
    return {
        "metric": name,
        "value": value,
        "source": entry["source"],
        "tier": entry["tier"],
        "live": True,
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
