#!/usr/bin/env python
"""SessionEnd hook: emit ONE real canonical telemetry record for the Claude session into the
Agentica OS Data layer, so Order Samurai's aggregator can see actual Claude usage.

Reads real signal from the session transcript (tokens, tool calls, model, turns). NEVER fabricates.
Bulletproof: any failure is swallowed and the hook exits 0 — a telemetry emitter must never break a session.

Locates the Agentica kernel via AGENTICA_GOVERNANCE env var (falls back to the known install path).
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# Slash-command skills expand to <command-name>/skill</command-name> in user turns —
# they are NOT Skill tool_use blocks, so capture them too (e.g. /simplify, /context-optimization).
_CMD = re.compile(r"<command-name>/?([a-z0-9][\w-]*)</command-name>")

# Mac location — the Windows-era Desktop path silently killed telemetry emission
# from 2026-06-21 (the hook swallows all errors by design, so no records landed)
_DEFAULT_GOVERNANCE = Path(__file__).resolve().parent.parent


def _governance_dir() -> Path:
    return Path(os.environ.get("SAMURAI_ROOT") or os.environ.get("ORDER_SAMURAI_ROOT") or os.environ.get("AGENTICA_GOVERNANCE", str(_DEFAULT_GOVERNANCE)))


def _tier_for(model: str) -> str:
    m = (model or "").lower()
    if "opus" in m:
        return "PREMIUM"
    if "sonnet" in m:
        return "STANDARD"
    if "haiku" in m:
        return "FAST"
    return "unknown"


# Approximate prices per million tokens (input, output, cache_read).
# Cache_write is not tracked in the transcript; cache_read at ~10% of input price.
_PRICE_PER_M: dict[str, tuple[float, float, float]] = {
    "opus":   (15.0, 75.0,  1.50),
    "sonnet": ( 3.0, 15.0,  0.30),
    "haiku":  ( 0.8,  4.0,  0.08),
    "fable":  (12.0, 60.0,  1.20),
}
_DEFAULT_PRICE = _PRICE_PER_M["sonnet"]


def _estimate_cost(model: str, tokens_in: int, tokens_out: int, cache_read: int) -> float:
    """Estimate session cost in USD from token counts and known model pricing."""
    m = (model or "").lower()
    for key, prices in _PRICE_PER_M.items():
        if key in m:
            pin, pout, pcache = prices
            break
    else:
        pin, pout, pcache = _DEFAULT_PRICE
    return round(
        tokens_in   / 1_000_000 * pin
        + tokens_out  / 1_000_000 * pout
        + cache_read  / 1_000_000 * pcache,
        6,
    )


_SLOP = ["delve", "leverage", "seamless", "robust", "testament to", "it's worth noting",
         "in conclusion", "underscore", "realm of", "dive into", "tapestry", "boast",
         "furthermore", "moreover", "elevate", "pivotal", "navigate the complexities"]
_FRUST = ["still nothing", "still not", "still broken", "did not", "didn't", "doesn't",
          "not working", "that's wrong", "that is wrong", "not render", "nothing render",
          "nothing is render", "incorrect", "nope", "not what i"]
_REWORK = ["instead", "actually", "try again", "redo", "not quite", "rather", " again"]


def _mcp_or_cli(tool_names: list[str]) -> str:
    """BRUSH-001: classify tool surface from the session's tool_names list."""
    if not tool_names:
        return "none"
    has_mcp = any(n.startswith("mcp__") for n in tool_names)
    has_non = any(not n.startswith("mcp__") for n in tool_names)
    if has_mcp and has_non:
        return "mixed"
    if has_mcp:
        return "mcp"
    return "cli"


_SKILL_TIER_PIPELINE  = re.compile(r"ship|qa|ultrawork")
_SKILL_TIER_GENERATOR = re.compile(r"plan|superpowers|gsd")
_SKILL_TIER_REVIEWER  = re.compile(r"review|audit|security|master")


def _skill_tier(skills_used: list[str]) -> str:
    """BRUSH-003: classify the highest-tier skill invoked in the session."""
    if not skills_used:
        return "none"
    for sk in skills_used:
        if _SKILL_TIER_PIPELINE.search(sk):
            return "pipeline"
    for sk in skills_used:
        if _SKILL_TIER_GENERATOR.search(sk):
            return "generator"
    for sk in skills_used:
        if _SKILL_TIER_REVIEWER.search(sk):
            return "reviewer"
    return "tool-wrapper"


def _empty() -> dict:
    return {"tokens_prompt": 0, "tokens_completion": 0, "cache_read_tokens": 0, "tool_calls": 0,
            "turns": 0, "model": "", "tool_names": [], "skills_used": [], "slop_markers": 0,
            "output_words": 0, "frustration_signals": 0, "rework_turns": 0, "subagent_spawns": 0,
            "tool_failure_count": 0, "chain_depth": 0}


