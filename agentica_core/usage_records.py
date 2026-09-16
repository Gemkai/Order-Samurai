"""Request-level usage, with event-time windows and explicit token accounting.

Input includes cache reads and cache creation on both platforms. Output includes
reasoning where the provider includes it; neither subset is added a second time.
No transcript establishes the user's billed dollars.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from .model_tiers import model_tier

VERSION = "request-usage-v6"
NATIVE_PLATFORMS = frozenset({"claude", "codex"})  # platforms whose usage is read from native transcripts
FIELDS = ("tokens_prompt", "tokens_completion", "cache_read_tokens", "cache_creation_tokens",
          "tool_calls", "output_words", "turns", "subagent_spawns", "tool_failure_count")


def ts(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except ValueError:
        return None


def number(value):
    return max(0, value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else 0


def _valid_count(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def cost(event, rate_card):
    """Price only known, disjoint usage at an explicit dated rate card's $/MTok rates."""
    result = {"value": None, "basis": "api_equivalent", "reason": None}
    def unknown(reason):
        return {**result, "reason": reason}
    if not isinstance(rate_card, dict) or not rate_card.get("as_of"):
        return unknown("rate_card_unavailable")
    models = rate_card.get("models")
    rates = models.get(event.get("model")) if isinstance(models, dict) and isinstance(event.get("model"), str) else None
    if not isinstance(rates, dict):
        return unknown("model_rate_unknown")
    if event.get("usage_known") is not True:
        return unknown("usage_unknown")
    fields = ("tokens_prompt", "tokens_completion", "cache_read_tokens", "cache_creation_tokens")
    if not all(_valid_count(event.get(key)) for key in fields):
        return unknown("invalid_usage")
    total, output, read, write = (event[key] for key in fields)
    ordinary = total - read - write
    if ordinary < 0:
        return unknown("inconsistent_cache_buckets")
    buckets = {"input": ordinary, "cache_read": read, "output": output}
    if write:
        short, long = event.get("cache_creation_5m_tokens"), event.get("cache_creation_1h_tokens")
        if short is None or long is None:
            return unknown("cache_write_ttl_unknown")
        if not _valid_count(short) or not _valid_count(long) or short + long != write:
            return unknown("inconsistent_cache_write_split")
        buckets.update(cache_write_5m=short, cache_write_1h=long)
    if any(not _valid_count(rates.get(key)) for key, count in buckets.items() if count):
        return unknown("invalid_or_missing_rate")
    return {**result, "value": sum(count * rates[key] for key, count in buckets.items() if count) / 1_000_000,
            "rate_card_as_of": rate_card["as_of"]}


def _lines(path):
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    yield obj
            except ValueError:
                continue


@lru_cache(maxsize=6000)
def _parse(path_string, size, mtime_ns, platform):
    path = Path(path_string)
    return _codex(path) if platform == "codex" else _claude(path)


def _event(stamp, model, key, **values):
    return {"timestamp": stamp, "model": model, "id": key, **values}


def _tool_metadata(name, arguments):
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            arguments = {}
    arguments = arguments if isinstance(arguments, dict) else {}
    name = str(name or "")
    leaf = name.split(".")[-1]
    result = {"tool_name": name}
    if leaf in {"Agent", "Task", "spawn_agent"}:
        role = arguments.get("subagent_type") or arguments.get("agent_type")
        result["agent_type"] = role.strip() if isinstance(role, str) and role.strip() else "Unspecified role"
        result["subagent_spawns"] = 1
    if leaf == "Skill" and isinstance(arguments.get("skill"), str):
        result["skill_name"] = arguments["skill"]
    return result


