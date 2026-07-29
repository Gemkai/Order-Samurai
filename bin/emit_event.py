"""BOW-001: CLI to append an autonomic event to the autonomic_events.jsonl stream.

Usage:
    emit_event.py <event_type> [--pillar bow|sword|brush|arts] [--detail TEXT] [--duration_ms N]

Exit codes: 0 success, 1 failure.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Resolve agentica_core on the import path without requiring an install, by
# MARKER rather than by depth.
#
#   live tree:      <repo>/Governance/Order Samurai/bin/emit_event.py
#                   -> agentica_core is 3 parents up, under Governance/
#   public export:  <root>/bin/emit_event.py   (the pack is flattened to the root)
#                   -> agentica_core is 1 parent up
#
# A fixed `parents[2]` describes only the first. In the export it resolved to a
# directory OUTSIDE the distribution entirely, so the import failed and the
# fallback wrote reflex-engine events to a path no consumer reads — the same
# silent-dead-end this file's own docstring records from the pre-relocation era,
# reintroduced by a hard-coded depth.
# ---------------------------------------------------------------------------
def _resolve_governance() -> Path:
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "agentica_core" / "telemetry.py").is_file():
            return candidate
    return here.parents[2]  # nothing found: keep the historical guess for the error path


_GOVERNANCE = _resolve_governance()
if str(_GOVERNANCE) not in sys.path:
    sys.path.insert(0, str(_GOVERNANCE))

try:
    from agentica_core.telemetry import (
        AUTONOMIC_EVENTS,
        append_event,
        default_events_path,
    )
    _IMPORT_OK = True
except ImportError:
    _IMPORT_OK = False
    # Fallback: hardcode the canonical set so the script still works offline.
    AUTONOMIC_EVENTS = {
        "zombie_killed", "daemon_restart", "heal", "drift_corrected",
        "boundary_blocked", "permission_escalation", "loop_breaker_fire",
        "hook_failure", "scope_change", "compaction", "mechanism_run",
        "rule_violation",
    }

    def default_events_path() -> Path:
        desktop = Path.home() / "Desktop"
        return desktop / "Agentica OS" / "Data" / "telemetry" / "autonomic_events.jsonl"

    def append_event(event: dict, path: Path | None = None) -> Path:
        target = path or default_events_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")
        return target


VALID_PILLARS = {"bow", "sword", "brush", "arts"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Append an autonomic event to the governance telemetry stream.",
    )
    parser.add_argument(
        "event_type",
        help=f"One of: {', '.join(sorted(AUTONOMIC_EVENTS))}",
    )
    parser.add_argument(
        "--pillar",
        choices=sorted(VALID_PILLARS),
        default=None,
        help="Pillar context: bow | sword | brush | arts",
    )
    parser.add_argument("--detail", default=None, help="Free-text detail string")
    parser.add_argument(
        "--duration_ms",
        type=int,
        default=None,
        help="Duration in milliseconds (integer)",
    )
    parser.add_argument(
        "--routing-efficient",
        action="store_true",
        default=False,
        help="Mark event as routing-efficient (mechanism ran instead of LLM skill)",
    )
    parser.add_argument("--kind", default=None, help="Execution kind: 'mechanism' or 'skill'")
    args = parser.parse_args(argv)

    # Validate event type.
    if args.event_type not in AUTONOMIC_EVENTS:
        print(
            f"error: unknown event_type {args.event_type!r}. "
            f"Valid values: {', '.join(sorted(AUTONOMIC_EVENTS))}",
            file=sys.stderr,
        )
        return 1

    # Build the event dict — omit None fields so the record stays clean.
    event: dict = {"timestamp": datetime.now(timezone.utc).isoformat(), "event": args.event_type}
    if args.pillar is not None:
        event["pillar"] = args.pillar
    if args.detail is not None:
        event["detail"] = args.detail
    if args.duration_ms is not None:
        event["duration_ms"] = args.duration_ms
    if args.routing_efficient:
        event["routing_efficient"] = True
    if args.kind is not None:
        event["kind"] = args.kind

    try:
        target = append_event(event)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"event emitted: {args.event_type}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
