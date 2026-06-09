"""Autonomic events scout.

Purpose: populate state/autonomic_events.jsonl from real harness data sources.
Owner: bow-pillar
Inputs:
  - ~/.claude/data/pipeline_errors.log (optional — skipped gracefully if absent)
Outputs:
  - state/autonomic_events.jsonl (appended, deduplicated by detail hash)
Failure modes:
  - Source file absent: writes no events, returns empty list (valid; Hook_Failure_Rate = 0.0)
  - Source file unreadable: same as absent
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

AUTONOMIC_EVENTS_PATH = REPO_ROOT / "state" / "autonomic_events.jsonl"
_CLAUDE_DATA_DIR = Path.home() / ".claude" / "data"
_PIPELINE_ERRORS_LOG = _CLAUDE_DATA_DIR / "pipeline_errors.log"


def _event_key(event: dict) -> str:
    """Stable deduplication key from event type + detail."""
    return hashlib.sha1(
        f"{event.get('event','')}{event.get('detail','')}".encode()
    ).hexdigest()


def _load_existing_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
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
        if not ts:
            entry["inferred_at"] = ts_final
        events.append(entry)
    return events


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

    return new_events


if __name__ == "__main__":
    written = run()
    print(f"autonomic_events_scout: wrote {len(written)} new event(s) to state/autonomic_events.jsonl")