def _codex(path):
    sid = path.stem
    cwd = ""
    model = None
    events = {}
    previous = {}
    previous_known = True
    turn = ""
    complete = False
    started = None
    for i, row in enumerate(_lines(path)):
        pl = row.get("payload") or {}
        kind = row.get("type")
        stamp = row.get("timestamp")
        if kind == "session_meta":
            sid = pl.get("id") or sid
            cwd = pl.get("cwd") or cwd
            started = ts(pl.get("timestamp") or stamp)
        if kind == "turn_context":
            model = pl.get("model") or model
            turn = pl.get("turn_id") or turn
        current = ts(stamp)
        inherited = started and current and current < started
        event_type = pl.get("type")
        if event_type == "token_count":
            info = pl.get("info") or {}
            totals = info.get("total_token_usage") or {}
            if not totals:
                continue
            fields = ("input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens")
            now = {k: number(totals.get(k)) for k in fields}
            # Rate-limit notifications repeat the last usage block. Only cumulative
            # growth is new work. A counter reset starts another accumulation period.
            reset = any(now[k] < previous.get(k, 0) for k in ("input_tokens", "output_tokens"))
            delta = {k: max(0, now[k] - (0 if reset else previous.get(k, 0))) for k in fields}
            known = all(_valid_count(totals.get(k)) for k in fields)
            delta_known = known and (reset or not previous or previous_known)
            previous, previous_known = now, known
            if inherited or not any(delta.values()):
                continue
            if delta['cached_input_tokens'] > delta['input_tokens']:
                delta['cached_input_tokens'] = delta['input_tokens']
            key = f"{turn}:{stamp}:usage"
            events[key] = _event(stamp, model, key,
                context_max=number((info.get('last_token_usage') or {}).get('input_tokens')),
                tokens_prompt=delta['input_tokens'], tokens_completion=delta['output_tokens'],
                cache_read_tokens=delta['cached_input_tokens'],
                cache_creation_tokens=delta['cache_write_input_tokens'],
                usage_known=delta_known
                    and now['cached_input_tokens'] + now['cache_write_input_tokens'] <= now['input_tokens'])
        elif not inherited:
            key = pl.get('call_id') or pl.get('id') or f'{turn}:{stamp}:{i}'
            if kind == 'response_item' and event_type in {'function_call', 'custom_tool_call'}:
                events[f'tool:{key}'] = _event(stamp, model, f'tool:{key}', tool_calls=1,
                    **_tool_metadata(pl.get('name'), pl.get('arguments')))
            elif kind == 'event_msg' and event_type == 'agent_message':
                events[f'output:{key}'] = _event(stamp, model, f'output:{key}',
                    output_words=len(str(pl.get('message') or '').split()))
            elif kind == 'event_msg' and event_type == 'user_message':
                events[f'user:{key}'] = _event(stamp, model, f'user:{key}', turns=1)
            elif event_type == 'task_complete':
                complete = True
    return sid, cwd, complete, list(events.values())


def _claude(path):
    sid = path.stem
    cwd = ''
    events = {}
    for i, row in enumerate(_lines(path)):
        cwd = row.get('cwd') or cwd
        msg = row.get('message') or {}
        if not isinstance(msg, dict):
            continue
        model = msg.get('model')
        stamp = row.get('timestamp')
        role = row.get('type')
        if role == 'user':
            content = msg.get('content')
            text = content if isinstance(content, str) else ''
            if text.strip() and not text.lstrip().startswith('<system-reminder>'):
                key = f"user:{row.get('uuid') or str(sid)+':'+str(i)}"
                events[key] = _event(stamp, None, key, turns=1)
        if role == 'assistant':
            if model == '<synthetic>':
                continue
            request = msg.get('id') or row.get('requestId') or row.get('uuid') or f'{sid}:{i}'
            usage = msg.get('usage')
            if isinstance(usage, dict):
                key = f'usage:{request}'
                values = dict(tokens_prompt=number(usage.get('input_tokens')) + number(usage.get('cache_read_input_tokens')) + number(usage.get('cache_creation_input_tokens')),
                    tokens_completion=number(usage.get('output_tokens')),
                    cache_read_tokens=number(usage.get('cache_read_input_tokens')),
                    cache_creation_tokens=number(usage.get('cache_creation_input_tokens')))
                values["context_max"] = values["tokens_prompt"]
                old = events.get(key, {})
                # Streaming fragments repeat one request's usage; take its maxima once.
                events[key] = _event(old.get('timestamp') or stamp, model, key,
                    **{k: max(v, old.get(k, 0)) for k, v in values.items()})
                required = ('input_tokens', 'output_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')
                events[key]['usage_known'] = all(_valid_count(usage.get(k)) for k in required)
                split = usage.get('cache_creation') or {}
                for source, target in (('ephemeral_5m_input_tokens', 'cache_creation_5m_tokens'),
                                       ('ephemeral_1h_input_tokens', 'cache_creation_1h_tokens')):
                    value = split.get(source) if isinstance(split, dict) else None
                    if _valid_count(value):
                        events[key][target] = max(value, old.get(target, 0))
                    elif target in old:
                        events[key][target] = old[target]
            content = msg.get('content') or []
            if not isinstance(content, list):
                continue
            for n, block in enumerate(content):
                if not isinstance(block, dict):
                    continue
                if block.get('type') == 'tool_use':
                    key = f"tool:{block.get('id') or (str(request)+':'+str(n))}"
                    events[key] = _event(stamp, model, key, tool_calls=1,
                        **_tool_metadata(block.get('name'), block.get('input')))
                elif block.get('type') == 'text':
                    # UUID identifies each emitted text fragment across transcript copies.
                    key = f"text:{row.get('uuid') or request}:{n}"
                    events[key] = _event(stamp, model, key,
                        output_words=len(str(block.get('text') or '').split()))
    return sid, cwd, True, list(events.values())


