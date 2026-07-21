"""Telemetry emitter — the bridge that turns the canonical schema into REAL records.

A hook (e.g. a Claude Stop / PostToolUse hook) or any script calls `emit()`; the agent-operation
metrics go live as the agent actually works. Writes a validated canonical record to the resolved
platform's telemetry_source. This is what makes Claude's metrics real (it emits nothing today).

Hook wiring (Claude): a Stop hook running
    python -m agentica_core.emit claude '{"task_name":"...","tokens_prompt":...,"total_cost":...}'
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .adapter import resolve_platform
from .telemetry import append_entry, normalize_entry, validate_entry


def build_record(platform: str, task_name: str, *, status: str = "success", latency_ms: float = 0.0,
                 tokens_prompt: int = 0, tokens_completion: int = 0, total_cost: float = 0.0,
                 project: str = "unknown", model_tier: str = "unknown",
                 timestamp: str | None = None, **optional) -> dict:
    """Build a canonical record. `optional` carries agent-operation fields
    (orchestrator, model, mcp_or_cli, phase, chain_depth, subagent_spawns, knowledge_refs, ...)."""
    rec = {
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "project": project, "task_name": task_name, "model_tier": str(model_tier),
        "latency_ms": float(latency_ms), "tokens_prompt": int(tokens_prompt),
        "tokens_completion": int(tokens_completion), "total_cost": float(total_cost),
        "status": status,
    }
    rec.update({k: v for k, v in optional.items() if v is not None})
    return normalize_entry(rec, platform=platform)


def emit(platform: str, task_name: str, *, path: Path | None = None, **kwargs) -> Path:
    """Build, validate, and append a canonical record to the platform's telemetry_source."""
    target = path or resolve_platform(platform).telemetry_source
    rec = build_record(platform, task_name, **kwargs)
    validate_entry(rec)
    return append_entry(rec, path=target)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    platform = argv[0] if argv else os.environ.get("AGENTICA_PLATFORM", "claude")
    raw = argv[1] if len(argv) > 1 else sys.stdin.read()
    data = json.loads(raw) if raw.strip() else {}
    task_name = data.pop("task_name", "session")
    print(str(emit(platform, task_name, **data)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
