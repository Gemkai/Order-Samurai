"""Telemetry-freshness gate: FAIL when the Claude telemetry stream has gone quiet.

The June 2026 outage: the SessionEnd telemetry hook swallowed every error while
its kernel path was dead, so ~/.claude/telemetry/telemetry.jsonl silently stopped
growing for 15 days and nothing noticed. Existence checks can't catch that — the
file was there the whole time. This gate reads the NEWEST record timestamp and
FAILS (exit-code-affecting, not a note) when it is older than MAX_AGE_HOURS.

Why record timestamp, not mtime: backfills and permission fixes touch mtime
without proving the emitter is alive; only a recent record timestamp does.

Threshold rationale: sessions happen daily on this machine; 48h of zero records
means the emitter is dead, not that the operator took a quiet day.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.verifier_results import make_result as _make_result  # noqa: F401
from execution.verifier_results import summarize  # noqa: F401  (re-exported for doctor/CLI)

MAX_AGE_HOURS = 48
_LABEL = "telemetry-freshness"


def _default_telemetry_path() -> Path:
    """The Claude platform's telemetry source. Single source of truth is
    agentica_core/platforms.json (via the adapter); the literal home path is
    only a fallback for environments where agentica_core is not importable."""
    try:
        _governance = ROOT_DIR.parent
        if str(_governance) not in sys.path:
            sys.path.insert(0, str(_governance))
        from agentica_core.adapter import resolve_platform
        return resolve_platform("claude").telemetry_source
    except Exception:  # noqa: BLE001
        # Broad and silent on purpose. This resolves WHERE to look, not whether the
        # stream is fresh — and the fallback is the same canonical path the adapter
        # would return. Letting an adapter defect propagate would crash doctor
        # (run_families does not wrap its runners), turning a locatable telemetry
        # file into no health report at all. The freshness FAIL below is the gate;
        # this is only the lookup.
        return Path.home() / ".claude" / "telemetry" / "telemetry.jsonl"


def _newest_record_ts(path: Path, now: datetime) -> datetime | None:
    """Max parseable record timestamp in the stream that is not in the future.
    Full scan on purpose: backfilled records are appended out of chronological
    order, so the last line is not necessarily the newest record.

    A future timestamp (clock skew, or a corrupt/malicious record) is not a
    legitimate "newest" -- letting one win would drive age_hours negative and
    mask a genuinely dead emitter as OK, defeating the whole gate."""
    newest: datetime | None = None
    try:
        with path.open(encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line).get("timestamp")
                    ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                except (ValueError, TypeError, AttributeError):
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts > now:
                    continue
                if newest is None or ts > newest:
                    newest = ts
    except OSError:
        return None
    return newest


def run_checks(path: Path | None = None,
               max_age_hours: float = MAX_AGE_HOURS,
               now: datetime | None = None) -> list[dict[str, str]]:
    target = path or _default_telemetry_path()
    now = now or datetime.now(timezone.utc)

    if not target.exists():
        return [_make_result("FAIL", _LABEL,
                             f"telemetry stream missing: {target} — the SessionEnd "
                             f"emitter has no output at all")]

    newest = _newest_record_ts(target, now)
    if newest is None:
        return [_make_result("FAIL", _LABEL,
                             f"no parseable record timestamps in {target} — emitter "
                             f"output is corrupt or empty")]

    age_hours = (now - newest).total_seconds() / 3600
    if age_hours > max_age_hours:
        return [_make_result("FAIL", _LABEL,
                             f"newest telemetry record is {age_hours:.0f}h old "
                             f"(> {max_age_hours:g}h) — the SessionEnd emitter is dead "
                             f"(this exact failure ran 15 days undetected in June 2026); "
                             f"check ~/.claude hooks and Governance/data/pipeline_errors.log")]
    return [_make_result("OK", _LABEL,
                         f"newest telemetry record is {age_hours:.1f}h old "
                         f"(gate: {max_age_hours:g}h)")]


def main() -> int:
    results = run_checks()
    for result in results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")
    _, exit_code = summarize(results)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
