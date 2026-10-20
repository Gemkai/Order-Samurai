#!/usr/bin/env python3
"""Read local coding-agent session logs and report measured usage.

Dollar estimates are available only for the claude harness. Codex logs record
cumulative token counts, not prices. This scan is read-only and needs no daemon.

Usage:
    python3 bin/first_blood.py                    # scan local session roots
    python3 bin/first_blood.py --logs-dir DIR      # override claude transcript root
    python3 bin/first_blood.py --codex-dir DIR     # override Codex sessions root
    python3 bin/first_blood.py --json              # machine-readable report
"""
from __future__ import annotations

import argparse
import json
import re
import runpy
import sys
from datetime import datetime, timezone
from pathlib import Path

_THIS = Path(__file__).resolve()
_ROOT = _THIS.parents[1]        # Order Samurai
_GOV = _ROOT.parent             # Governance
# agentica_core is a CHILD of the root in a web install (~/.samurai/core) and a SIBLING
# of it in the live Governance tree. The child goes first so the bundled copy wins.
for _dir in (_GOV, _ROOT):
    if str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))

from agentica_core.emit import build_record  # noqa: E402  — canonical record shape, reused not reimplemented
from agentica_core.aggregate import (  # noqa: E402  — SAME reducers the live dashboard grades Brush with
    r_cost_per_task,
    r_token_density,
    r_token_spend,
    r_total_cost,
)

# Load the bundled CLI as a library to share detection, labels and manifest handling.
# Its main guard prevents commands from running during import.
_HARNESS = runpy.run_path(str(_THIS.with_name("samurai")))
HARNESS_LABELS = _HARNESS["HARNESS_LABELS"]

# Model-family keyword -> (tier label, estimated USD per-million-token pricing). One
# shared table for both tier classification and pricing (CLAUDE.md Principle #7: DRY
# at the knowledge level) — two separate keyword lists previously let "mythos" get
# tagged PREMIUM by _tier_for() while _price_for() had no matching entry and silently
# fell through to the STANDARD/sonnet rate, undercutting the spend-spike signal for
# exactly the tier this tool most needs to catch. Always labelled ESTIMATED in the
# report — never a substitute for your actual invoice. Mirrors the estimation approach
# the SessionEnd emitter (scripts/agentica_emit.py) already uses, so a user who later
# wires the live hook sees consistent numbers, not a second cost formula.
_MODEL_FAMILIES: dict[str, tuple[str, tuple[float, float, float]]] = {
    "opus": ("PREMIUM", (15.0, 75.0, 1.50)),
    "fable": ("PREMIUM", (12.0, 60.0, 1.20)),
    "mythos": ("PREMIUM", (15.0, 75.0, 1.50)),  # no public list price yet — opus-tier estimate
    "sonnet": ("STANDARD", (3.0, 15.0, 0.30)),
    "haiku": ("FAST", (0.8, 4.0, 0.08)),
}
_DEFAULT_PRICE = _MODEL_FAMILIES["sonnet"][1]

# Sessions at/above this estimated cost are flagged as a spend-spike signal — the literal
# $6,000-overnight failure mode Order Samurai exists to catch. This is one honest, measured
# threshold check, not the full kill-chain taxonomy (out of scope for this cutline — see
# wargames/03-order-samurai-commercialization.md Move 1 failure/counter).
_SPIKE_THRESHOLD_USD = 5.0


def _tier_for(model: str) -> str:
    m = (model or "").lower()
    for key, (tier, _price) in _MODEL_FAMILIES.items():
        if key in m:
            return tier
    return "unknown"


def _price_for(model: str) -> tuple[float, float, float] | None:
    m = (model or "").lower()
    # Preserve legacy unknown Claude transcript pricing, but never assign it to
    # an explicitly different provider/model family. Codex never calls this path.
    if re.match(r"^(?:openai/|google/|gpt-|codex(?:-|$)|o\d(?:-|$)|chatgpt-|gemini|gemma|llama|"
                r"meta-llama/|mistral|mixtral|qwen|deepseek|grok|x-ai/|command-r)", m):
        return None
    for key, (_tier, price) in _MODEL_FAMILIES.items():
        if key in m:
            return price
    return _DEFAULT_PRICE


