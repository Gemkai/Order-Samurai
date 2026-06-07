"""Order Samurai metric aggregator.

REGISTRY: the single source of truth for all LIVE metrics.
Each entry declares a reducer that reads from a real source — never invented.

Build order (METRICS.md):
  1. telemetry.py  — schema  (done)
  2. aggregate.py  — REGISTRY + load_telemetry_records  (this file)
  3. autonomic_events emitter  — future
"""
from __future__ import annotations

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
    """Read state/autonomic_events.jsonl and return event dicts."""
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
    value = entry["reducer"](records, repo_root)
    return {
        "metric": name,
        "value": value,
        "source": entry["source"],
        "tier": entry["tier"],
        "live": True,
    }