def enrich(platform, raw, runtime_root, *, source=None):
    if platform not in NATIVE_PLATFORMS:
        return raw
    source = source or runtime_root / ('sessions' if platform == 'codex' else 'projects')
    latest = {}
    for r in raw:
        sid = r.get('session_id')
        if sid and (sid not in latest or str(r.get('timestamp', '')) > str(latest[sid].get('timestamp', ''))):
            latest[sid] = r
    records = []
    sessions = {}
    seen = {}
    found = set()
    # Shorter paths first: an original transcript owns shared request IDs before copies.
    paths = sorted(source.rglob('*.jsonl'), key=lambda p: (len(p.parts), str(p)))
    for path in paths:
        try:
            stat = path.stat()
            sid, cwd, complete, events = _parse(str(path), stat.st_size, stat.st_mtime_ns, platform)
        except (OSError, ValueError, TypeError):
            continue
        found.add(sid)
        unique = []
        for e in events:
            key = (platform, e['id'])
            if key in seen:
                if e['id'].startswith('usage:'):
                    for f in (*FIELDS, "context_max", "cache_creation_5m_tokens", "cache_creation_1h_tokens"):
                        if f in e:
                            seen[key][f] = max(number(seen[key].get(f)), number(e[f]))
                if 'usage_known' in e:
                    seen[key]['usage_known'] = seen[key].get('usage_known') is True and e['usage_known'] is True
                continue
            if ts(e.get('timestamp')) and any(number(e.get(f)) for f in FIELDS):
                event = dict(e)
                seen[key] = event
                unique.append(event)
        if not unique:
            continue
        base = dict(latest.get(sid, {}))
        # Session-level quality counters cannot be apportioned to a shorter event window.
        for field in (*FIELDS, 'slop_markers', 'frustration_signals', 'rework_turns', 'total_cost'):
            base.pop(field, None)
        base.update(platform=platform, session_id=sid, project=base.get('project') or Path(cwd).name or 'unknown',
                    status='success' if complete else 'incomplete', usage_source=VERSION,
                    cost_basis='unavailable', _usage_events=unique)
        if sid in sessions:
            sessions[sid]['_usage_events'].extend(unique)
            if complete:
                sessions[sid]['status'] = 'success'
        else:
            sessions[sid] = base
            records.append(base)
    missing = [r for sid, r in latest.items() if sid not in found and any(number(r.get(f)) for f in FIELDS)]
    # Unrecoverable snapshots remain visible as coverage gaps, never precise day totals.
    for r in missing:
        records.append(dict(r, usage_source=VERSION, _usage_events=[], usage_missing=True))
    summaries = []
    for record in records:
        if record.get('usage_missing'):
            summaries.append(record)
            continue
        for summary in window([record]):
            summary['_usage_events'] = record['_usage_events']
            summaries.append(summary)
    return summaries


def window(records, start=None, end=None):
    end = end or datetime.now(timezone.utc)
    output = []
    for raw in records:
        if '_usage_events' not in raw:
            stamp = ts(raw.get('timestamp'))
            if stamp and (start is None or stamp >= start) and stamp <= end:
                output.append(raw)
            continue
        events = [e for e in raw['_usage_events'] if (stamp := ts(e['timestamp']))
                  and (start is None or stamp >= start) and stamp <= end]
        if not events:
            continue
        r = {k: v for k, v in raw.items() if k != '_usage_events'}
        models = defaultdict(lambda: defaultdict(int))
        for e in events:
            for f in FIELDS:
                if f in e:
                    models[e.get('model')][f] += number(e[f])
        for f in FIELDS:
            if any(f in values for values in models.values()):
                r[f] = sum(values.get(f, 0) for values in models.values())
        r['_activity'] = [e for e in events if e.get('tool_name')]
        r['context_max'] = max((number(e.get('context_max')) for e in events), default=0)
        for model, values in models.items():
            values['context_max'] = max((number(e.get('context_max')) for e in events if e.get('model') == model), default=0)
        r['timestamp'] = max(e['timestamp'] for e in events)
        r['_model_usage'] = [dict(values, model=m, model_tier=model_tier(m)) for m, values in models.items()
                             if any(values.get(f, 0) for f in ('tokens_prompt', 'tokens_completion', 'tool_calls', 'output_words'))]
        model_names = [m for m in models if m]
        r['model'] = model_names[0] if len(model_names) == 1 else None
        tiers = {m['model_tier'] for m in r['_model_usage']}
        if not tiers:
            continue
        r['model_tier'] = next(iter(tiers)) if len(tiers) == 1 else 'MIXED'
        output.append(r)
    return output


def for_tier(records, tier):
    result = []
    for raw in records:
        if '_model_usage' not in raw:
            if raw.get('model_tier') == tier:
                result.append(raw)
            continue
        parts = [p for p in raw['_model_usage'] if p['model_tier'] == tier]
        if not parts:
            continue
        record = {k: v for k, v in raw.items() if k not in FIELDS}
        record.update(model_tier=tier, _model_usage=parts,
                      context_max=max((number(p.get("context_max")) for p in parts), default=0))
        for field in FIELDS:
            if any(field in p for p in parts):
                record[field] = sum(number(p.get(field)) for p in parts)
        result.append(record)
    return result