def estimate_cost(model: str, tokens_in: int, tokens_out: int, cache_read: int) -> float | None:
    prices = _price_for(model)
    if prices is None:
        return None
    p_in, p_out, p_cache = prices
    return round(
        tokens_in / 1_000_000 * p_in
        + tokens_out / 1_000_000 * p_out
        + cache_read / 1_000_000 * p_cache,
        4,
    )


def _project_from_dir(dirname: str) -> str:
    """Recover the real project name from a Claude Code transcript directory name.

    Claude Code encodes a session's cwd as "-Users-<username>-<...path...>"
    (every "/" replaced by "-"). Taking only the LAST "-"-separated fragment
    collapses any repo whose own name contains a hyphen (AgenticaOS-bot,
    Order-Samurai, ...) to just its final word. Strip the known
    "-Users-<username>-" prefix (3 leading segments) and keep the rest intact
    instead of re-splitting it."""
    parts = dirname.split("-")
    if len(parts) > 3 and parts[1] == "Users":
        return "-".join(parts[3:]) or "unknown"
    return dirname.rsplit("-", 1)[-1] or "unknown"


def parse_transcript(path: Path) -> dict | None:
    """Read one Claude Code session transcript (JSONL) and return a canonical-record-shaped
    dict, or None if the file has no assistant turns (a stub, not a session). A malformed
    line is skipped, never the whole session — a bad line must not lose a real one."""
    tokens_in = tokens_out = cache_read = 0
    model = ""
    last_ts: str | None = None
    turns = tool_calls = 0
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict) or rec.get("type") != "assistant":
                continue
            turns += 1
            msg = rec.get("message") or {}
            content = msg.get("content") or []
            if isinstance(content, list):
                tool_calls += sum(isinstance(item, dict) and item.get("type") == "tool_use"
                                  for item in content)
            usage = msg.get("usage") or {}
            # cache_creation_input_tokens (prompt-cache writes) is deliberately excluded here,
            # matching the SessionEnd emitter (scripts/agentica_emit.py) this tool mirrors --
            # folding it into tokens_in double-counted cache writes at the full input rate.
            tokens_in += int(usage.get("input_tokens") or 0)
            tokens_out += int(usage.get("output_tokens") or 0)
            cache_read += int(usage.get("cache_read_input_tokens") or 0)
            model = msg.get("model") or model
            ts = rec.get("timestamp")
            if ts:
                last_ts = ts
    if turns == 0:
        return None
    cost = estimate_cost(model, tokens_in, tokens_out, cache_read)
    project = _project_from_dir(path.parent.name)
    record = build_record(
        "claude",
        task_name="session",
        timestamp=last_ts or datetime.now(timezone.utc).isoformat(),
        project=project,
        session_id=path.stem,
        model=model or None,
        model_tier=_tier_for(model),
        tokens_prompt=tokens_in,
        tokens_completion=tokens_out,
        cache_read_tokens=cache_read or None,
        total_cost=cost or 0.0,
        tool_calls=tool_calls,
    )
    record["total_cost"] = cost
    return record


def scan_logs(logs_dir: Path) -> list[dict]:
    """Read-only scan of every transcript under logs_dir/*/*.jsonl. Never raises on a
    missing or unreadable directory — an absent log dir is an honest zero-session report."""
    records: list[dict] = []
    if not logs_dir.exists():
        return records
    for path in sorted(logs_dir.glob("*/*.jsonl")):
        try:
            rec = parse_transcript(path)
        except OSError:
            continue
        if rec is not None:
            records.append(rec)
    return records


