"""Telemetry-record and transcript primitives for the aggregator: session-id and dedup helpers, percentile and numeric extraction, window and week anchors, prior-week lookups, transcript scanning and usage labels (split from aggregate.py)."""
from __future__ import annotations

import json
import os
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Any

from . import remediation
from .telemetry import iso_week, parse_ts


# ---------------------------------------------------------------- reducers (records -> value|None)
def _nums(records: list[dict], field: str) -> list[float]:
    return [r[field] for r in records
            if isinstance(r.get(field), (int, float)) and not isinstance(r.get(field), bool)]


# session_id values that are PLACEHOLDERS, not identities. The antigravity
# emitter stamps every record "local-session", and telemetry.normalize_entry
# defaults absent sids to the same string. Keying per-session dedup on it
# collapsed 3,300 distinct antigravity task records into ONE "session" —
# max-per-session kept a single record's cost/tokens and discarded the rest
# ($28.76 true spend reported as $0.075; 2026-07-12 audit). Placeholder-sid
# records must always take the loose (count-individually) path.
_PLACEHOLDER_SIDS = frozenset({"local-session"})


def _real_sid(r: dict) -> str | None:
    """The record's session_id when it is a genuine identity, else None."""
    sid = r.get("session_id")
    if not sid or sid in _PLACEHOLDER_SIDS:
        return None
    return sid


def _dedup_field(records: list[dict], field: str) -> tuple[dict[str, float], list[float]]:
    """Per-session dedup for cumulative snapshot fields (tokens_prompt/completion, total_cost).

    The Claude SessionEnd emitter (scripts/agentica_emit.py) writes ONE record per session
    from the whole transcript, so a resumed session re-emits the SAME session_id with higher
    cumulative totals (verified: session 12e49f9b… emitted 20 rows, tokens_completion rising
    684k→1009k). Summing raw double-counts a single session up to 20x (~46% of windowed rows
    are such re-emits). Each session therefore contributes its MAX (latest cumulative) value
    once. Mirrors _sum_per_session() but preserves floats (that helper int()-truncates, which
    is wrong for dollar costs). Records with no REAL session_id (absent or placeholder)
    can't be deduped → counted individually. Returns (max-per-session dict, loose values)."""
    by_sess: dict[str, float] = {}
    loose: list[float] = []
    for r in records:
        v = r.get(field)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        sid = _real_sid(r)
        if sid:
            by_sess[sid] = max(by_sess.get(sid, 0.0), float(v))
        else:
            loose.append(float(v))
    return by_sess, loose


def _count_success_sessions(records: list[dict]) -> int:
    """Distinct successful sessions (each real session_id once) + loose success records."""
    sids: set = set()
    loose = 0
    for r in records:
        if r.get("status") != "success":
            continue
        sid = _real_sid(r)
        if sid:
            sids.add(sid)
        else:
            loose += 1
    return len(sids) + loose


