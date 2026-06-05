"""Canonical telemetry record schema for Order Samurai.

All fields are optional — the schema represents what MAY be in a telemetry
record.  Emitters populate only the fields they have evidence for; aggregators
treat absent fields as None (never invent values).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class TelemetryRecord:
    # Tool routing
    mcp_or_cli: Optional[str] = None          # "mcp" or "cli" — which path was taken
    # Model tier
    model_tier: Optional[str] = None          # "LOCAL", "CLOUD", etc.
    # Edit type
    mod_type: Optional[str] = None            # "CLOBBER" or "SURGICAL"
    # Tool call counts
    tool_calls: Optional[int] = None
    tool_calls_list: Optional[List[str]] = field(default=None)
    # Session identity
    session_id: Optional[str] = None
    task_name: Optional[str] = None
    # Cost / token fields
    total_cost: Optional[float] = None
    tokens_prompt: Optional[int] = None
    tokens_completion: Optional[int] = None
    # Outcome
    status: Optional[str] = None             # "ok", "error", etc.
    # Skills / orchestration
    skill_hits: Optional[List[str]] = field(default=None)
    subagent_spawns: Optional[int] = None
    # Timing
    timestamp: Optional[str] = None
