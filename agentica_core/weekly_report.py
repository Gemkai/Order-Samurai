"""Weekly ecosystem reports. Telemetry record counts on the dashboard are CUMULATIVE
(append-only); these reports window the data to each ISO week so you see what actually
happened that week, per ecosystem (claude / antigravity / codex).

Each ecosystem cuts across all four pillar areas (the Jarvis 4): Bow=Operations,
Sword=Security, Brush=Architecture & Cost, Arts=Craft & UX. One .md per (week, ecosystem)
is written to Data/reports/, plus an index.json the dashboard lists.

  python -m agentica_core.weekly_report            # backfill every week present
  python -m agentica_core.weekly_report --latest   # only the current ISO week
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import aggregate as agg
from .telemetry import iso_week

_THIS = Path(__file__).resolve()
_REPORTS = _THIS.parents[2] / "Data" / "reports"


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".") if v != int(v) else f"{int(v):,}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _table(rows: list[tuple[str, object]]) -> str:
    out = ["| Metric | Value |", "| --- | --- |"]
    out += [f"| {k} | {_fmt(v)} |" for k, v in rows]
    return "\n".join(out)


def _security_rows(payload: dict, platform: str) -> list[tuple[str, object]]:
    """Current Sword/security snapshot for an ecosystem (e.g. Jarvis = antigravity),
    pulled from the live per-platform pillars. Point-in-time, not weekly history."""
    sw = (payload.get("by_platform", {}).get(platform, {}) or {}).get("sword", {})
    rows: list[tuple[str, object]] = []
    for group in sw.values():
        for mk, env in group.items():
            if not env.get("is_simulated"):
                rows.append((mk.replace("_", " "), env.get("val")))
    return rows


_PILLAR_LABEL = {"bow": "Bow (Ops)", "sword": "Sword (Sec)", "brush": "Brush (Arch)", "arts": "Arts (UX)"}
_PILLAR_COLOR = {"bow": "var(--bow)", "sword": "var(--sword)", "brush": "var(--brush)", "arts": "var(--arts)"}

# Hex values matching index.css CSS vars (used in SVG which can't reference CSS vars in attributes)
_PILLAR_HEX = {"bow": "#facc15", "sword": "#ef4444", "brush": "#f472b6", "arts": "#f5f5f5"}


def _compute_week_scores(by_week: "dict[str, list]") -> "dict[str, dict[str, float]]":
    """Compute pillar scores for every week bucket. Returns {week: {pillar_key: score}}."""
    from . import insights
    result: dict[str, dict[str, float]] = {}
    for w in sorted(by_week):
        scores = insights.annotate(agg.build_pillars(by_week[w]))
        result[w] = {pk: scores[pk]["score"] for pk in ("bow", "sword", "brush", "arts")}
    return result


def _pillar_trend_svg(week: str, all_week_scores: "dict[str, dict[str, float]]") -> str:
    """Inline SVG line chart: 4 pillar score lines across the last ≤7 weeks.
    Returns empty string when there are fewer than 2 data points."""
    all_weeks = sorted(all_week_scores)
    if week not in all_weeks:
        return ""
    idx = all_weeks.index(week)
    window = all_weeks[max(0, idx - 6): idx + 1]
    if len(window) < 2:
        return ""

    W, H = 580, 170
    pl, pr, pt, pb = 34, 16, 24, 28
    cw = W - pl - pr
    ch = H - pt - pb
    n = len(window)

    pillars_meta = [
        ("bow",   _PILLAR_HEX["bow"],   "Bow"),
        ("sword", _PILLAR_HEX["sword"], "Sword"),
        ("brush", _PILLAR_HEX["brush"], "Brush"),
        ("arts",  _PILLAR_HEX["arts"],  "Arts"),
    ]

    parts: list[str] = []
    parts.append(f'<rect width="{W}" height="{H}" fill="#0c0c0c" rx="10"/>')

    # Horizontal grid lines
    for score in (25, 50, 75, 100):
        y = pt + (1 - score / 100) * ch
        parts.append(f'<line x1="{pl}" y1="{y:.1f}" x2="{W - pr}" y2="{y:.1f}" stroke="#222" stroke-width="1"/>')
        parts.append(f'<text x="{pl - 4}" y="{y + 3:.1f}" fill="#444" font-size="8" text-anchor="end" font-family="monospace">{score}</text>')

    # One polyline + end dot per pillar
    for pk, color, _label in pillars_meta:
        pts_xy: list[tuple[float, float]] = []
        for i, w in enumerate(window):
            sc = all_week_scores[w].get(pk)
            if sc is None:
                continue
            x = pl + (i / (n - 1)) * cw
            y = pt + (1 - sc / 100) * ch
            pts_xy.append((x, y))
        if len(pts_xy) < 2:
            continue
        pts_str = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts_xy)
        parts.append(f'<polyline points="{pts_str}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')
        lx, ly = pts_xy[-1]
        parts.append(f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3.5" fill="{color}"/>')

    # X-axis week labels (current week in bold)
    for i, w in enumerate(window):
        x = pl + (i / (n - 1)) * cw
        lbl = "W" + w.split("-W")[-1] if "-W" in w else w[-3:]
        bold = "bold" if w == week else "normal"
        col = "#aaa" if w == week else "#555"
        parts.append(f'<text x="{x:.1f}" y="{H - 6}" fill="{col}" font-size="8" font-weight="{bold}" text-anchor="middle" font-family="monospace">{lbl}</text>')

    # Legend row above grid
    for i, (pk, color, label) in enumerate(pillars_meta):
        lx = pl + i * 125
        ly = pt - 8
        parts.append(f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3" fill="{color}"/>')
        parts.append(f'<text x="{lx + 6:.1f}" y="{ly + 3:.1f}" fill="{color}" font-size="8" font-family="monospace">{label}</text>')

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'style="width:100%;max-width:580px;display:block;margin:10px 0;border-radius:10px">\n'
        + "\n".join(parts)
        + "\n</svg>"
    )


def _weekly_radar(recs: list[dict]) -> str:
    """Text 'radar' fallback — used only when there is a single data point (no trend yet)."""
    from . import insights
    scores = insights.annotate(agg.build_pillars(recs))
    out = ["| Pillar | Score | |", "| --- | --- | --- |"]
    for pk in ("bow", "sword", "brush", "arts"):
        sc = scores[pk]["score"]
        if sc is None:
            out.append(f"| {_PILLAR_LABEL[pk]} | —/100 | {{{{{_PILLAR_COLOR[pk]}::░░░░░░░░░░░░░░░░░░░░}}}} |")
            continue
        s = int(round(sc))
        fill = round(s / 5)
        bar = "█" * fill + "░" * (20 - fill)
        out.append(f"| {_PILLAR_LABEL[pk]} | {s}/100 | {{{{c:{_PILLAR_COLOR[pk]}::{bar}}}}} |")
    return "\n".join(out)


def _sword_section(payload: dict, platform: str) -> str:
    rows = _security_rows(payload, platform)
    if not rows:
        return ("No security scan wired for this ecosystem yet, so no Sword signals to report. "
                "(Going forward, as scans are added the metrics here will change.)")
    return (_table(rows) + "\n\n_Point-in-time snapshot from the live runtime scan, not weekly "
            "history — weekly security history is not retained yet, so past weeks show the current "
            "values. Going forward these become per-week as snapshots accumulate._")


def _report_md(platform: str, week: str, recs: list[dict], total_all: int, payload: dict,
               all_week_scores: "dict[str, dict[str, float]] | None" = None) -> str:
    sessions = agg.r_session_count(recs) or 1
    frust = sum(agg._int_vals(recs, "frustration_signals"))
    rework = sum(agg._int_vals(recs, "rework_turns"))
    bow = [
        ("Tasks (throughput)", agg.r_count(recs)),
        ("Error rate %", agg.r_error_rate(recs)),
        ("Tool calls", agg.r_tool_volume(recs)),
        ("Tool diversity", agg.r_tool_diversity(recs)),
        ("Sessions", agg.r_session_count(recs)),
        ("Avg turns / session", agg.r_avg_session_turns(recs)),
        ("Latency P50 (ms)", agg.r_lat(50)(recs)),
        ("Latency P95 (ms)", agg.r_lat(95)(recs)),
    ]
    brush = [
        ("Total cost ($)", agg.r_total_cost(recs)),
        ("Token spend", agg.r_token_spend(recs)),
        ("Cost / task ($)", agg.r_cost_per_task(recs)),
        ("Token exec density", agg.r_token_density(recs)),
        ("Model tier mix", agg.r_model_tier_mix(recs)),
        ("Subagent spawns", agg.r_sum_field("subagent_spawns")(recs)),
    ]
    arts = [
        ("Slop density / 1k words", agg.r_slop_density(recs)),
        ("Frustration signals (total)", frust),
        ("Frustration / session", round(frust / sessions, 2)),
        ("Rework loops (total)", rework),
        ("Rework / session", round(rework / sessions, 2)),
    ]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    trend_svg = _pillar_trend_svg(week, all_week_scores or {})
    if trend_svg:
        radar_block = trend_svg
        radar_note = f"_4-pillar score trend for {platform}. Current week in bold. Bow=Ops · Sword=Sec · Brush=Arch · Arts=UX._"
    else:
        radar_block = _weekly_radar(recs)
        radar_note = ("_Telemetry-derived pillar scores for this week only (the dashboard radar shows the same, "
                      "windowed weekly). Sword here is telemetry-light; the live security snapshot is below._")

    return f"""# Weekly Report — {platform} — {week}

