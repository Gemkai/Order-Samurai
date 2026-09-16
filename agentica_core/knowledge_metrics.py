"""Read bounded knowledge diagnostics and native provider usage for dashboard windows.

These observations establish reuse and latency, not accepted-task quality or savings.
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from . import usage_records

PLATFORMS = ("claude", "codex")
RETRIEVAL_KEYS = (
    "Embedding_Cache_Hits", "Embedding_Cache_Misses", "Embedding_Cache_Errors",
    "Embedding_Cache_Hit_Rate", "Retrieval_Observations", "Retrieval_Latency_P50_ms",
    "Retrieval_Latency_P95_ms", "Retrieval_Search_Failures", "Injected_Context_Chars_P50",
)
_SEARCH_STATES = {"ok", "empty", "unavailable", "invalid_response"}
_LOG_BYTES = 2 * 1024 * 1024
_NATIVE_LIMITS = "Native retained sample: at most 60 recent complete transcripts per platform, 32 MiB per file, 128 MiB total; cached up to 5 minutes. Older or oversized files may be absent."


def collect(repo: Path, home: Path, start: datetime | None, end: datetime,
            platforms: list[str] | None = None) -> dict:
    """Return separate platform and token-weighted combined observations in [start,end)."""
    selected = [p for p in dict.fromkeys(PLATFORMS if platforms is None else platforms)
                if p in PLATFORMS]
    period = f"Window [{start.isoformat() if start else 'retained history'}, {end.isoformat()})."
    by_platform, all_rows, all_usage = {}, [], []
    source_count = truncated_count = 0
    for platform in selected:
        if platform == "codex":
            paths = [repo / "Governance/data/knowledge_bridge/events.jsonl"]
        else:
            path = home / ".claude/data/qdrant_hit_rate.jsonl"
            paths = [path, path.with_suffix(".jsonl.1")]
        rows, available, truncated = [], 0, 0
        for path in paths:
            raw, readable, cut = _read_log(path)
            available += readable
            truncated += cut
            for row in raw:
                stamp = _stamp(row.get("at"), epoch=True) if platform == "codex" else _stamp(row.get("ts"), local=True)
                if not _in_window(stamp, start, end):
                    continue
                if platform == "codex" and row.get("event") != "UserPromptSubmit":
                    continue
                if row.get("status") in ("out_of_scope", "disabled_for_experiment", "disabled", "ignored", "short_prompt"):
                    continue
                if _embedding(row) in {"hit", "miss", "error"} or _searches(row):
                    rows.append(row)
        usage = []
        for record in _native_records(home, platform):
            for event in record.get("_usage_events", []):
                if ("tokens_prompt" in event or "cache_read_tokens" in event) and _in_window(
                        _stamp(event.get("timestamp")), start, end):
                    usage.append(dict(event, platform=platform))
        metrics = _retrieval(rows, period, available, truncated)
        metrics["Cache_Hit_Rate"] = provider_rate(usage, detail=f"{period} {_NATIVE_LIMITS}")
        by_platform[platform] = metrics
        all_rows.extend(rows)
        all_usage.extend(usage)
        source_count += available
        truncated_count += truncated
    combined = _retrieval(all_rows, period, source_count, truncated_count)
    combined["Cache_Hit_Rate"] = provider_rate(all_usage, detail=f"{period} {_NATIVE_LIMITS}")
    return {"combined": combined, "by_platform": by_platform}


def provider_rate(records: list[dict], *, dedup: bool = False, detail: str = "") -> dict:
    """Pure cached-input share over explicit valid buckets; input already includes cache."""
    valid, sessions = [], {}
    for record in records:
        prompt, cached = record.get("tokens_prompt"), record.get("cache_read_tokens")
        if (record.get("platform") not in PLATFORMS or record.get("usage_known") is False
                or not _number(prompt) or not _number(cached) or cached > prompt):
            continue
        creation = record.get("cache_creation_tokens", 0)
        if not _number(creation) or cached + creation > prompt:
            continue
        sid = record.get("session_id")
        if dedup and isinstance(sid, str) and sid:
            key = (record["platform"], sid)
            previous = sessions.get(key)
            if previous is None or (prompt, cached) > (previous["tokens_prompt"], previous["cache_read_tokens"]):
                sessions[key] = record
        else:
            valid.append(record)
    valid.extend(sessions.values())
    total = sum(r["tokens_prompt"] for r in valid)
    cached = sum(r["cache_read_tokens"] for r in valid)
    if not _number(total) or not _number(cached):
        return _envelope(None, f"{detail} Token totals exceed the supported numeric range.")
    note = f"{len(valid)}/{len(records)} valid usage observations; {cached:g}/{total:g} cached/total input tokens. Unknown buckets excluded; reuse does not establish quality or savings."
    return _envelope(round(cached / total * 100, 2) if total else None, f"{detail} {note}".strip())


def _retrieval(rows, period, available, truncated):
    base = (f"{period} Retained hook sample: {len(rows)} instrumented retrieval observations, "
            f"{available} readable log files; at most 2 MiB per log, {truncated} truncated tails. "
            "Legacy or missing fields excluded; this is not complete historical coverage.")
    embedding = [_embedding(r) for r in rows if _embedding(r) in {"hit", "miss", "error"}]
    hits, misses, errors = (embedding.count(state) for state in ("hit", "miss", "error"))
    elapsed = [r["elapsed_ms"] for r in rows if _number(r.get("elapsed_ms"))]
    context = [r["context_chars"] for r in rows if _number(r.get("context_chars"))]
    searches = [state for r in rows for state in _searches(r)]
    values = {
        "Embedding_Cache_Hits": (hits if embedding else None, len(embedding)),
        "Embedding_Cache_Misses": (misses if embedding else None, len(embedding)),
        "Embedding_Cache_Errors": (errors if embedding else None, len(embedding)),
        "Embedding_Cache_Hit_Rate": (round(100 * hits / (hits + misses), 2) if hits + misses else None, len(embedding)),
        "Retrieval_Observations": (len(rows) if rows else None, len(rows)),
        "Retrieval_Latency_P50_ms": (_percentile(elapsed, .5), len(elapsed)),
        "Retrieval_Latency_P95_ms": (_percentile(elapsed, .95), len(elapsed)),
        "Injected_Context_Chars_P50": (_percentile(context, .5), len(context)),
    }
    result = {key: _envelope(value, f"{base} {count}/{len(rows)} valid field samples.")
              for key, (value, count) in values.items()}
    result["Embedding_Cache_Hit_Rate"]["detail"] += f" {hits} hits / {hits + misses} lookups; {errors} errors excluded from the rate."
    result["Retrieval_Search_Failures"] = _envelope(
        sum(s in {"unavailable", "invalid_response"} for s in searches) if searches else None,
        f"{base} {len(searches)} collection attempts with known status; ok and empty are successful searches.")
    return result


def _envelope(value, detail):
    result = {"val": value, "detail": detail, "calibrated": True}
    if value is None:
        result["data_gap"] = True
    return result


def _number(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _embedding(row):
    value = row.get("embedding")
    result = value.get("result") if isinstance(value, dict) else None
    return result if isinstance(result, str) else None


def _searches(row):
    value = row.get("search_diagnostics")
    if not isinstance(value, dict):
        return []
    return [v["status"] for v in value.values() if isinstance(v, dict)
            and isinstance(v.get("status"), str) and v["status"] in _SEARCH_STATES]


def _stamp(value, *, epoch=False, local=False):
    try:
        if epoch:
            return datetime.fromtimestamp(value, timezone.utc) if _number(value) else None
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.astimezone() if local else stamp.replace(tzinfo=timezone.utc)
        return stamp
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _in_window(stamp, start, end):
    return stamp is not None and (start is None or stamp >= start) and stamp < end


def _percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * fraction
    low = int(index)
    return round(values[low] + (values[min(low + 1, len(values) - 1)] - values[low]) * (index - low), 2)


def _read_log(path):
    try:
        with path.open("rb") as stream:
            size = stream.seek(0, 2)
            stream.seek(max(0, size - _LOG_BYTES))
            block = stream.read(_LOG_BYTES)
        cut = size > _LOG_BYTES
        lines = block.splitlines()[1:] if cut else block.splitlines()
        rows = []
        for line in lines:
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
            except (ValueError, UnicodeError, RecursionError):
                continue
        return rows, True, cut
    except OSError:
        return [], False, False


class _NativeSample:
    """Supply complete, bounded files to the existing parser's source interface."""
    def __init__(self, paths):
        self.paths = paths

    def rglob(self, pattern):
        return iter(self.paths)


def _native_records(home, platform):
    try:
        return _load_native(str(home), platform, int(time.monotonic() // 300))
    except (OSError, ValueError, TypeError, RecursionError, OverflowError):
        return []


@lru_cache(maxsize=4)
def _load_native(home, platform, generation):
    root = Path(home) / f".{platform}"
    source = root / ("sessions" if platform == "codex" else "projects")
    files = []
    for path in source.rglob("*.jsonl"):
        try:
            stat = path.stat()
            files.append((stat.st_mtime_ns, str(path), stat.st_size, path))
        except OSError:
            continue
    selected, total = [], 0
    for _, _, size, path in sorted(files, reverse=True)[:60]:
        if size <= 32 * 1024 * 1024 and total + size <= 128 * 1024 * 1024:
            selected.append(path)
            total += size
    return usage_records.enrich(platform, [], root, source=_NativeSample(selected))