def parse_codex_transcript(path: Path) -> dict | None:
    """Read the last cumulative usage snapshot; absent usage stays unknown."""
    session_id, project, model = path.stem, "unknown", None
    usage = None
    tool_calls = 0
    seen = meta_seen = False
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict) or not isinstance(event.get("payload"), dict):
                continue
            payload = event["payload"]
            kind = event.get("type")
            if not isinstance(kind, str):
                continue
            if kind == "session_meta":
                seen = True
                # A forked or subagent rollout repeats its parent's session_meta and carries
                # the parent's session_id; its own thread id is `id` of the FIRST record.
                if meta_seen:
                    continue
                meta_seen = True
                for key in ("id", "session_id"):
                    candidate = payload.get(key)
                    if isinstance(candidate, str) and candidate:
                        session_id = candidate
                        break
            elif kind == "turn_context":
                seen = True
                model = payload.get("model") or model
            elif kind == "response_item" and payload.get("type") in ("function_call", "custom_tool_call"):
                seen = True
                tool_calls += 1
            elif kind == "event_msg" and payload.get("type") == "token_count":
                seen = True
                info = payload.get("info")
                totals = info.get("total_token_usage") if isinstance(info, dict) else None
                if isinstance(totals, dict):
                    values = [totals.get(key) for key in
                              ("input_tokens", "cached_input_tokens", "output_tokens")]
                    if all(type(value) is int and value >= 0 for value in values):
                        usage = values
            if kind in {"session_meta", "turn_context"} and isinstance(payload.get("cwd"), str):
                project = Path(payload["cwd"]).name or "unknown"
    if not seen:
        return None
    return {
        "platform": "codex", "session_id": session_id, "project": project,
        "model": model, "tool_calls": tool_calls, "total_cost": None,
        "tokens_prompt": usage[0] if usage is not None else None,
        "cache_read_tokens": usage[1] if usage is not None else None,
        "tokens_completion": usage[2] if usage is not None else None,
    }


def scan_codex_logs(logs_dir: Path) -> list[dict]:
    records = []
    for path in sorted(logs_dir.glob("**/rollout-*.jsonl")):
        try:
            record = parse_codex_transcript(path)
        except OSError:
            continue
        if record is not None:
            records.append(record)
    return records


# Harnesses whose session logs this scan reads. Others (Cursor, Gemini CLI) are left out
# of the report rather than shown as measured zeros.
SCANNED_HARNESSES = ("claude", "codex")


def build_report(records: list[dict], harnesses: list[str] | None = None) -> dict:
    """Keep dollar reducers and spikes confined to the existing Claude records."""
    claude = [r for r in records if r.get("platform", "claude") == "claude"]
    present = (set(harnesses or ()) | {r.get("platform", "claude") for r in records}) & set(SCANNED_HARNESSES)
    summaries = {}
    for harness in HARNESS_LABELS:
        if harness not in present:
            continue
        rows = [r for r in records if r.get("platform", "claude") == harness]
        recorded = [r for r in rows if r.get("tokens_prompt") is not None]
        summaries[harness] = {
            "label": HARNESS_LABELS[harness],
            "sessions_scanned": len(rows),
            "tool_calls": sum(r.get("tool_calls", 0) for r in rows),
            "tokens_recorded_sessions": len(recorded),
            "tokens_input": sum(r.get("tokens_prompt") or 0 for r in recorded) if recorded else None,
            "tokens_cached": sum(r.get("cache_read_tokens") or 0 for r in recorded) if recorded else None,
            "tokens_output": sum(r.get("tokens_completion") or 0 for r in recorded) if recorded else None,
            "total_cost": r_total_cost(rows) if harness == "claude" else None,
        }
    spikes = [
        {"session_id": r.get("session_id"), "project": r.get("project"),
         "estimated_cost": r.get("total_cost")}
        for r in claude if (r.get("total_cost") or 0) >= _SPIKE_THRESHOLD_USD
    ]
    # Calculate each harness separately: session IDs from different agents can collide.
    token_totals = [r_token_spend([r for r in records if r.get("platform", "claude") == h])
                    for h in summaries]
    return {
        "sessions_scanned": len(records), "harnesses": summaries,
        "total_cost": r_total_cost(claude),
        "token_spend": sum(v for v in token_totals if v is not None) if any(v is not None for v in token_totals) else None,
        "cost_per_task": r_cost_per_task(claude),
        "token_execution_density": r_token_density(claude),
        "spend_spikes": spikes, "spend_spike_threshold_usd": _SPIKE_THRESHOLD_USD,
        "kill_chain_findings": "SIMULATED — not wired in the first-blood cutline (wargames/03 Move 1 counter)",
        "note": "Claude Code total_cost is ESTIMATED from public per-token list pricing, not your actual invoice."
                if claude else "Dollar cost is unavailable; session logs record usage, not prices.",
    }


