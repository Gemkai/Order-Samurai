"""Generate the current Agentica OS state report.

HANDOFF.md is a chronological diary. This report is the current authoritative
snapshot: doctors, live metrics, platform parity, top reflexes, and vault health.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import aggregate as agg
from . import insights, verify_layers, verify_secrets
from .adapter import PlatformUnavailable, list_platforms, resolve_platform
from .verifiers import load_verifiers, run_all, summarize

_THIS = Path(__file__).resolve()
_ROOT = _THIS.parents[2]
_REPORTS = _ROOT / "Data" / "reports"


def _load_vault_health():
    path = _ROOT / "Knowledge" / "vault" / "_scripts" / "vault_health.py"
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location("agentica_vault_health", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _platform_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in list_platforms():
        try:
            platform = resolve_platform(name)
        except PlatformUnavailable as exc:
            rows.append({
                "platform": name,
                "available": "no",
                "telemetry": "no",
                "surface": "no",
                "ok": 0,
                "warn": 0,
                "fail": 1,
                "note": str(exc),
            })
            continue
        try:
            results = run_all(load_verifiers(name))
        except Exception as exc:
            results = [{"status": "FAIL", "label": "verifier-provider", "detail": repr(exc)}]
        counts, _ = summarize(results)
        note = "no verifier provider" if not results else ""
        # Freshness-aware telemetry status: a file that exists but hasn't been
        # written in 7+ days means the producer is dead — existence alone hid
        # the Antigravity pipeline being disabled for 4 days.
        telemetry = "no"
        if platform.telemetry_source.exists():
            age_days = (datetime.now(timezone.utc).timestamp()
                        - platform.telemetry_source.stat().st_mtime) / 86400
            if age_days > 7:
                telemetry = f"stale ({age_days:.0f}d)"
                counts["WARN"] += 1
                note = (note + "; " if note else "") + "telemetry producer appears dead"
            else:
                telemetry = "yes"
        rows.append({
            "platform": name,
            "available": "yes",
            "telemetry": telemetry,
            "surface": "yes" if platform.surface_matrix.exists() else "no",
            "ok": counts["OK"],
            "warn": counts["WARN"],
            "fail": counts["FAIL"],
            "note": note,
        })
    return rows


def _vault_snapshot() -> dict[str, Any]:
    vh = _load_vault_health()
    if vh is None:
        return {"available": False}
    pending = vh.check_raw_pending()
    counts = vh.wiki_article_counts()
    stale = vh.find_stale_articles()
    orphans = vh.find_orphaned_wiki()
    score = vh.compute_score(pending, counts, stale, orphans) if hasattr(vh, "compute_score") else None
    return {
        "available": True,
        "pending": len(pending),
        "articles": sum(counts.values()),
        "empty_domains": [k for k, v in counts.items() if v == 0],
        "stale": len(stale),
        "orphans": len(orphans),
        "score": score,
    }


def build_report(payload: dict[str, Any] | None = None, timestamp: str | None = None) -> str:
    payload = payload or agg.aggregate(timestamp=timestamp)
    timestamp = timestamp or datetime.now(timezone.utc).isoformat()
    live, sim = insights.count_live_sim(payload)
    layer_counts, _ = summarize(verify_layers.run_checks())
    security_counts, _ = summarize(verify_secrets.run_checks())
    platform_rows = _platform_rows()
    vault = _vault_snapshot()

    # Status counts, not scores — the weighted-mean pillar score was retired 2026-07-19.
    def _rollup_str(info: dict) -> str:
        r = info.get("rollup") or {}
        return f"{r.get('worst', '?')} ({r.get('passing', 0)}/{r.get('graded', 0)} passing)"
    score_line = ", ".join(
        f"{name} {_rollup_str(info)}" for name, info in payload.get("category_scores", {}).items()
    )
    records = ", ".join(f"{k}={v}" for k, v in payload.get("record_counts", {}).items())

    # Headline business metrics — lead with outcomes, not internal pillar scores.
    def _biz(pillar: str, group: str, key: str) -> tuple[str, bool]:
        env = payload.get("pillars", {}).get(pillar, {}).get(group, {}).get(key, {})
        flag = env.get("calibrated", True) is False or env.get("data_gap") is True
        return env.get("val", "—"), flag

    chains_dis, _ = _biz("sword", "Governance", "Kill_Chains_Disrupted")
    chains_det, _ = _biz("sword", "Governance", "Kill_Chains_Detected")
    craft_wins, craft_est = _biz("arts", "Craft", "Craft_Improvements")
    cost_saved, cost_est = _biz("brush", "Token Efficiency", "Estimated_Cost_Savings")
    agent_saved, agent_est = _biz("bow", "Activity", "Estimated_Agent_Time_Saved")

    def _e(flag: bool) -> str:
        return " (estimate, uncalibrated)" if flag else ""

    # Three-tier honesty: measured (calibrated live) / estimate (live but
    # uncalibrated or data-gapped) / simulated.
    estimates = 0
    for groups in payload.get("pillars", {}).values():
        for metrics in groups.values():
            for env in metrics.values():
                if not env.get("is_simulated") and (
                        env.get("calibrated", True) is False or env.get("data_gap") is True):
                    estimates += 1
    measured = live - estimates

    lines = [
        "# Agentica OS Current State",
        "",
        f"Generated: {timestamp}",
        "",
        "## Executive Snapshot",
        "",
        f"- Kill chains: {chains_dis} disrupted / {chains_det} detected this week.",
        f"- Craft improvements this week: {craft_wins} (promotions + arts deliverables){_e(craft_est)}.",
        f"- Cost savings this week: ${cost_saved}{_e(cost_est)}.",
        f"- Agent hours saved this week: {agent_saved}{_e(agent_est)}.",
        f"- Metrics: {measured} measured / {estimates} estimates / {sim} simulated.",
        f"- Records: {records}.",
        f"- Pillar status (supporting): {score_line}.",
        f"- Layer checks: OK={layer_counts['OK']} WARN={layer_counts['WARN']} FAIL={layer_counts['FAIL']}.",
        f"- Security checks: OK={security_counts['OK']} WARN={security_counts['WARN']} FAIL={security_counts['FAIL']}.",
        "",
        "## Platform Governance",
        "",
        "| Platform | Available | Telemetry | Surface Matrix | OK | WARN | FAIL | Note |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for row in platform_rows:
        lines.append(
            f"| {row['platform']} | {row['available']} | {row['telemetry']} | {row['surface']} | "
            f"{row['ok']} | {row['warn']} | {row['fail']} | {row['note']} |"
        )

    lines += ["", "## Knowledge Vault", ""]
    if vault.get("available"):
        empty = ", ".join(vault["empty_domains"]) or "none"
        lines += [
            f"- Score: {vault['score']}/100.",
            f"- Curated wiki articles: {vault['articles']}.",
            f"- Raw items pending compile: {vault['pending']}.",
            f"- Stale articles: {vault['stale']}.",
            f"- Orphaned wiki articles: {vault['orphans']}.",
            f"- Empty domains: {empty}.",
        ]
    else:
        lines.append("- Vault health script unavailable.")

    lines += ["", "## Top Reflexes", ""]
    reflexes = payload.get("reflexes", [])[:8]
    if not reflexes:
        lines.append("- No active reflexes.")
    else:
        for reflex in reflexes:
            cmd = reflex.get("command") or ""
            lines.append(f"- {reflex.get('tier')} [{reflex.get('source')}]: {reflex.get('message')} {cmd}".strip())

    lines += [
        "",
        "## Operating Notes",
        "",
        "- HANDOFF.md is historical. This file is the current-state snapshot.",
        "- Cumulative telemetry metrics should be interpreted with their score rules; weekly reports window activity by ISO week.",
        "- A platform with telemetry but missing verifiers is instrumented, not fully governed.",
        "",
    ]
    return "\n".join(lines)


def write_report(markdown: str | None = None, path: Path | None = None) -> Path:
    target = path or (_REPORTS / "current-state.md")
    target.parent.mkdir(parents=True, exist_ok=True)
    text = markdown or build_report()
    target.write_text(text, encoding="utf-8")
    if path is None:
        (_ROOT / "STATE.md").write_text(text, encoding="utf-8")
    return target


def main() -> int:
    path = write_report()
    print(f"state_report: wrote {path} and {_ROOT / 'STATE.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
