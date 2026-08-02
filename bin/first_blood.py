#!/usr/bin/env python3
"""One-command "first blood" report: point Order Samurai at your existing Claude Code
session logs and get a real cost report in minutes — no daemon, no account, no config.

Value Equation fix (validation-idea-filter.md Step 4): time-to-first-result was Order
Samurai's weakest lever ("value shows after telemetry accumulates"). This reads directly
from ~/.claude/projects/*/*.jsonl — the session transcripts Claude Code already writes —
so the first report is available immediately, before any daemon or scout ever runs.

Reuses the SAME canonical record shape (agentica_core.emit.build_record) and the SAME
cost/density reducers (agentica_core.aggregate) the live dashboard grades Brush with —
one cost calculation, not two (CLAUDE.md Principle #7: DRY at the knowledge level).
Read-only: this script never writes to your real telemetry store.

Usage:
    python3 bin/first_blood.py                    # scan ~/.claude/projects
    python3 bin/first_blood.py --logs-dir DIR      # scan a different transcript root
    python3 bin/first_blood.py --json              # machine-readable report
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_THIS = Path(__file__).resolve()
_ROOT = _THIS.parents[1]        # Order Samurai
_GOV = _ROOT.parent             # Governance
if str(_GOV) not in sys.path:
    sys.path.insert(0, str(_GOV))

from agentica_core.emit import build_record  # noqa: E402  — canonical record shape, reused not reimplemented
from agentica_core.aggregate import (  # noqa: E402  — SAME reducers the live dashboard grades Brush with
    r_cost_per_task,
    r_token_density,
    r_token_spend,
    r_total_cost,
)

_DEFAULT_LOGS_DIR = Path.home() / ".claude" / "projects"

# Estimated USD per-million-token pricing (public Anthropic list pricing). Always labelled
# ESTIMATED in the report — this is never a substitute for your actual invoice. Mirrors the
# estimation approach the SessionEnd emitter (scripts/agentica_emit.py) already uses, so a
# user who later wires the live hook sees consistent numbers, not a second cost formula.
_PRICE_PER_M: dict[str, tuple[float, float, float]] = {
    "opus": (15.0, 75.0, 1.50),
    "sonnet": (3.0, 15.0, 0.30),
    "haiku": (0.8, 4.0, 0.08),
    "fable": (12.0, 60.0, 1.20),
}
_DEFAULT_PRICE = _PRICE_PER_M["sonnet"]

# Sessions at/above this estimated cost are flagged as a spend-spike signal — the literal
# $6,000-overnight failure mode Order Samurai exists to catch. This is one honest, measured
# threshold check, not the full kill-chain taxonomy (out of scope for this cutline — see
# wargames/03-order-samurai-commercialization.md Move 1 failure/counter).
_SPIKE_THRESHOLD_USD = 5.0


def _tier_for(model: str) -> str:
    m = (model or "").lower()
    if "opus" in m or "fable" in m or "mythos" in m:
        return "PREMIUM"
    if "sonnet" in m:
        return "STANDARD"
    if "haiku" in m:
        return "FAST"
    return "unknown"


def _price_for(model: str) -> tuple[float, float, float]:
    m = (model or "").lower()
    for key, price in _PRICE_PER_M.items():
        if key in m:
            return price
    return _DEFAULT_PRICE


def estimate_cost(model: str, tokens_in: int, tokens_out: int, cache_read: int) -> float:
    p_in, p_out, p_cache = _price_for(model)
    return round(
        tokens_in / 1_000_000 * p_in
        + tokens_out / 1_000_000 * p_out
        + cache_read / 1_000_000 * p_cache,
        4,
    )


def parse_transcript(path: Path) -> dict | None:
    """Read one Claude Code session transcript (JSONL) and return a canonical-record-shaped
    dict, or None if the file has no assistant turns (a stub, not a session). A malformed
    line is skipped, never the whole session — a bad line must not lose a real one."""
    tokens_in = tokens_out = cache_read = 0
    model = ""
    last_ts: str | None = None
    turns = 0
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("type") != "assistant":
                continue
            turns += 1
            msg = rec.get("message") or {}
            usage = msg.get("usage") or {}
            tokens_in += int(usage.get("input_tokens") or 0) + int(
                usage.get("cache_creation_input_tokens") or 0
            )
            tokens_out += int(usage.get("output_tokens") or 0)
            cache_read += int(usage.get("cache_read_input_tokens") or 0)
            model = msg.get("model") or model
            ts = rec.get("timestamp")
            if ts:
                last_ts = ts
    if turns == 0:
        return None
    cost = estimate_cost(model, tokens_in, tokens_out, cache_read)
    project = path.parent.name.rsplit("-", 1)[-1] or "unknown"
    return build_record(
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
        total_cost=cost,
    )


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


def build_report(records: list[dict]) -> dict:
    """Pure — the same reducers the live dashboard grades Brush with (no second cost
    formula). Empty input is an honest zero-session report, not a crash or a fake number."""
    spikes = [
        {
            "session_id": r.get("session_id"),
            "project": r.get("project"),
            "estimated_cost": r.get("total_cost"),
        }
        for r in records
        if (r.get("total_cost") or 0) >= _SPIKE_THRESHOLD_USD
    ]
    return {
        "sessions_scanned": len(records),
        "total_cost": r_total_cost(records),
        "token_spend": r_token_spend(records),
        "cost_per_task": r_cost_per_task(records),
        "token_execution_density": r_token_density(records),
        "spend_spikes": spikes,
        "spend_spike_threshold_usd": _SPIKE_THRESHOLD_USD,
        "kill_chain_findings": "SIMULATED — not wired in the first-blood cutline (wargames/03 Move 1 counter)",
        "note": "total_cost is ESTIMATED from public per-token list pricing, not your actual invoice.",
    }


def render_report(report: dict) -> str:
    def _line(label: str, key: str, prefix: str = "") -> str:
        val = report[key]
        return f"  {label:<26}{prefix}{val}" if val is not None else f"  {label:<26}SIMULATED (no data found)"

    lines = [
        "Order Samurai -- first blood report",
        f"  sessions scanned:         {report['sessions_scanned']}",
        _line("total cost (est.):", "total_cost", "$"),
        _line("cost per task (est.):", "cost_per_task", "$"),
        _line("token spend:", "token_spend"),
        _line("token execution density:", "token_execution_density"),
        f"  kill-chain findings:      {report['kill_chain_findings']}",
    ]
    if report["spend_spikes"]:
        lines.append(f"  spend spikes (>= ${report['spend_spike_threshold_usd']}):")
        for s in report["spend_spikes"]:
            sid = (s["session_id"] or "")[:8]
            lines.append(f"    - {s['project']} / {sid}: ${s['estimated_cost']}")
    else:
        lines.append(f"  spend spikes (>= ${report['spend_spike_threshold_usd']}): none")
    lines.append(f"  note: {report['note']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--logs-dir", default=str(_DEFAULT_LOGS_DIR), help="Root containing */*.jsonl transcripts")
    ap.add_argument("--json", action="store_true", help="Emit the machine-readable report instead of text")
    args = ap.parse_args(argv)

    logs_dir = Path(args.logs_dir).expanduser()
    records = scan_logs(logs_dir)
    report = build_report(records)

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    if not records:
        print(f"Order Samurai -- no Claude Code session logs found under {logs_dir}")
        print("Run a Claude Code session first, or point --logs-dir at a transcript folder.")
        return 0

    print(render_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
