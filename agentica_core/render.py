"""Static HTML renderer for the unified dashboard (the Jarvis replacement).

Renders the WIDPayload to a self-contained HTML file with the metric cards built SERVER-SIDE
(pure Python) — ZERO JavaScript required for the core view, so it can never fail to a blank
screen. Per-platform views use native <details> (also no JS). 4 pillar grids, SIMULATED
metrics dashed + tier-badged. Zero dependencies.

Multi-window mode: pass render_html({"week": p7, "month": p30, "total": p_all}) to render
a Week/Month/Total segmented control. Single-payload call (render_html(payload)) still works
for backward compat (treated as the "month" window).
"""
from __future__ import annotations

import html
import json
from pathlib import Path

from . import insights

_THIS = Path(__file__).resolve()

_PILLARS = [
    ("bow", "🏹 Bow — Operational"),
    ("sword", "⚔️ Sword — Security"),
    ("brush", "🖌️ Brush — Architecture & Tokens"),
    ("arts", "🎭 Arts — UX & Docs"),
]

_WINDOW_KEYS = frozenset({"week", "month", "total"})

_CSS = """
:root{--bg:#0d1117;--panel:#161b22;--line:#30363d;--ink:#e6edf3;--dim:#8b949e;
--auto:#3fb950;--derived:#58a6ff;--sim:#6e7681}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.4 ui-sans-serif,Segoe UI,Roboto,Helvetica,Arial}
header{padding:18px 24px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:20px;flex-wrap:wrap}
h1{font-size:18px;margin:0;letter-spacing:.5px}
.meta{color:var(--dim);font-size:12px}
.vlabel{padding:18px 24px 0;font-size:13px;color:var(--dim);text-transform:uppercase;letter-spacing:.6px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px;padding:16px 24px 24px}
section{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px}
section h2{margin:0 0 12px;font-size:15px}
.group h3{margin:14px 0 8px;font-size:12px;color:var(--dim);text-transform:uppercase;letter-spacing:.6px}
.cards{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.card{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px}
.card.sim{border-style:dashed;opacity:.55}
.k{font-size:11px;color:var(--dim)}
.v{font-size:20px;font-weight:600;margin:2px 0}
.tier{font-size:10px;font-weight:700;letter-spacing:.5px}
.tier.AUTO{color:var(--auto)}.tier.DERIVED{color:var(--derived)}.tier.SIMULATED{color:var(--sim)}
details{margin:0 24px 12px;border:1px solid var(--line);border-radius:10px}
summary{cursor:pointer;padding:12px 16px;color:var(--ink);font-weight:600}
.wc{margin-left:auto;display:flex;gap:0;border:1px solid var(--line);border-radius:6px;overflow:hidden}
.wc-btn{padding:5px 14px;background:transparent;color:var(--dim);border:none;cursor:pointer;font-size:12px;font-weight:600;transition:background .15s,color .15s}
.wc-btn:hover{background:var(--line);color:var(--ink)}
.wc-btn.active{background:var(--derived);color:#000}
.main-meta{display:none}
.win-note{color:var(--dim);font-size:12px;padding:2px 24px 6px;max-width:78ch;line-height:1.5}
.win-note b{color:var(--ink);font-weight:600}
"""

_JS = """<script>
(function(){
var KEY='agentica-win';
function setWin(w){
  document.querySelectorAll('.main-grid').forEach(function(el){el.style.display=el.dataset.window===w?'':'none';});
  document.querySelectorAll('.main-meta').forEach(function(el){el.style.display=el.dataset.window===w?'inline':'none';});
  document.querySelectorAll('.wc-btn').forEach(function(el){el.classList.toggle('active',el.dataset.w===w);});
  try{localStorage.setItem(KEY,w);}catch(e){}
}
document.querySelectorAll('.wc-btn').forEach(function(b){b.addEventListener('click',function(){setWin(b.dataset.w);});});
var saved='month';try{saved=localStorage.getItem(KEY)||'month';}catch(e){}
setWin(saved);
})();
</script>"""


def _fmt(env: dict) -> str:
    if env["is_simulated"]:
        return "—"
    val = html.escape(str(env["val"]))
    return val + "%" if env.get("is_percent") else val


def _card(key: str, env: dict) -> str:
    sim = " sim" if env["is_simulated"] else ""
    tier = html.escape(env["tier"])
    return (f'<div class="card{sim}"><div class="k">{html.escape(key.replace("_", " "))}</div>'
            f'<div class="v">{_fmt(env)}</div><div class="tier {tier}">{tier}</div></div>')


def _pillars_html(pillars: dict) -> str:
    out = []
    for pk, label in _PILLARS:
        groups = pillars.get(pk, {})
        parts = [f"<section><h2>{html.escape(label)}</h2>"]
        if not groups:
            parts.append('<div class="meta">no metrics</div>')
        for gname, metrics in groups.items():
            parts.append(f'<div class="group"><h3>{html.escape(gname)}</h3><div class="cards">')
            parts.extend(_card(mk, env) for mk, env in metrics.items())
            parts.append("</div></div>")
        parts.append("</section>")
        out.append("".join(parts))
    return "".join(out)


