"""Reflex Eureka — learning-loop analytics for autonomous reflex effectiveness.

Reads exec_log.jsonl (written by ReflexEngine), groups runs by (skill, metric),
and computes per-(skill, metric) improvement rates.  The `improved` field (added
to exec_log in the blast-radius update) is used directly when present; older
entries fall back to the exit-code proxy (status == 'done').

Output: ~/.claude/data/auto_eureka_skills.md — a human-readable analysis file
with GOTCHA/RULE/CONTEXT findings for operator review. When findings exist and
the content changed, a timestamped copy is also exported to
~/.claude/.tmp/intelligence/auto_eureka_<ts>.md, where knowledge_sync.py and
lesson_graduate.py pick it up (the documented lesson-pipeline closure).

Run automatically by refresh_dashboard.py (non-fatal) or standalone:
  python -m agentica_core.reflex_eureka
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Output location — consistent with auto_eureka.py's data directory
_DATA_DIR = Path.home() / ".claude" / "data"
_FINDINGS_FILE = _DATA_DIR / "auto_eureka_skills.md"

# Only flag skills with at least this many reflex runs (avoids noise from single fires)
_MIN_RUNS = 5

# Below this improvement rate → GOTCHA (skill rarely resolves its target metric)
_LOW_IMPROVEMENT_THRESHOLD = 0.30

# Above this → RULE (skill reliably resolves its target metric)
_HIGH_IMPROVEMENT_THRESHOLD = 0.70


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_exec_log(log_path: Path) -> list[dict]:
    """Parse exec_log.jsonl, returning only reflex_engine entries with a reflex_id."""
    records: list[dict] = []
    if not log_path.exists():
        return records
    for ln in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
            if r.get("source") == "reflex_engine" and r.get("reflex_id"):
                records.append(r)
        except (json.JSONDecodeError, TypeError):
            pass
    return records


def _was_effective(record: dict) -> bool:
    """Did this reflex run actually resolve the triggering metric?

    Prefers the explicit 'improved' boolean (added to exec_log in the blast-radius
    update) over the coarser exit-code proxy.  This distinction matters: a skill
    can exit 0 without the metric actually moving past its threshold.

    Read-only mechanisms (detect scripts) are the exception: they produce a verdict
    but never move their own metric, so 'improved' is always false. Grade them by a
    clean run (exit-0) instead, else they grade to 0% and demote despite working.
    """
    if record.get("kind") == "mechanism" and record.get("read_only") is True:
        return record.get("status") == "done"  # verdict produced = success
    if "improved" in record:
        return bool(record["improved"])
    # Fallback for older log entries: treat exit-0 as a proxy for improvement
    return record.get("status") == "done"


def _parse_reflex_id(reflex_id: str) -> tuple[str, str]:
    """Extract (pillar, metric) from a reflex_id such as 'metric:bow:Error_Rate'."""
    parts = reflex_id.split(":", 2)
    if len(parts) == 3:
        return parts[1], parts[2]
    return "unknown", reflex_id


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

def analyze(log_path: Path, out_path: Path = _FINDINGS_FILE) -> dict:
    """Analyze exec_log and write findings to out_path.

    Returns a summary dict: {total_entries, skill_metric_pairs, gotchas, rules, context}.
    Always writes the output file — even if exec_log is empty (writes a 'no data' notice).
    """
    records = _load_exec_log(log_path)

    # Group runs by (skill, pillar, metric)
    groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for r in records:
        skill = r.get("skill")
        if not skill:
            parts = (r.get("command") or "unknown").strip().lstrip("/").split()
            skill = parts[0] if parts else "unknown"
        reflex_id = r.get("reflex_id", "")
        pillar, metric = _parse_reflex_id(reflex_id)
        groups[(skill, pillar, metric)].append({
            "effective":      _was_effective(r),
            "timestamp":      r.get("timestamp", ""),
            "status":         r.get("status", "unknown"),
            "has_improved":   "improved" in r,         # track data-quality coverage
            "files_changed":  r.get("files_changed", []),
        })

    # Compute per-(skill, metric) stats
    stats: list[dict] = []
    for (skill, pillar, metric), runs in groups.items():
        total = len(runs)
        effective_count = sum(1 for r in runs if r["effective"])
        has_improved_count = sum(1 for r in runs if r["has_improved"])
        rate = round(effective_count / total, 3) if total else None
        stats.append({
            "skill":              skill,
            "pillar":             pillar,
            "metric":             metric,
            "total_runs":         total,
            "effective_count":    effective_count,
            "has_improved_count": has_improved_count,
            "effectiveness_rate": rate,
            "latest_run":         max((r["timestamp"] for r in runs), default=""),
        })

    # Sort ascending by rate so worst offenders appear first in GOTCHA section
    stats.sort(key=lambda x: (x["effectiveness_rate"] is None, x["effectiveness_rate"] or 0,
                               -x["total_runs"]))

    # Classify by effectiveness (only count pairs with enough data)
    mature = [s for s in stats if s["total_runs"] >= _MIN_RUNS]
    gotchas = [s for s in mature if (s["effectiveness_rate"] or 1) < _LOW_IMPROVEMENT_THRESHOLD]
    rules   = [s for s in mature if (s["effectiveness_rate"] or 0) >= _HIGH_IMPROVEMENT_THRESHOLD]
    context = [s for s in mature
               if _LOW_IMPROVEMENT_THRESHOLD <= (s["effectiveness_rate"] or 0) < _HIGH_IMPROVEMENT_THRESHOLD]

    # ── Write findings ────────────────────────────────────────────────────────
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with_improved = sum(1 for r in records if "improved" in r)

    lines: list[str] = [
        "# Auto Eureka — Reflex Skill Effectiveness",
        f"_Generated: {ts}_",
        (f"_Source: exec_log.jsonl — {len(records)} reflex entries, "
         f"{len(groups)} skill-metric pairs, "
         f"{with_improved}/{len(records)} entries with explicit `improved` field_"),
        "",
    ]

    if not records:
        lines += [
            "_No reflex engine exec_log entries found yet._",
            "_Findings will populate once autonomous reflexes start firing._",
            "",
        ]
    else:
        if gotchas:
            lines += [
                "## GOTCHA — Skills That Rarely Resolve Their Target Metric",
                "",
                (f"Skills below fire autonomously but have < {int(_LOW_IMPROVEMENT_THRESHOLD*100)}% "
                 "actual metric improvement rate."),
                "Consider: wrong skill for the metric? Metric not resolvable by automation? Threshold miscalibrated?",
                "",
            ]
            for s in gotchas:
                rate_pct = f"{(s['effectiveness_rate'] or 0)*100:.0f}%"
                lines += [
                    f"- **`/{s['skill']}`** → `{s['pillar']}/{s['metric']}`",
                    (f"  {s['effective_count']}/{s['total_runs']} runs improved the metric "
                     f"({rate_pct}) · last run: {s['latest_run'][:10]}"),
                    "  → Consider: different skill in METRIC_CONFIG, manual-only gate, or remove reflex",
                    "",
                ]

        if rules:
            lines += [
                "## RULE — High-Effectiveness Skills",
                "",
                (f"Skills with ≥ {int(_HIGH_IMPROVEMENT_THRESHOLD*100)}% improvement rate "
                 "— proven autonomous remediators."),
                "",
            ]
            for s in rules:
                rate_pct = f"{(s['effectiveness_rate'] or 0)*100:.0f}%"
                lines += [
                    f"- **`/{s['skill']}`** → `{s['pillar']}/{s['metric']}`",
                    (f"  {s['effective_count']}/{s['total_runs']} runs improved the metric "
                     f"({rate_pct}) · last run: {s['latest_run'][:10]}"),
                    "",
                ]

        if context:
            lines += [
                "## CONTEXT — Mixed Effectiveness (30–70%)",
                "",
                "These skills sometimes help. More data or different conditions may explain variance.",
                "",
            ]
            for s in context:
                rate_pct = f"{(s['effectiveness_rate'] or 0)*100:.0f}%"
                lines += [
                    f"- **`/{s['skill']}`** → `{s['pillar']}/{s['metric']}`",
                    (f"  {s['effective_count']}/{s['total_runs']} runs improved the metric "
                     f"({rate_pct}) · last run: {s['latest_run'][:10]}"),
                    "",
                ]

        new_pairs = [s for s in stats if s["total_runs"] < _MIN_RUNS]
        if new_pairs:
            lines += [
                f"## Insufficient Data (< {_MIN_RUNS} runs — not yet classified)",
                "",
            ]
            for s in new_pairs:
                rate_pct = (f"{(s['effectiveness_rate'] or 0)*100:.0f}%"
                            if s["effectiveness_rate"] is not None else "n/a")
                lines.append(
                    f"- `/{s['skill']}` → `{s['pillar']}/{s['metric']}` "
                    f"({s['total_runs']} run{'s' if s['total_runs'] != 1 else ''}, {rate_pct})"
                )
            lines.append("")

    lines += [
        "## Data-Quality Coverage",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Reflex entries analyzed | {len(records)} |",
        f"| Unique skill-metric pairs | {len(groups)} |",
        f"| Entries with explicit `improved` field | {with_improved} |",
        f"| Entries using exit-code fallback | {len(records) - with_improved} |",
        f"| Pairs with enough data (≥ {_MIN_RUNS} runs) | {len(mature)} |",
        f"| GOTCHAs | {len(gotchas)} |",
        f"| RULEs | {len(rules)} |",
        f"| CONTEXT | {len(context)} |",
        "",
        ("> Tip: older exec_log entries lack the `improved` field and use exit-code as proxy. "
         "Over time, as more reflexes fire with the new schema, accuracy will improve."),
        "",
    ]

    content = "\n".join(lines)
    out_path.write_text(content, encoding="utf-8")
    _export_to_lesson_pipeline(content, has_findings=bool(gotchas or rules or context))
    return {
        "total_entries":      len(records),
        "skill_metric_pairs": len(groups),
        "gotchas":            len(gotchas),
        "rules":              len(rules),
        "context":            len(context),
    }


def _export_to_lesson_pipeline(content: str, has_findings: bool) -> None:
    """Close the learning loop: drop a timestamped copy into the intelligence dir
    that knowledge_sync.py / lesson_graduate.py actually glob (auto_eureka_*.md in
    ~/.claude/.tmp/intelligence/). Exports only when there are classified findings
    AND the content changed since the last export — refresh runs every 15 minutes
    and must not spam the lesson pipeline with identical files."""
    if not has_findings:
        return
    try:
        import hashlib
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        state_file = _DATA_DIR / "auto_eureka_skills_export.json"
        try:
            prev = json.loads(state_file.read_text(encoding="utf-8")).get("sha256")
        except (OSError, ValueError):
            prev = None
        if digest == prev:
            return
        intel_dir = Path.home() / ".claude" / ".tmp" / "intelligence"
        intel_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        (intel_dir / f"auto_eureka_{stamp}.md").write_text(content, encoding="utf-8")
        state_file.write_text(json.dumps({"sha256": digest, "exported_at": stamp}),
                              encoding="utf-8")
    except Exception:
        pass  # export is best-effort — never break the refresh pipeline


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    _THIS = Path(__file__).resolve()
    _local_root = _THIS.parents[1]
    if (_local_root / "config").exists() and not (_local_root / "Order Samurai").exists():
        _default_root = _local_root
    else:
        _default_root = _local_root / "Order Samurai"
    _root = os.environ.get("ORDER_SAMURAI_ROOT", str(_default_root))
    _log_path = Path(_root) / "state" / "exec_log.jsonl"
    result = analyze(_log_path)
    print(
        f"reflex_eureka: {result['total_entries']} entries → "
        f"{result['gotchas']} gotchas, {result['rules']} rules, "
        f"{result['context']} context [{result['skill_metric_pairs']} pairs]"
    )
    print(f"Findings written to: {_FINDINGS_FILE}")
    sys.exit(0)