def _pctile(vals: list[float], p: float):
    if not vals:
        return None
    s = sorted(vals)
    k = (len(s) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return round(s[f] + (s[c] - s[f]) * (k - f), 1)


def _w_num(v) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def _int_vals(recs, field):
    """Integer values of `field` across records (bools/non-numerics dropped). Returns the
    list — caller decides sum-vs-None — hence not named '_isum'."""
    return [int(r[field]) for r in recs
            if isinstance(r.get(field), (int, float)) and not isinstance(r.get(field), bool)]


def _sum_per_session(recs, field) -> int:
    """Sum `field` counting each session once (max value seen per real session_id).

    These are per-session counters (rule_violations, frustration_signals, …). When a
    session is logged to telemetry more than once the static count is re-emitted, so
    summing raw records double-counts it. Records without a REAL session_id (absent
    or placeholder) can't be deduplicated, so each is counted individually."""
    by_sess: dict = {}
    loose: list[int] = []
    for r in recs:
        v = r.get(field)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        sid = _real_sid(r)
        if sid:
            by_sess[sid] = max(by_sess.get(sid, 0), int(v))
        else:
            loose.append(int(v))
    return sum(by_sess.values()) + sum(loose)


def _has_field(recs, field) -> bool:
    return any(
        isinstance(v := r.get(field), (int, float)) and not isinstance(v, bool)
        for r in recs
    )


def _newest_jsonls(projects_dir: Path, limit: int) -> list[Path]:
    """The `limit` most recently modified transcripts, oldest first.

    Transcripts can vanish between rglob and stat (factory worktree cleanup); those are
    skipped rather than raised, so one deleted file cannot fail the whole refresh."""
    stamped = []
    for path in projects_dir.rglob("*.jsonl"):
        try:
            stamped.append((path.stat().st_mtime, path))
        except OSError:
            continue
    stamped.sort(key=lambda item: item[0])
    return [path for _, path in stamped[-limit:]]


def _file_signature(paths: list[Path]) -> tuple:
    """Cheap change key for file-backed reducers; missing paths stay visible."""
    parts = []
    for path in paths:
        try:
            stat = path.stat()
            parts.append((str(path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            parts.append((str(path), None, None))
    return tuple(parts)


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


def _get_weekly_promotions_count(now: datetime) -> int:
    log_path = Path.home() / ".claude" / "data" / "skill_promotion_log.jsonl"
    if not log_path.exists():
        return 0
    count = 0
    this_week = now.strftime("%G-W%V")
    try:
        for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                ts_val = obj.get("timestamp") or obj.get("ts") or obj.get("created_at")
                if ts_val:
                    dt = datetime.fromisoformat(ts_val.replace("Z", "+00:00"))
                    if dt.strftime("%G-W%V") == this_week:
                        count += 1
            except Exception:
                continue
    except Exception:
        pass
    return count


def _get_prior_week_val(history_path: Path, metric_key: str,
                        before_week: str | None = None) -> float | None:
    """Latest history value for metric_key; with before_week (\"%G-W%V\"), only
    snapshots from a strictly earlier ISO week count — otherwise the most recent
    snapshot may already contain the current week and deltas self-compare to 0."""
    if not history_path.exists():
        return None
    try:
        lines = history_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if not line.strip():
            continue
        # Per-line try/except (matching load_records/_load_history/populate_history):
        # one malformed row anywhere above the wanted week must not abort the scan
        # and hide every valid week that exists earlier in the file.
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        # Live rows are computed over the payload's 30-day window, not one ISO
        # week — using one as a week baseline made the savings figure swing
        # ~2x on scheduler timing (whether backfill had pruned it yet).
        # Weekly baselines come from kind:"weekly" (or legacy un-kinded) rows.
        if obj.get("kind") == "live":
            continue
        if before_week is not None:
            ts = _parse_iso(obj.get("ts"))
            # zero-padded %G-W%V strings order lexicographically
            if ts is None or ts.strftime("%G-W%V") >= before_week:
                continue
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
    return None


_MCP_UUID_NAMES: dict[str, str] = {
    "2f62a1e0": "Vercel", "951222fb": "Exa Search", "952281cb": "Gmail",
    "b39b0009": "Google Calendar", "d66590cf": "Supabase", "0667bb3a": "Drive",
    "9aa7cbe6": "Context7", "831d333c": "Visualize", "becec896": "Stripe",
    "694eaac4": "Firecrawl",
}


_MCP_SERVER_DISPLAY: dict[str, str] = {
    "ccd_session": "CCD Session", "ccd_directory": "CCD Directory",
    "Claude_Preview": "Claude Preview", "Claude_in_Chrome": "Chrome MCP",
    "computer-use": "Computer Use", "scheduled-tasks": "Scheduled Tasks",
    "mcp-registry": "MCP Registry",
    "plugin_engineering_github": "GitHub Plugin", "plugin_engineering_datadog": "Datadog Plugin",
    "plugin_productivity_linear": "Linear Plugin", "plugin_productivity_notion": "Notion Plugin",
}


def _mcp_server_label(server: str) -> str:
    if server in _MCP_SERVER_DISPLAY:
        return _MCP_SERVER_DISPLAY[server]
    slug = server.replace("-", "").replace("_", "")[:8].lower()
    for prefix, name in _MCP_UUID_NAMES.items():
        if slug.startswith(prefix[:8]):
            return name
    return server.replace("_", " ").replace("-", " ").title()


# Safety valve on the file scan, not a real limit: callers window this list down to
# 1d/7d/30d/all-time (refresh_dashboard.py's "all" variant uses window_days=36500),
# so the scan must cover every session file that could fall in the widest window, or
# the narrower windows silently truncate too (a fixed 40-file cap once made "top
# agents, last 7 days" actually mean "top agents in whatever sliver of today those 40
# files happened to span" — found 2026-08-14, 589 files were touched in a real 7-day
# window). Measured at ~3s for the full ~2.3k-file corpus (cheap '"Agent"' substring
# pre-filter below), well inside the 60s cache TTL, so there's no perf reason to cap
# tighter. This ceiling only exists to bound runaway growth, not to bind in practice.
_AGENT_SPAWN_FILE_CAP = 10_000


def _agent_spawn_events() -> list[tuple[str, str]]:
    """(timestamp, subagent_type) for Agent tool_use entries across up to
    _AGENT_SPAWN_FILE_CAP most recently touched session JSONLs — effectively all of
    them at current volume, so windowed counts (1d/7d/30d/all) are accurate rather
    than truncated to whatever a small fixed file cap happened to cover."""
    from . import aggregate as _agg
    import json as _json
    now = time.monotonic()
    if (_agg._AGENT_SPAWN_CACHE["v"] is not None
            and now - _agg._AGENT_SPAWN_CACHE["t"] < _SCOUT_CACHE_TTL_SEC):
        return _agg._AGENT_SPAWN_CACHE["v"]
    projects_dir = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".claude" / "projects"
    events: list[tuple[str, str]] = []
    if not projects_dir.exists():
        return events
    jsonls = _newest_jsonls(projects_dir, _AGENT_SPAWN_FILE_CAP)
    for jl in jsonls:
        try:
            with open(jl, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if '"Agent"' not in line:  # cheap pre-filter before json.loads
                        continue
                    try:
                        entry = _json.loads(line)
                        if entry.get("type") != "assistant":
                            continue
                        for c in (entry.get("message") or {}).get("content") or []:
                            if (isinstance(c, dict) and c.get("type") == "tool_use"
                                    and c.get("name") == "Agent"):
                                st = (c.get("input") or {}).get("subagent_type")
                                if st:
                                    events.append((entry.get("timestamp", ""), st))
                    except Exception:
                        pass
        except Exception:
            pass
    _agg._AGENT_SPAWN_CACHE.update(t=now, v=events)
    return events


def _count_agent_types(window_days: int | None = None) -> Counter:
    """Count Agent subagent_type invocations, optionally windowed by timestamp."""
    return Counter(
        st for ts, st in _agent_spawn_events()
        if window_days is None or _within_days(ts, window_days)
    )


def _record_set_week(records: list[dict]) -> str | None:
    """The latest ISO week ('%G-W%V') actually present in `records`, or None when the
    set carries no parseable timestamp. Zero-padded %G-W%V strings order
    lexicographically, so max() is the newest week."""
    weeks = [w for w in (iso_week(r.get("timestamp", "")) for r in records) if w]
    return max(weeks) if weeks else None


def _target_week_anchor(records: list[dict]) -> datetime:
    """The instant a WEEKLY reducer should treat as "now" — the Monday of the newest
    ISO week in `records`, falling back to wall-clock when the set is empty or carries
    no parseable timestamp.

    2026-08-08 metric-honesty class sweep. Every weekly reducer here opened with
    `now = datetime.now(timezone.utc)`. That is correct for a live refresh (the newest
    record IS today, so this helper returns the same week) but wrong for a REPLAY:
    backfill_history.py feeds build_pillars() one historical ISO week of telemetry at a
    time, and a wall-clock `now` made every one of those rows describe the CURRENT week
    instead — re-stamping today's state across the whole rebuilt series and fabricating
    a flat history. Anchoring on Monday keeps `anchor - 7d` inside the prior ISO week
    regardless of which weekday the records fall on.

    Kill switch: AGENTICA_METRIC_WEEK_FROM_RECORDS=false|0|no restores the wall-clock
    week for every reducer that calls this. Default is the fixed behaviour. (Shared
    house idiom — remediation._kill_switch_off.)
    """
    if remediation._kill_switch_off("AGENTICA_METRIC_WEEK_FROM_RECORDS"):
        return datetime.now(timezone.utc)
    week = _record_set_week(records)
    if week is None:
        return datetime.now(timezone.utc)
    return datetime.strptime(f"{week}-1", "%G-W%V-%u").replace(tzinfo=timezone.utc)


_SCOUT_CACHE_TTL_SEC = 60.0


def _week_record_count(path: Path, week: str,
                       keep: Callable[[dict], bool] = lambda r: True,
                       *, strict: bool = False) -> int | None:
    """Count JSONL records in ISO `week` that pass `keep`. A missing file counts 0 (it
    holds no records); any other read failure returns None, so callers can tell
    "observed empty" from "could not observe". With `strict`, a non-blank line that is
    not a JSON object also returns None: it may be a record from `week`, so a caller
    that needs "no records" as evidence gets "unknown", not 0."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except FileNotFoundError:
        return 0
    except OSError:
        return None
    count = 0
    for line in text.splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            rec = None
        if not isinstance(rec, dict):
            if strict and line.strip():
                return None
            continue
        if iso_week(rec.get("timestamp")) == week and keep(rec):
            count += 1
    return count


def _within_days(ts: str, days: int) -> bool:
    t = parse_ts(ts)
    if t is None:
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t) <= timedelta(days=days)