def _window_grid(window: str, payload: dict) -> str:
    rc = payload.get("record_counts", {})
    live, sim = insights.count_live_sim(payload)
    return (
        f'<main class="grid main-grid" data-window="{html.escape(window)}">'
        f'{_pillars_html(payload.get("pillars", {}))}</main>'
        f'<span class="main-meta" data-window="{html.escape(window)}">'
        f'{live} live &middot; {sim} simulated &middot; '
        f'records: {html.escape(" · ".join(f"{k}:{v}" for k, v in rc.items()) or "—")}'
        f'</span>'
    )


def _render_multi(payloads: dict[str, dict]) -> str:
    """Render with Week/Month/Total segmented control."""
    default_payload = payloads.get("month") or next(iter(payloads.values()))
    ts = html.escape(default_payload.get("timestamp", ""))

    # segmented control buttons — order: Week, Month, Total
    btn_order = [("week", "Week"), ("month", "Month"), ("total", "Total")]
    btns = "".join(
        f'<button class="wc-btn" data-w="{w}">{label}</button>'
        for w, label in btn_order
        if w in payloads
    )
    segmented = f'<div class="wc">{btns}</div>' if btns else ""

    # per-window grids (default month, others hidden via JS)
    grids = "".join(
        _window_grid(w, payloads[w])
        for w, _ in btn_order
        if w in payloads
    )

    # per-platform breakdown from canonical (month) payload
    rc = default_payload.get("record_counts", {})
    per_platform = "".join(
        f'<details><summary>platform: {html.escape(p)} ({rc.get(p, 0)} records)</summary>'
        f'<div class="grid">{_pillars_html(view)}</div></details>'
        for p, view in default_payload.get("by_platform", {}).items()
    )

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Agentica OS - Governance Dashboard</title><style>{_CSS}</style></head><body>
<header>
  <h1>AGENTICA OS &middot; GOVERNANCE</h1>
  <span class="meta main-meta" data-window="week">Week window</span>
  <span class="meta main-meta" data-window="month">Month window</span>
  <span class="meta main-meta" data-window="total">All-time window</span>
  <span class="meta">{ts}</span>
  {segmented}
</header>
<h2 class="vlabel">Combined — all platforms</h2>
<p class="win-note">The Week / Month / Total filter scopes <b>telemetry</b> only — tokens, cost, latency, throughput, sessions. <b>Security &amp; governance</b> counts (CVEs, kill chains, violations, scorecard) are a <b>current snapshot</b>, and <b>estimate / "saved" / weekly</b> metrics reflect the <b>current week</b>. Those stay constant across windows by design — not a bug.</p>
{grids}
{per_platform}
{_JS}
</body></html>"""


def render_html(payload: dict) -> str:
    """Render dashboard HTML from a single payload or a multi-window dict.

    Multi-window: pass {"week": p7, "month": p30, "total": p_all}.
    Single payload (backward compat): treated as the month window.
    """
    if set(payload.keys()) & _WINDOW_KEYS and all(isinstance(v, dict) for v in payload.values()):
        return _render_multi(payload)

    # Single-payload path (backward compat)
    live, sim = insights.count_live_sim(payload)
    rc = payload.get("record_counts", {})
    rc_str = html.escape(" · ".join(f"{k}:{v}" for k, v in rc.items()) or "—")
    ts = html.escape(payload.get("timestamp", ""))

    combined = f'<h2 class="vlabel">Combined — all platforms</h2><main class="grid">{_pillars_html(payload.get("pillars", {}))}</main>'
    per_platform = "".join(
        f'<details><summary>platform: {html.escape(p)} ({rc.get(p, 0)} records)</summary>'
        f'<div class="grid">{_pillars_html(view)}</div></details>'
        for p, view in payload.get("by_platform", {}).items()
    )

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Agentica OS - Governance Dashboard</title><style>{_CSS}</style></head><body>
<header>
  <h1>AGENTICA OS &middot; GOVERNANCE</h1>
  <span class="meta">{live} live &middot; {sim} simulated</span>
  <span class="meta">records: {rc_str}</span>
  <span class="meta">{ts}</span>
</header>
{combined}
{per_platform}
</body></html>"""


def default_dashboard_path() -> Path:
    return _THIS.parents[2] / "Data" / "dashboard.html"


def write_dashboard(payload: dict, path: Path | None = None) -> Path:
    target = path or default_dashboard_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_html(payload), encoding="utf-8")
    return target


def main() -> int:
    from datetime import datetime, timezone

    from .aggregate import aggregate

    payload = aggregate(timestamp=datetime.now(timezone.utc).isoformat())
    path = write_dashboard(payload)
    live, sim = insights.count_live_sim(payload)
    print(f"Agentica Dashboard -> {path}  ({live} live / {sim} simulated)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
