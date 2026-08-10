"""The canonical telemetry contract — the Data-layer interface every Execution run emits to.
Harvested from Jarvis `telemetry.jsonl` (schema 2026-04-07.1), normalized for vendor neutrality
(adds `platform`, forces `model_tier` to a string).

Also carries the metric-envelope honesty rule: every metric must declare a `tier`, so a
dashboard can never silently present a simulated number as a live one.
"""
from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "agentica.1"

# field name -> accepted type(s)
REQUIRED_FIELDS: dict[str, type | tuple[type, ...]] = {
    "schema_version": str,
    "timestamp": str,
    "platform": str,
    "project": str,
    "task_name": str,
    "model_tier": str,
    "latency_ms": (int, float),
    "tokens_prompt": int,
    "tokens_completion": int,
    "total_cost": (int, float),  # the token/cost signal the "optimize tokens" goal needs
    "status": str,
}

# Optional fields. Validated for type ONLY when present, so legacy task-level records (and any
# platform that doesn't emit them) still validate. These take the schema from task-level to
# orchestration-level (see Order Samurai/Research/METRICS.md instrumentation gaps).
OPTIONAL_FIELDS: dict[str, type | tuple[type, ...]] = {
    "error": str,
    "exit_code": int,           # harness exit code; presence proves the error channel is wired (Error_Rate falsifiability)
    "session_id": str,
    "turns": int,               # user back-and-forth prompt turns this session (cumulative
                                # snapshot like tool_calls — a resumed session re-emits higher;
                                # Avg_Session_Turns reducer takes max per session). 0 = not parsed.
    "tool_calls": int,
    "tool_calls_list": list,
    "tool_latencies": list,
    "mod_type": str,
    "skill_hits": int,
    # agent-operation extensions:
    "orchestrator": str,        # Master/Bow/Sword/Brush/Arts/none
    "chain_depth": int,         # orchestration fan-out: Agent+Task calls per session (NOT nesting depth)
    "subagent_spawns": int,     # Agent-only spawn count per session
    "parent_task": str,         # subagent cost attribution
    "knowledge_refs": int,      # knowledge/lessons surfaced
    "cache_read_tokens": int,   # prompt-cache reuse
    "model": str,               # concrete model id (Opus<20% adherence)
    "skill_tier": str,          # tool-wrapper/reviewer/generator/pipeline
    "mcp_or_cli": str,          # "mcp" | "cli"
    "phase": str,               # 7-phase workflow stage
    "approved": bool,           # gate approval recorded
    "outcome_ref": str,         # link to merged PR / resolved task
    # Arts / quality signals (transcript-derivable):
    "slop_markers": int,        # AI-slop marker count in agent output
    "output_words": int,        # agent output word count (slop-density denominator)
    "frustration_signals": int, # user turns expressing dissatisfaction
    "rework_turns": int,        # user turns requesting correction/redo
    "stop_hook_refires": int,   # Stop/goal-hook re-fire loops (assistant self-repetition against a stuck hook); emitter = stop-hook-breaker (CP-9)
    "skills_used": list,        # skill names invoked this session (Simplify Pass, dead-skill detection)
    "rule_violations": int,     # CLAUDE.md principle violations fired during this session window
    # git-platform fields (emitted by the post-commit hook; absent in session-level records)
    "commit_hash": str,         # SHA-1 of the git commit
    "files_changed": int,       # files changed in the commit
    "insertions": int,          # lines inserted
    "deletions": int,           # lines deleted
    # Sword / security signals:
    "dangerous_tool_invocations": int,  # PreToolUse guardrail blocks this session (source:
                                         # count_dangerous_tool_invocations(), reading the harness's
                                         # own ~/.claude/data/hook_timings.jsonl — hook=="guardrails",
                                         # status=="blocked"/exit_code==2; Research/METRICS.md Sword >
                                         # Agent Operation > Dangerous Tool Invocations, +FIELD)
}

# LLM-JUDGED: an LLM classifier-judge score (evals/), reproducible only up to model
# variance — honest-lower than deterministic DERIVED/AUTO, honest-higher than SIMULATED.
# Ratified 2026-07-15 for the Arts output-quality metrics; carries a "· llm-judged" badge.
VALID_TIERS = {"AUTO", "DERIVED", "SIMULATED", "SKILL", "LLM-JUDGED"}
VALID_TRENDS = {"up", "down", "neutral"}
_METRIC_REQUIRED = {"val", "delta", "trend", "history", "tier"}

# Autonomic / failure / governance events that are NOT task records — written to
# Data/telemetry/autonomic_events.jsonl (emit_event.py) and merged with the Order
# Samurai state/ scout stream by the +STREAM metric reducers in aggregate.py.
AUTONOMIC_EVENTS = {
    "zombie_killed", "daemon_restart", "heal", "drift_corrected", "boundary_blocked",
    "permission_escalation", "loop_breaker_fire", "hook_failure", "scope_change",
    "compaction", "mechanism_run", "rule_violation",
    "pipeline_error",  # emitted by the Order Samurai autonomic_events_scout
    # knowledge-layer recall telemetry — schema first; the hook emitter is staged behind
    # the control-plane gate (see HANDOFF 2026-07-06 <BRAND>³ governance wiring):
    "memory_recall", "recall_miss", "memory_contradicted",
}
EVENT_REQUIRED: dict[str, type | tuple[type, ...]] = {"timestamp": str, "event": str}


class TelemetryValidationError(ValueError):
    pass