def render_report(report: dict) -> str:
    lines = ["Order Samurai -- first blood report",
             f"  sessions scanned:         {report['sessions_scanned']} (MEASURED)",
             # Before the spike list: the website installer stops printing at the first spike.
             f"  kill-chain findings:      {report['kill_chain_findings']}"]
    for harness, stats in report.get("harnesses", {}).items():
        lines += [f"  {stats['label']} (MEASURED):",
                  f"    sessions scanned: {stats['sessions_scanned']}",
                  f"    tool calls: {stats['tool_calls']}"]
        for name in ("input", "cached", "output"):
            value = stats[f"tokens_{name}"]
            lines.append(f"    tokens {name}: {value if value is not None else 'unavailable (not recorded)'}")
        lines.append(f"    token usage recorded in {stats['tokens_recorded_sessions']} of {stats['sessions_scanned']} sessions")
        if harness == "codex":
            lines.append("    Estimated cost: unavailable for Codex (logs record tokens, not prices)")
    if report["total_cost"] is not None:
        lines.append(f"  total cost (est.):        ${report['total_cost']}")
        cost_per_task = report["cost_per_task"]
        if cost_per_task is not None:
            lines.append(f"  cost per task (est.):     ${cost_per_task}")
        if report["token_spend"] is not None:
            lines.append(f"  token spend:              {report['token_spend']}")
        if report["token_execution_density"] is not None:
            lines.append(f"  token execution density:  {report['token_execution_density']}")
        if report["spend_spikes"]:
            lines.append(f"  spend spikes (>= ${report['spend_spike_threshold_usd']}):")
            for spike in report["spend_spikes"]:
                sid = (spike["session_id"] or "")[:8]
                lines.append(f"    - {spike['project']} / {sid}: ${spike['estimated_cost']}")
        else:
            lines.append(f"  spend spikes (>= ${report['spend_spike_threshold_usd']}): none")
    lines.append(f"  note: {report['note']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--logs-dir", default=str(Path.home() / ".claude" / "projects"),
                    help="Root containing */*.jsonl transcripts for the claude harness")
    ap.add_argument("--codex-dir", default=str(_HARNESS["codex_home"]() / "sessions"),
                    help="Codex sessions root (default: $CODEX_HOME/sessions)")
    ap.add_argument("--json", action="store_true", help="Emit the machine-readable report instead of text")
    args = ap.parse_args(argv)
    records = scan_logs(Path(args.logs_dir).expanduser()) + scan_codex_logs(Path(args.codex_dir).expanduser())
    harnesses = _HARNESS["installed_or_detected_harnesses"]()
    report = build_report(records, harnesses)
    labels = [HARNESS_LABELS[h] for h in report["harnesses"]]
    found = (" ".join(f"No {label} session logs found." for label in labels) if labels
             else "No Claude Code or Codex session logs found (the usage scan reads those two).")
    report["no_logs_message"] = (found + " Run a session first, then scan again; use --logs-dir or "
                                 "--codex-dir to choose a transcript folder.")
    if args.json:
        print(json.dumps(report, indent=2))
    elif not records:
        print("Order Samurai -- " + report["no_logs_message"])
    else:
        print(render_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