def _parse_transcript(path: Path) -> dict:
    """Extract real signal from the session transcript. Returns zeros if unreadable."""
    tp = tc = cache = tools = turns = slop = words = frust = rework = spawns = 0
    tool_fails = chain_depth = 0  # BOW-002, BRUSH-002
    model = ""
    tool_names: set[str] = set()
    skills: set[str] = set()
    timestamps: list[str] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            # Top-level timestamp on every transcript entry
            if ts := obj.get("timestamp"):
                timestamps.append(str(ts))
            role = obj.get("type") or obj.get("role")
            msg = obj.get("message") or {}
            usage = msg.get("usage") or {}
            if usage:
                tp += int(usage.get("input_tokens") or 0)
                tc += int(usage.get("output_tokens") or 0)
                cache += int(usage.get("cache_read_input_tokens") or 0)
            if msg.get("model"):
                model = msg["model"]
            if role == "assistant":
                turns += 1
            content = msg.get("content")
            if role == "user":
                utext = content if isinstance(content, str) else (
                    " ".join(b.get("text", "") for b in content
                             if isinstance(b, dict) and b.get("type") == "text")
                    if isinstance(content, list) else "")
                low = utext.lower()
                frust += any(p in low for p in _FRUST)
                rework += any(p in low for p in _REWORK)
                for m in _CMD.finditer(low):
                    skills.add(m.group(1))
            if isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_use":
                        tools += 1
                        nm = b.get("name")
                        if nm:
                            tool_names.add(nm)
                        if nm == "Agent":
                            spawns += 1
                        if nm in ("Agent", "Task"):  # BRUSH-002
                            chain_depth += 1
                        if nm == "Skill":
                            sk = (b.get("input") or {}).get("skill")
                            if sk:
                                skills.add(sk)
                    elif b.get("type") == "tool_result":  # BOW-002
                        if b.get("is_error"):
                            tool_fails += 1
                        else:
                            result_content = b.get("content") or ""
                            if isinstance(result_content, list):
                                result_content = " ".join(
                                    c.get("text", "") for c in result_content
                                    if isinstance(c, dict)
                                )
                            if "Error" in str(result_content):
                                tool_fails += 1
                    elif b.get("type") == "text" and role == "assistant":
                        txt = b.get("text", "") or ""
                        words += len(txt.split())
                        low = txt.lower()
                        slop += sum(low.count(s) for s in _SLOP)
    except OSError:
        pass
    return {"tokens_prompt": tp, "tokens_completion": tc, "cache_read_tokens": cache,
            "tool_calls": tools, "turns": turns, "model": model,
            "tool_names": sorted(tool_names), "skills_used": sorted(skills),
            "slop_markers": slop, "output_words": words,
            "frustration_signals": int(frust), "rework_turns": int(rework),
            "subagent_spawns": spawns,
            "tool_failure_count": tool_fails,   # BOW-002
            "chain_depth": chain_depth,          # BRUSH-002
            "session_start": min(timestamps) if timestamps else None,
            "session_end": max(timestamps) if timestamps else None}


def _count_violations_in_window(log: Path, session_start: str | None, session_end: str | None) -> int:
    """Count principle_violations.jsonl entries whose ts falls within the session window.
    Conservative: if window unknown, returns 0 rather than over-counting."""
    if not log.exists() or not session_start or not session_end:
        return 0
    count = 0
    try:
        for line in log.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            ts = str(entry.get("ts") or "")
            if ts and session_start <= ts <= session_end:
                count += 1
    except OSError:
        pass
    return count


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0  # no/invalid hook payload — nothing to do
    try:
        sys.path.insert(0, str(_governance_dir()))
        from agentica_core.emit import emit  # noqa: E402

        cwd = payload.get("cwd") or ""
        project = Path(cwd).name or "unknown"
        session_id = payload.get("session_id") or "local-session"
        t = _empty()
        tpath = payload.get("transcript_path")
        if tpath:
            p = Path(tpath)
            if p.exists():
                t = _parse_transcript(p)

        # Count CLAUDE.md principle violations that fired during this session window
        violations_log = Path.home() / ".claude" / "data" / "principle_violations.jsonl"
        rule_violations = _count_violations_in_window(
            violations_log, t.get("session_start"), t.get("session_end")
        )

        tool_names_list = t["tool_names"] or []
        emit(
            "claude", task_name="session",
            project=project, session_id=session_id,
            model=t["model"] or None, model_tier=_tier_for(t["model"]),
            tokens_prompt=t["tokens_prompt"], tokens_completion=t["tokens_completion"],
            cache_read_tokens=t["cache_read_tokens"] or None,
            tool_calls=t["tool_calls"], tool_calls_list=tool_names_list or None,
            tool_failure_count=t["tool_failure_count"] or None,          # BOW-002
            mcp_or_cli=_mcp_or_cli(tool_names_list),                     # BRUSH-001
            skills_used=t["skills_used"] or None,
            chain_depth=t["chain_depth"] or None,                        # BRUSH-002 (Task+Agent count)
            skill_tier=_skill_tier(t["skills_used"] or []),              # BRUSH-003
            slop_markers=t["slop_markers"], output_words=t["output_words"],
            frustration_signals=t["frustration_signals"], rework_turns=t["rework_turns"],
            subagent_spawns=t["subagent_spawns"],
            rule_violations=rule_violations or None,
            total_cost=_estimate_cost(
                t["model"] or "",
                t["tokens_prompt"] or 0,
                t["tokens_completion"] or 0,
                t["cache_read_tokens"] or 0,
            ),
            status="success",
        )
        # SessionEnd recompute cadence: rebuild the dashboard payload now that this session's
        # record is in, snapshot history, and refresh the dashboard's served copy. Best-effort.
        try:
            import shutil
            from datetime import datetime, timezone
            from agentica_core import aggregate as _agg
            payload = _agg.aggregate(timestamp=datetime.now(timezone.utc).isoformat(), write_history=True)
            _agg.write_payload(payload)
            pub = _governance_dir() / "dashboard-ui" / "public" / "wid_payload.json"
            if pub.parent.exists():
                shutil.copyfile(_agg.default_payload_path(), pub)
        except Exception:
            pass
    except Exception:
        return 0  # never break a session over telemetry
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