def normalize_entry(entry: dict[str, Any], platform: str | None = None) -> dict[str, Any]:
    """Fill platform-neutral defaults so a raw platform record meets the canonical schema."""
    out = dict(entry)
    out.setdefault("schema_version", SCHEMA_VERSION)
    out.setdefault("session_id", "local-session")
    if platform is not None:
        out.setdefault("platform", platform)
    if out.get("model_tier") is not None:
        out["model_tier"] = str(out["model_tier"])
    return out


def validate_entry(entry: dict[str, Any]) -> None:
    for name, types in REQUIRED_FIELDS.items():
        if name not in entry:
            raise TelemetryValidationError(f"missing required field: {name}")
        value = entry[name]
        if value is None or not isinstance(value, types):
            raise TelemetryValidationError(
                f"field {name} must be {types}, got {type(value).__name__}"
            )
        # bool is a subclass of int — reject it where a number is expected
        if types in ((int, float), int) and isinstance(value, bool):
            raise TelemetryValidationError(f"field {name} must not be a bool")

    # optional fields: validate type only when present and non-null
    for name, types in OPTIONAL_FIELDS.items():
        if entry.get(name) is None:
            continue
        value = entry[name]
        if not isinstance(value, types):
            raise TelemetryValidationError(
                f"optional field {name} must be {types}, got {type(value).__name__}"
            )
        if types is int and isinstance(value, bool):  # int field, not a bool (approved is bool by design)
            raise TelemetryValidationError(f"field {name} must not be a bool")
    if entry["status"] not in ("success", "error"):
        raise TelemetryValidationError(
            f"status must be 'success' or 'error', got {entry['status']!r}"
        )


def validate_metric(node: dict[str, Any]) -> None:
    """Honesty rule: every metric must declare a tier and a valid trend."""
    missing = _METRIC_REQUIRED - node.keys()
    if missing:
        raise TelemetryValidationError(f"metric missing keys: {sorted(missing)}")
    if node["tier"] not in VALID_TIERS:
        raise TelemetryValidationError(f"tier must be one of {sorted(VALID_TIERS)}, got {node['tier']!r}")
    if node["trend"] not in VALID_TRENDS:
        raise TelemetryValidationError(f"trend must be one of {sorted(VALID_TRENDS)}, got {node['trend']!r}")


def parse_ts(ts: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp (tolerating a trailing 'Z'). None if unparseable.
    The single shared parser — callers must not reimplement the 'Z' fix-up."""
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None


@lru_cache(maxsize=32768)
def _iso_week_text(ts: str) -> str | None:
    dt = parse_ts(ts)
    return dt.strftime("%G-W%V") if dt else None


def iso_week(ts: Any) -> str | None:
    """ISO year-week label ('%G-W%V', e.g. '2026-W22') for a timestamp, or None."""
    return _iso_week_text(str(ts))


def default_telemetry_path() -> Path:
    """Agentica OS/Data/telemetry/telemetry.jsonl, relative to this file
    (Governance/agentica_core/telemetry.py -> ../../Data/...)."""
    governance = Path(__file__).resolve().parent.parent
    return governance.parent / "Data" / "telemetry" / "telemetry.jsonl"


def append_entry(entry: dict[str, Any], path: Path | None = None) -> Path:
    """Validate, then append one record as a JSONL line. Returns the file written to."""
    validate_entry(entry)
    target = path or default_telemetry_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    return target


# --- Autonomic / failure / governance event stream ---------------------------------------

def default_events_path() -> Path:
    governance = Path(__file__).resolve().parent.parent
    return governance.parent / "Data" / "telemetry" / "autonomic_events.jsonl"


def validate_event(event: dict[str, Any]) -> None:
    for name, types in EVENT_REQUIRED.items():
        if name not in event or not isinstance(event[name], types):
            raise TelemetryValidationError(f"event field {name} must be {types}")
    if event["event"] not in AUTONOMIC_EVENTS:
        raise TelemetryValidationError(
            f"unknown event {event['event']!r}; valid: {sorted(AUTONOMIC_EVENTS)}"
        )


def append_event(event: dict[str, Any], path: Path | None = None) -> Path:
    """Validate, then append one autonomic event. Shape: {timestamp, event, pillar?, detail?, duration_ms?}."""
    validate_event(event)
    target = path or default_events_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")
    return target


# --- Sword / security signal readers ------------------------------------------------------
# Read-only over the harness's own control-plane logs under ~/.claude/data/ — the pillar
# never writes there (see prompts/ronin_sword.md scope boundary).

def default_hook_timings_path() -> Path:
    """~/.claude/data/hook_timings.jsonl — every PreToolUse/PostToolUse hook dispatch,
    written by hook_dispatch.py's own `_log_timing` (real, pre-existing harness log;
    not something this pillar emits). Each record: {ts, hook, event, status, duration_ms,
    exit_code, platform}."""
    return Path.home() / ".claude" / "data" / "hook_timings.jsonl"


def count_dangerous_tool_invocations(path: Path | None = None) -> int:
    """Count real `guardrails` PreToolUse hook blocks (status=="blocked", i.e. exit_code==2 —
    a dangerous Bash/git pattern or a protected-path Read was stopped) recorded in
    hook_timings.jsonl. This is the FIELD-tier source for Sword's Dangerous_Tool_Invocations
    (Research/METRICS.md): a real signal the harness already writes on every dangerous-command
    block, not a fabricated count. Fail-open like every other reader in this module — an
    absent/unreadable log returns 0, it does not raise."""
    target = path or default_hook_timings_path()
    if not target.exists():
        return 0
    count = 0
    try:
        with target.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict):
                    continue
                if rec.get("hook") != "guardrails":
                    continue
                if rec.get("status") == "blocked" or rec.get("exit_code") == 2:
                    count += 1
    except OSError:
        return 0
    return count
