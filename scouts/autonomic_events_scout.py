"""Autonomic events scout.

Purpose: populate state/autonomic_events.jsonl from real harness data sources, and
  (AUTO-019) bridge the real ~/.claude/data/mechanism_audit.json health-check output
  into the CANONICAL cross-platform Data/telemetry/autonomic_events.jsonl stream as
  mechanism_run events — the source aggregate.py's bow/Autonomic/Mechanism_Liveness
  reducer reads.
Owner: bow-pillar
Inputs:
  - ~/.claude/data/pipeline_errors.log (optional — skipped gracefully if absent)
  - ~/.claude/data/mechanism_audit.json (optional — written by the mechanism_audit
    SessionStart health-check; skipped gracefully if absent/malformed)
Outputs:
  - state/autonomic_events.jsonl (appended, deduplicated by detail hash)
  - Data/telemetry/autonomic_events.jsonl (canonical stream; mechanism_run events only,
    deduplicated the same way, via agentica_core.telemetry.append_event)
Failure modes:
  - Source file absent: writes no events, returns empty list (valid; Hook_Failure_Rate = 0.0)
  - Source file unreadable: same as absent
  - agentica_core unimportable / canonical stream unwritable: mechanism_run emission is
    skipped silently — never breaks the primary local-stream scout job
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Canonical repo-root resolution (same pattern as execution/*.py)
_HERE = Path(__file__).resolve()
REPO_ROOT = _HERE.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# agentica_core is the canonical Governance kernel (parents[2]), not this repo — same
# bootstrap pattern as scouts/vibe_alignment_scout.py.
_GOVERNANCE = _HERE.parents[2]
if str(_GOVERNANCE) not in sys.path:
    sys.path.insert(0, str(_GOVERNANCE))

AUTONOMIC_EVENTS_PATH = REPO_ROOT / "state" / "autonomic_events.jsonl"
_CLAUDE_DATA_DIR = Path.home() / ".claude" / "data"
_PIPELINE_ERRORS_LOG = _CLAUDE_DATA_DIR / "pipeline_errors.log"
_MECHANISM_AUDIT_JSON = _CLAUDE_DATA_DIR / "mechanism_audit.json"


def _event_key(event: dict) -> str:
    """Stable deduplication key from event type + detail."""
    return hashlib.sha1(
        f"{event.get('event','')}{event.get('detail','')}".encode()
    ).hexdigest()


def _load_existing_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    keys: set[str] = set()
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                keys.add(_event_key(obj))
        except json.JSONDecodeError:
            continue
    return keys


_TS_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\]\s+\[([^\]]+)\]\s+(.*)")

# Hook names that represent actual Claude Code hook failures (inflates Hook_Failure_Rate).
# Other bracketed names (LLM-ROUTER, VALIDATOR, etc.) are pipeline errors, not hook failures.
_HOOK_NAMES = {
    "CLAUDEMD-IMPROVE", "MEX-INIT", "LOOP-BREAKER", "GUARDRAILS",
    "MECHANISM-AUDIT", "SECURITY-GATE", "PHASE-WORKFLOW", "LESSON-REVIEW",
    "SESSION-OPERATOR", "PREFETCH", "SKILL-SECURITY",
}


def _read_hook_failures() -> list[dict]:
    """Extract hook_failure and pipeline_error events from pipeline_errors.log.

    Only processes timestamped summary lines — skips traceback continuations.
    Parses the actual event timestamp rather than using now() so events are
    temporally meaningful in autonomic_events.jsonl.
    """
    if not _PIPELINE_ERRORS_LOG.exists():
        return []
    try:
        lines = _PIPELINE_ERRORS_LOG.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []

    events = []
    for line in lines:
        m = _TS_RE.match(line.strip())
        if not m:
            continue  # skip traceback continuation lines
        raw_ts, hook_name, detail = m.groups()
        try:
            ts = datetime.fromisoformat(raw_ts).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            ts = None

        event_type = "hook_failure" if hook_name in _HOOK_NAMES else "pipeline_error"
        ts_final = ts or datetime.now(timezone.utc).isoformat()
        entry = {
            "event": event_type,
            "pillar": "bow",
            "detail": f"[{hook_name}] {detail}"[:300],
            "duration_ms": 0,
            "timestamp": ts_final,
        }
        if ts is None:
            entry["inferred_at"] = ts_final
        events.append(entry)
    return events


def _read_mechanism_audit_event() -> dict | None:
    """Build one mechanism_run event from the real mechanism-audit health-check output.

    Source: ~/.claude/data/mechanism_audit.json, written by the mechanism_audit
    SessionStart hook. Its `counts` are already read downstream by
    agentica_core.scouts.security_signals() into bow/Autonomic/Mechanism_Orphans — real,
    pre-existing consumption. This event records that the audit mechanism itself ran and
    had its output consumed (the 3-step Mechanism Rule: registered as a SessionStart
    hook, verified to run via a fresh generated_at, output verified consumed by the
    Mechanism_Orphans reducer). Keyed by the audit's own generated_at so a stale/unrun
    audit never re-emits a duplicate. Returns None (no fabrication) when the file is
    absent or doesn't have the expected shape — never guesses a field.
    """
    if not _MECHANISM_AUDIT_JSON.exists():
        return None
    try:
        data = json.loads(_MECHANISM_AUDIT_JSON.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    generated_at = data.get("generated_at")
    counts = data.get("counts")
    if not isinstance(generated_at, str) or not isinstance(counts, dict):
        return None
    try:
        c = int(counts.get("critical", 0) or 0)
        o = int(counts.get("orphan", 0) or 0)
        w = int(counts.get("warning", 0) or 0)
        i = int(counts.get("info", 0) or 0)
    except (TypeError, ValueError):
        return None
    ts = generated_at if "T" in generated_at else f"{generated_at}T00:00:00"
    return {
        "event": "mechanism_run",
        "pillar": "bow",
        "mechanism": "mechanism_audit",
        "detail": (f"mechanism_audit ran at {generated_at}: critical={c} orphan={o} "
                   f"warning={w} info={i} — consumed by bow/Autonomic/Mechanism_Orphans"),
        "duration_ms": 0,
        "timestamp": ts,
    }


def _emit_mechanism_run_to_canonical() -> dict | None:
    """Append the mechanism_audit-sourced mechanism_run event to the CANONICAL
    cross-platform stream (Data/telemetry/autonomic_events.jsonl) — the path
    aggregate.py's bow/Autonomic/Mechanism_Liveness reducer reads
    (agentica_core.telemetry.default_events_path()), NOT the local
    state/autonomic_events.jsonl file this scout otherwise writes to. Idempotent:
    skipped when an event with the same (event, detail) already exists in the
    canonical file. Returns the emitted event, or None if there was nothing new
    to emit (source absent, malformed, already emitted, or agentica_core/canonical
    stream unavailable)."""
    event = _read_mechanism_audit_event()
    if event is None:
        return None
    try:
        from agentica_core.telemetry import append_event, default_events_path
    except Exception:
        return None
    try:
        canonical_path = default_events_path()
        existing = _load_existing_keys(canonical_path)
        if _event_key(event) in existing:
            return None
        append_event(event, path=canonical_path)
    except Exception:
        return None
    return event


def run(repo_root: Path = REPO_ROOT) -> list[dict]:
    """Read real sources and emit new events to state/autonomic_events.jsonl.

    Returns list of newly-appended event dicts (empty if no new events).
    Idempotent: re-running does not duplicate events.
    """
    out_path = repo_root / "state" / "autonomic_events.jsonl"
    existing_keys = _load_existing_keys(out_path)

    candidates = _read_hook_failures()
    new_events = [e for e in candidates if _event_key(e) not in existing_keys]

    if new_events:
        with out_path.open("a", encoding="utf-8") as fh:
            for e in new_events:
                fh.write(json.dumps(e) + "\n")
    elif not out_path.exists():
        # Ensure file exists even with no events (reducer reads it safely)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.touch()

    # AUTO-019: bridge the real mechanism_audit health-check into the canonical
    # cross-platform stream. Never let this break the scout's primary local-stream job.
    try:
        _emit_mechanism_run_to_canonical()
    except Exception:
        pass

    return new_events


if __name__ == "__main__":
    written = run()
    print(f"autonomic_events_scout: wrote {len(written)} new event(s) to state/autonomic_events.jsonl")