_Generated {now}. Records this week: **{len(recs)}** (cumulative all-time for {platform}: {total_all:,})._

> Ecosystem record totals on the dashboard are **cumulative** (append-only telemetry).
> This report windows to the single ISO week so trends are real, not ever-growing sums.
> Each ecosystem spans the four pillar areas (the Jarvis 4): Bow=Operations,
> Sword=Security, Brush=Architecture & Cost, Arts=Craft & UX.

## 📈 Pillar Score Trend
{radar_block}

{radar_note}

## 🏹 Bow — Operations
{_table(bow)}

## ⚔️ Sword — Security
{_sword_section(payload, platform)}

## 🖌️ Brush — Architecture & Cost
{_table(brush)}

## 🎭 Arts — Craft & UX
{_table(arts)}
"""


def _load_payload() -> dict:
    try:
        return json.loads(agg.default_payload_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _rebuild_index() -> list[dict]:
    """Index ALL report .md present on disk (not just ones written this run), so a
    latest-only refresh never clobbers the historical list the UI reads."""
    import re
    _HDR_BYTES = 4096  # "Records this week:" always appears in the first ~1 KB of the header
    index: list[dict] = []
    for f in _REPORTS.glob("*.md"):
        m = re.match(r"(.+?)__(.+)\.md$", f.name)
        if not m:
            continue
        try:
            # Read only the header slice — avoids full-file I/O for large historical reports.
            with f.open(encoding="utf-8", errors="ignore") as fh:
                head = fh.read(_HDR_BYTES)
        except OSError:
            head = ""
        rec = re.search(r"Records this week:\s*\*\*(\d+)\*\*", head)
        index.append({"file": f.name, "week": m.group(1), "platform": m.group(2),
                      "records": int(rec.group(1)) if rec else 0})
    index.sort(key=lambda x: (x["week"], x["platform"]), reverse=True)
    (_REPORTS / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    return index


def generate(latest_only: bool = False) -> list[dict]:
    _REPORTS.mkdir(parents=True, exist_ok=True)
    payload = _load_payload()
    this_week = datetime.now(timezone.utc).strftime("%G-W%V")
    for platform in agg.list_platforms():
        recs = agg.load_records(platform)
        total = len(recs)
        by_week: dict[str, list] = defaultdict(list)
        for r in recs:
            w = iso_week(r.get("timestamp", ""))
            if w:
                by_week[w].append(r)
        # Pre-compute all weeks' pillar scores for this platform's trend chart
        all_week_scores = _compute_week_scores(by_week)
        for week, wrecs in by_week.items():
            if latest_only and week != this_week:
                continue
            fname = f"{week}__{platform}.md"
            (_REPORTS / fname).write_text(
                _report_md(platform, week, wrecs, total, payload, all_week_scores),
                encoding="utf-8",
            )
    return _rebuild_index()  # always index every file on disk


def main() -> int:
    idx = generate(latest_only="--latest" in sys.argv)
    print(f"weekly_report: wrote {len(idx)} reports -> {_REPORTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
