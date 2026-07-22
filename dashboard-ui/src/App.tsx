import { useEffect, useState } from "react"
import { motion, AnimatePresence } from "motion/react"
import { X, Terminal, Info, Zap } from "lucide-react"
import { PILLARS, type PillarKey, type WIDPayload, type Reflex } from "@/types"
import logoImg from "@/assets/logo.png"
import {
  loadPayload, liveSimCounts, METRIC_DOCS, metricLabel, type FlatMetric,
} from "@/lib/data"
import {
  num, topMovers, platformMetricVal, reflexMetric, resolveHero, scoreMap, type ScoreScope,
} from "@/lib/metrics"
import {
  ScoreNumber, ScoreDelta, PillarStatus, TierMixMini, TrendMark, TrendChips, Sparkline,
} from "@/components/helpers"
import { tierColor } from "@/components/helper-values"
import { ReflexPanel, type ReflexProps } from "@/components/ReflexList"
import { RemediationPanel } from "@/components/RemediationPanel"
import { PillarPage } from "@/components/PillarPage"
import { useDojo, type DojoProps } from "@/hooks/useDojo"
import { DojoPanel } from "@/components/DojoPanel"
import { AsciiBackground } from "@/components/AsciiBackground"
import { SidebarParticles } from "@/components/SidebarParticles"
import { IconTorii, IconShuriken, IconKatana, IconFan, IconArmor, IconYinYang } from "@/components/SamuraiIcons"
import { LandingPage } from "@/components/LandingPage"

type GlyphKey = "overview" | "bow" | "sword" | "brush" | "arts" | "reports"
function NavIcon({ k, size, color }: { k: GlyphKey; size: number; color?: string }) {
  const props = { size, style: { flexShrink: 0, color } }
  if (k === "overview") return <IconTorii {...props} />
  if (k === "bow")      return <IconShuriken {...props} />
  if (k === "sword")    return <IconKatana {...props} />
  if (k === "brush")    return <IconFan {...props} />
  if (k === "arts")     return <IconArmor {...props} />
  if (k === "reports")  return <IconYinYang {...props} />
  return null
}

type View = "overview" | PillarKey | "reports"

interface RubricRow {
  name: string
  weight: number
  warn: number
  fail: number
  dir: "lower" | "higher"
  per?: string | null
  desc: string
}

/** Collect graded metrics (those carrying an effective rule) per pillar, heaviest first. */
function rubricFromPayload(pillars: WIDPayload["pillars"]): Record<PillarKey, RubricRow[]> {
  const out: Record<PillarKey, RubricRow[]> = { bow: [], sword: [], brush: [], arts: [] }
  for (const pk of Object.keys(out) as PillarKey[]) {
    for (const groups of Object.values(pillars[pk] ?? {})) {
      for (const [mk, env] of Object.entries(groups)) {
        if (!env.rule) continue
        out[pk].push({
          name: metricLabel(mk),
          weight: env.rule.weight, warn: env.rule.warn, fail: env.rule.fail,
          dir: env.rule.dir, per: env.rule.per,
          desc: METRIC_DOCS[mk]?.what ?? "",
        })
      }
    }
    out[pk].sort((a, b) => b.weight - a.weight || a.name.localeCompare(b.name))
  }
  return out
}

const DEMO_REPORTS = [
  {
    file: "w29.md",
    week: "2026-W29 (Current Week)",
    isCurrent: true,
    platform: "claude-code",
    title: "Weekly Governance Dispatch — 2026-W29",
    html: `
      <div style="font-family:var(--font-mono);font-size:12px;color:rgba(255,255,255,0.6);">WEEKLY GOVERNANCE DISPATCH · 2026-W29</div>
      <h2 style="margin:8px 0 16px;font-size:1.4rem;color:#4ade80;">STATUS: PASS · 100% Security Boundary Compliance</h2>

      <h3 style="font-size:1rem;color:var(--foreground);margin-top:20px;">Pillar Performance Summary</h3>
      <ul style="line-height:1.8;font-size:0.88rem;color:rgba(255,255,255,0.85);padding-left:20px;">
        <li><strong style="color:var(--sword);">Sword (Security):</strong> 14 ATT&CK kill chains intercepted & disrupted. 0 credential leaks.</li>
        <li><strong style="color:var(--bow);">Bow (Operations):</strong> 42.5 agent hours returned via unattended task completion (88.2% pass rate).</li>
        <li><strong style="color:var(--brush);">Brush (Architecture):</strong> $3,940 in spend savings achieved via local model routing & token optimization.</li>
        <li><strong style="color:var(--arts);">Arts (Craft):</strong> 18.5 human review hours saved through automated verifiers & doc parity checks.</li>
      </ul>

      <h3 style="font-size:1rem;color:var(--foreground);margin-top:20px;">Multi-Agent Telemetry</h3>
      <p style="font-size:0.85rem;color:rgba(255,255,255,0.7);line-height:1.6;">Active agent sessions monitored: 14 Claude Code, 6 Codex CLI, 23 Antigravity, 11 Cursor, 8 Local Ollama.</p>
    `
  },
  {
    file: "w28.md",
    week: "2026-W28 (Past Week)",
    isCurrent: false,
    platform: "claude-code",
    title: "Weekly Audit — 2026-W28",
    html: `
      <div style="font-family:var(--font-mono);font-size:12px;color:rgba(255,255,255,0.6);">WEEKLY GOVERNANCE DISPATCH · 2026-W28</div>
      <h2 style="margin:8px 0 16px;font-size:1.4rem;color:#facc15;">STATUS: HIGH · 2 Anomaly Warnings Resolved</h2>
      <p style="font-size:0.85rem;color:rgba(255,255,255,0.7);">Detailed historical telemetry logs and automated dojo trace for 2026-W28.</p>
    `
  },
  {
    file: "w27.md",
    week: "2026-W27 (Past Week)",
    isCurrent: false,
    platform: "codex-cli",
    title: "Weekly Audit — 2026-W27",
    html: `
      <div style="font-family:var(--font-mono);font-size:12px;color:rgba(255,255,255,0.6);">WEEKLY GOVERNANCE DISPATCH · 2026-W27</div>
      <h2 style="margin:8px 0 16px;font-size:1.4rem;color:#4ade80;">STATUS: PASS · 98.4% Pass Rate</h2>
      <p style="font-size:0.85rem;color:rgba(255,255,255,0.7);">Detailed historical telemetry logs and automated dojo trace for 2026-W27.</p>
    `
  }
]

function Reports({ payload }: { payload: WIDPayload }) {
  const [subTab, setSubTab] = useState<"logs" | "rubric">("logs")
  const [activeIdx, setActiveIdx] = useState(0)

  const ACCENT: Record<string, string> = { "claude-code": "var(--brush)", antigravity: "var(--bow)", "codex-cli": "var(--arts)" }
  const rubricData = rubricFromPayload(payload.pillars)

  const fmtT = (r: RubricRow, v: number) => {
    const n = v >= 10000 ? `${Math.round(v / 1000)}k` : `${+v.toFixed(2)}`
    return r.per === "session" ? `${n}/sess` : n
  }

  const weightColor = (w: number) => {
    if (w >= 3.0) return "var(--sword)"
    if (w >= 2.0) return "var(--bow)"
    return "var(--muted-foreground)"
  }

  const currentRep = DEMO_REPORTS[activeIdx] ?? DEMO_REPORTS[0]

  return (
    <section className="page-enter">
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 20,
        paddingBottom: 16, borderBottom: "1px solid rgba(255,255,255,0.055)" }}>
        <span style={{ fontSize: "1.6rem" }}>📜</span>
        <h2 style={{ fontSize: "1.4rem", margin: 0, letterSpacing: 2, textTransform: "uppercase" }}>Reports & Governance</h2>
        <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
          <button onClick={() => setSubTab("logs")} className="mono"
            style={{ padding: "6px 12px", borderRadius: 8, cursor: "pointer", fontSize: "var(--text-caption)", letterSpacing: 1, textTransform: "uppercase",
              background: subTab === "logs" ? "rgba(255,255,255,0.05)" : "transparent",
              border: `1px solid ${subTab === "logs" ? "var(--arts)" : "rgba(255,255,255,0.06)"}`,
              color: subTab === "logs" ? "var(--arts)" : "rgba(255,255,255,0.45)", transition: "0.2s" }}>
            Weekly Logs
          </button>
          <button onClick={() => setSubTab("rubric")} className="mono"
            style={{ padding: "6px 12px", borderRadius: 8, cursor: "pointer", fontSize: "var(--text-caption)", letterSpacing: 1, textTransform: "uppercase",
              background: subTab === "rubric" ? "rgba(255,255,255,0.05)" : "transparent",
              border: `1px solid ${subTab === "rubric" ? "var(--arts)" : "rgba(255,255,255,0.06)"}`,
              color: subTab === "rubric" ? "var(--arts)" : "rgba(255,255,255,0.45)", transition: "0.2s" }}>
            Scoring Rubric
          </button>
        </div>
      </div>

      {subTab === "logs" ? (
        (() => {
          const isDemo = typeof window !== "undefined" && (window.location.search.includes("demo") || window.location.hash.includes("demo") || window.location.pathname.includes("demo"))
          if (!isDemo && payload?.window?.records === 0) {
            return (
              <div className="glass" style={{ borderRadius: 18, padding: 36, textAlign: "center", border: "1px dashed rgba(255,255,255,0.15)", background: "rgba(255,255,255,0.01)" }}>
                <h3 className="mono" style={{ margin: "0 0 10px", fontSize: "0.95rem", color: "var(--foreground)", letterSpacing: 1 }}>No Weekly Reports Generated Yet</h3>
                <p className="mono" style={{ margin: "0 auto", fontSize: "0.75rem", color: "var(--muted-foreground)", lineHeight: 1.6, maxWidth: 520 }}>
                  Weekly dispatches are generated automatically as agent sessions complete and local telemetry is aggregated.
                </p>
              </div>
            )
          }
          return (
            <div style={{ display: "flex", gap: 20, alignItems: "flex-start", flexWrap: "wrap" }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 220 }}>
                {DEMO_REPORTS.map((r, idx) => {
                  const on = activeIdx === idx
                  return (
                    <button key={r.file} onClick={() => setActiveIdx(idx)} className="mono"
                      style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", borderRadius: 8, cursor: "pointer", textAlign: "left", width: "100%",
                        background: on ? "rgba(255,255,255,0.05)" : "rgba(255,255,255,0.01)",
                        border: `1px solid ${on ? "rgba(255,255,255,0.12)" : "rgba(255,255,255,0.04)"}`,
                        borderLeft: `3px solid ${on ? (ACCENT[r.platform] ?? "var(--arts)") : "transparent"}`,
                        color: on ? (ACCENT[r.platform] ?? "var(--arts)") : "rgba(255,255,255,0.6)", fontSize: "var(--text-caption)" }}>
                      <span>{r.week}</span>
                      {!r.isCurrent && <span style={{ marginLeft: "auto", fontSize: 10 }}>🔒</span>}
                    </button>
                  )
                })}
              </div>

              <div style={{ flex: 1, minWidth: 360, position: "relative", borderRadius: 18, overflow: "hidden" }}>
                <div className="glass report-md" style={{
                  borderRadius: 18, padding: "24px 30px",
                  borderLeft: `3px solid ${ACCENT[currentRep.platform] ?? "var(--arts)"}`,
                  ...(currentRep.isCurrent ? {} : { filter: "blur(6px)", opacity: 0.4, pointerEvents: "none", userSelect: "none" })
                }} dangerouslySetInnerHTML={{ __html: currentRep.html }} />

                {!currentRep.isCurrent && (
                  <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 10, background: "rgba(5,5,5,0.55)", backdropFilter: "blur(4px)" }}>
                    <div style={{ fontSize: 24 }}>🔒</div>
                    <div className="mono" style={{ fontSize: 12, letterSpacing: 2, color: "#facc15", fontWeight: 700 }}>PAST WEEKLY AUDIT REPORTS · PRO</div>
                    <div className="mono" style={{ fontSize: 11, color: "rgba(255,255,255,0.6)" }}>Current week report is included in Free. Historical archives ship with Pro.</div>
                  </div>
                )}
              </div>
            </div>
          )
        })()
      ) : (
        <div className="page-enter" style={{ display: "flex", flexDirection: "column", gap: 24 }}>
          {/* Header Explanation — mirrors insights._health() + insights.annotate() */}
          <div className="glass" style={{ borderRadius: 18, padding: 24, borderLeft: "3px solid var(--arts)", background: "rgba(255,255,255,0.01)" }}>
            <h3 className="mono" style={{ margin: "0 0 10px", fontSize: "0.85rem", letterSpacing: 1.5, textTransform: "uppercase", color: "var(--foreground)" }}>How Scoring Works — Thresholds → Health → Pillar Status</h3>
            <p className="mono" style={{ margin: 0, fontSize: "0.68rem", lineHeight: 1.7, color: "var(--muted-foreground)" }}>
              <strong style={{ color: "var(--foreground)" }}>1 · Tiers.</strong>{" "}
              Each graded metric has a <strong style={{ color: "var(--foreground)" }}>warn</strong> and <strong style={{ color: "var(--foreground)" }}>fail</strong> threshold.
              Crossing warn raises a <span style={{ color: "var(--bow)" }}>HIGH</span> reflex; crossing fail escalates to <span style={{ color: "var(--sword)" }}>CRITICAL</span> and queues the metric's remediation skill.
              <br />
              <strong style={{ color: "var(--foreground)" }}>2 · Health curve.</strong>{" "}
              The same thresholds map each value onto a continuous 0–100 health: <span style={{ color: "var(--brush)" }}>100</span> at or inside warn,
              sliding down to <span style={{ color: "var(--brush)" }}>40</span> at the fail threshold, then decaying toward <span style={{ color: "var(--brush)" }}>0</span> the further it runs past fail.
              Per-session metrics are normalized by session count first.
              <br />
              <strong style={{ color: "var(--foreground)" }}>3 · Pillar status.</strong>{" "}
              A pillar's <strong style={{ color: "var(--foreground)" }}>status</strong> is its worst metric tier — one CRITICAL metric marks the whole pillar CRITICAL, shown as a chip with the passing fraction (e.g. 9/11).
              No averaging can hide a hard failure. There is no blended pillar score: the weighted-mean drift index was retired 2026-07-19 —
              slow broad degradation is caught per-metric by trajectory early-warnings and σ-anomaly reflexes instead.
              Any metric below 60 health is flagged on the pillar card. Simulated and informational metrics never contribute.
              Weights are priority hints (sort order in Needs Attention), never multipliers.
              <br />
              <strong style={{ color: "var(--foreground)" }}>4 · Calibration.</strong>{" "}
              Thresholds below are the <em>effective</em> values: data-derived calibration (thresholds.json, recalibrated from trailing history) overrides the hand-set defaults where available.
            </p>
            <div style={{ marginTop: 14, display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
              <div style={{ padding: "8px 12px", background: "rgba(0,0,0,0.3)", borderRadius: 8, border: "1px solid rgba(255,255,255,0.05)" }}>
                <code className="mono" style={{ fontSize: "0.68rem", color: "var(--brush)" }}>
                  Pillar Status = worst(metric tiers) · passing/graded
                </code>
              </div>
              {[
                { label: "PASS", color: "rgba(255,255,255,0.25)", bg: "rgba(255,255,255,0.04)", desc: "health 100" },
                { label: "HIGH", color: "var(--bow)", bg: "rgba(250,204,21,0.08)", desc: "health 99–40" },
                { label: "CRITICAL", color: "var(--sword)", bg: "rgba(239,68,68,0.08)", desc: "health ≤ 40 → reflex fires" },
              ].map((t) => (
                <div key={t.label} style={{ padding: "5px 10px", borderRadius: 6, background: t.bg, border: `1px solid ${t.color}33`, display: "flex", alignItems: "center", gap: 7 }}>
                  <span className="mono" style={{ fontSize: "var(--text-caption)", color: t.color, fontWeight: 700, letterSpacing: 1 }}>{t.label}</span>
                  <span className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)" }}>{t.desc}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Pillars Grid */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))", gap: 20 }}>
            {/* Bow */}
            <div className="glass-bow-card" style={{ padding: 20, borderRadius: 20, display: "flex", flexDirection: "column", gap: 14, background: "rgba(90, 70, 5, 0.04)", border: "1px solid rgba(250, 204, 21, 0.15)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, borderBottom: "1px solid rgba(250,204,21,0.15)", paddingBottom: 10 }}>
                <IconShuriken size={20} style={{ color: "var(--bow)" }} />
                <h4 className="mono" style={{ fontSize: "0.75rem", margin: 0, letterSpacing: 1, color: "var(--bow)" }}>Way of the Bow (Operations)</h4>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {rubricData.bow.map((m) => (
                  <div key={m.name} style={{ display: "flex", flexDirection: "column", gap: 3, borderBottom: "1px solid rgba(255,255,255,0.03)", paddingBottom: 6 }}>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                      <span className="mono" style={{ fontSize: "0.7rem", fontWeight: 600 }}>{m.name}</span>
                      <span className="mono" style={{ fontSize: "var(--text-caption)", color: weightColor(m.weight), background: "rgba(0,0,0,0.25)", padding: "1px 5px", borderRadius: 4, whiteSpace: "nowrap" }}>
                        {m.dir === "lower" ? "↓" : "↑"} warn:{fmtT(m, m.warn)} · fail:{fmtT(m, m.fail)} · w{m.weight.toFixed(0)}
                      </span>
                    </div>
                    <span style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)", lineHeight: 1.4 }}>{m.desc}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Sword */}
            <div className="glass-sword-card" style={{ padding: 20, borderRadius: 20, display: "flex", flexDirection: "column", gap: 14, background: "rgba(80, 10, 10, 0.04)", border: "1px solid rgba(239, 68, 68, 0.15)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, borderBottom: "1px solid rgba(239,68,68,0.15)", paddingBottom: 10 }}>
                <IconKatana size={20} style={{ color: "var(--sword)" }} />
                <h4 className="mono" style={{ fontSize: "0.75rem", margin: 0, letterSpacing: 1, color: "var(--sword)" }}>Way of the Sword (Security)</h4>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {rubricData.sword.map((m) => (
                  <div key={m.name} style={{ display: "flex", flexDirection: "column", gap: 3, borderBottom: "1px solid rgba(255,255,255,0.03)", paddingBottom: 6 }}>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                      <span className="mono" style={{ fontSize: "0.7rem", fontWeight: 600 }}>{m.name}</span>
                      <span className="mono" style={{ fontSize: "var(--text-caption)", color: weightColor(m.weight), background: "rgba(0,0,0,0.25)", padding: "1px 5px", borderRadius: 4, whiteSpace: "nowrap" }}>
                        {m.dir === "lower" ? "↓" : "↑"} warn:{fmtT(m, m.warn)} · fail:{fmtT(m, m.fail)} · w{m.weight.toFixed(0)}
                      </span>
                    </div>
                    <span style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)", lineHeight: 1.4 }}>{m.desc}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Brush */}
            <div className="glass-brush-card" style={{ padding: 20, borderRadius: 20, display: "flex", flexDirection: "column", gap: 14, background: "rgba(75, 10, 48, 0.04)", border: "1px solid rgba(244, 114, 182, 0.15)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, borderBottom: "1px solid rgba(244,114,182,0.15)", paddingBottom: 10 }}>
                <IconFan size={20} style={{ color: "var(--brush)" }} />
                <h4 className="mono" style={{ fontSize: "0.75rem", margin: 0, letterSpacing: 1, color: "var(--brush)" }}>Way of the Brush (Architecture)</h4>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {rubricData.brush.map((m) => (
                  <div key={m.name} style={{ display: "flex", flexDirection: "column", gap: 3, borderBottom: "1px solid rgba(255,255,255,0.03)", paddingBottom: 6 }}>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                      <span className="mono" style={{ fontSize: "0.7rem", fontWeight: 600 }}>{m.name}</span>
                      <span className="mono" style={{ fontSize: "var(--text-caption)", color: weightColor(m.weight), background: "rgba(0,0,0,0.25)", padding: "1px 5px", borderRadius: 4, whiteSpace: "nowrap" }}>
                        {m.dir === "lower" ? "↓" : "↑"} warn:{fmtT(m, m.warn)} · fail:{fmtT(m, m.fail)} · w{m.weight.toFixed(0)}
                      </span>
                    </div>
                    <span style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)", lineHeight: 1.4 }}>{m.desc}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Arts */}
            <div className="glass-arts-card" style={{ padding: 20, borderRadius: 20, display: "flex", flexDirection: "column", gap: 14, background: "rgba(38, 38, 38, 0.06)", border: "1px solid rgba(255, 255, 255, 0.08)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, borderBottom: "1px solid rgba(255,255,255,0.1)", paddingBottom: 10 }}>
                <IconArmor size={20} style={{ color: "var(--arts)" }} />
                <h4 className="mono" style={{ fontSize: "0.75rem", margin: 0, letterSpacing: 1, color: "var(--arts)" }}>Way of the Arts (Craft)</h4>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {rubricData.arts.map((m) => (
                  <div key={m.name} style={{ display: "flex", flexDirection: "column", gap: 3, borderBottom: "1px solid rgba(255,255,255,0.03)", paddingBottom: 6 }}>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                      <span className="mono" style={{ fontSize: "0.7rem", fontWeight: 600 }}>{m.name}</span>
                      <span className="mono" style={{ fontSize: "var(--text-caption)", color: weightColor(m.weight), background: "rgba(0,0,0,0.25)", padding: "1px 5px", borderRadius: 4, whiteSpace: "nowrap" }}>
                        {m.dir === "lower" ? "↓" : "↑"} warn:{fmtT(m, m.warn)} · fail:{fmtT(m, m.fail)} · w{m.weight.toFixed(0)}
                      </span>
                    </div>
                    <span style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)", lineHeight: 1.4 }}>{m.desc}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Composite & Derived Metrics — the headline numbers and where they come from */}
          <div className="glass" style={{ borderRadius: 18, padding: 24, borderLeft: "3px solid var(--brush)", background: "rgba(255,255,255,0.01)" }}>
            <h3 className="mono" style={{ margin: "0 0 10px", fontSize: "0.85rem", letterSpacing: 1.5, textTransform: "uppercase", color: "var(--foreground)" }}>Composite & Headline Metrics</h3>
            <p className="mono" style={{ margin: "0 0 14px", fontSize: "0.68rem", lineHeight: 1.7, color: "var(--muted-foreground)" }}>
              The hero numbers on the Overview cards (Est. Agent Time Saved, Kill Chains Disrupted, …) are
              <strong style={{ color: "var(--foreground)" }}> computed independently by scouts — they do NOT feed the pillar status</strong>.
              Pillar status is only the worst-tier rollup of the graded metrics above (no blended score exists). Composites below are themselves
              single metrics: some graded (Architecture Grade, Vault Health), some informational (Subagent ROI, the Est. savings trio).
            </p>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 10 }}>
              {[
                { n: "Est. Agent Hours Saved (Bow hero, calibration-gated)", f: "Completed autonomous backlog items, each timed started→completed and weighted by task-kind coefficients. Until 20 real samples calibrate it, the hero slot falls back to Complexity-Weighted Throughput (real data)." },
                { n: "Cost Savings (Brush hero, measured)", f: "Cost-per-task improvement vs last week × this week's task volume — real spend telemetry. A raw spend drop is NOT counted (less work ≠ savings). The old estimated routing-savings component ($0.05/run, no sample source) was removed; this is now a measured metric. Falls back to Cost per Task only if there's no prior-week baseline." },
                { n: "Craft Improvements (Arts hero, measured)", f: "Real count of discrete craft wins this week — skill promotions plus completed arts backlog items. Replaced the former synthetic 'Est. Human Hours Saved' (real deltas × hours coefficients with no sample source). Vibe-alignment and doc-parity deltas appear in the breakdown and as their own metrics. Falls back to Knowledge Vault Health only if the source is unavailable." },
                { n: "Subagent ROI Index (graded, w2)", f: "100 × (successful sessions ÷ subagent spawns) × cost penalty. Penalty kicks in when avg cost per spawn exceeds the $0.10 benchmark. Capped at 100." },
                { n: "Architecture Grade (graded, w3)", f: "Weighted category rubric from architecture_scorecard.json — structure, boundaries, doc parity, hygiene categories each scored and weight-averaged." },
                { n: "Security Posture Score (graded, w2)", f: "Weighted checklist across scanning, supply chain, skill vetting, and PII handling." },
                { n: "Knowledge Vault Health (graded, w2)", f: "100 minus penalties for pending raw notes, orphaned articles, stale articles, and empty topic domains." },
                { n: "Metric Coverage (informational)", f: "Graded-and-live metrics ÷ total gradeable, per pillar. Low coverage = the pillar status rests on thin data." },
              ].map((c) => (
                <div key={c.n} style={{ padding: "10px 12px", borderRadius: 10, background: "rgba(0,0,0,0.2)", border: "1px solid rgba(255,255,255,0.05)" }}>
                  <div className="mono" style={{ fontSize: "0.66rem", fontWeight: 700, marginBottom: 4, color: "var(--brush)" }}>{c.n}</div>
                  <div style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)", lineHeight: 1.5 }}>{c.f}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </section>
  )
}

// ── Horizontal labeled bars for metric contribution attribution ───────────────
function ContributionBars({ data, color }: { data: { project: string; value: number }[]; color: string }) {
  if (!data.length) {
    return <span style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)" }}>not attributable</span>
  }
  const max = Math.max(...data.map((d) => Math.abs(d.value)), 1)
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {data.map((d) => (
        <div key={d.project} style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)", width: 78, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", textAlign: "right" }} title={d.project}>{d.project}</span>
          <div style={{ flex: 1, height: 12, background: "rgba(255,255,255,0.05)", borderRadius: 3 }}>
            <motion.div initial={{ width: 0 }} animate={{ width: `${(Math.abs(d.value) / max) * 100}%` }}
              transition={{ duration: 0.6, ease: "easeOut" }}
              style={{ height: "100%", background: color, borderRadius: 3, boxShadow: `0 0 10px ${color}bb` }} />
          </div>
          <span className="mono" style={{ fontSize: "var(--text-caption)", color, width: 56, textAlign: "right" }}>{d.value.toLocaleString()}</span>
        </div>
      ))}
    </div>
  )
}

// ── Metric detail modal ───────────────────────────────────────────────────────
interface MetricModalProps {
  metric: FlatMetric
  color: string
  contributions: { project: string; value: number }[]
  scopeLabel: string
  onClose: () => void
  dojoProps?: DojoProps
}

function MetricModal({ metric, color, contributions, scopeLabel, onClose, dojoProps }: MetricModalProps) {
  const env = metric.env
  const doc = METRIC_DOCS[metric.key]

  return (
    <div
      style={{ position: "fixed", inset: 0, background: "rgba(5,5,5,0.82)", backdropFilter: "blur(8px)", zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center", padding: 24 }}
      onClick={onClose}
    >
      <motion.div
        initial={{ scale: 0.94, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.94, opacity: 0 }}
        transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
        onClick={(e) => e.stopPropagation()}
        className="glass"
        style={{ borderRadius: 28, padding: 32, width: "100%", maxWidth: 620, position: "relative",
          border: `1px solid ${color}22`, borderTop: `3px solid ${color}66` }}
      >
        <button
          onClick={onClose}
          style={{ position: "absolute", top: 20, right: 20, background: "none", border: "none", cursor: "pointer", color: "var(--muted-foreground)", padding: 6, borderRadius: "50%" }}
          onMouseEnter={(e) => (e.currentTarget.style.color = "white")}
          onMouseLeave={(e) => (e.currentTarget.style.color = "var(--muted-foreground)")}
        >
          <X size={18} />
        </button>

        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 20 }}>
          <Info size={20} style={{ color }} />
          <span className="mono" style={{ fontSize: "0.75rem", fontWeight: 700, letterSpacing: 2, textTransform: "uppercase", color }}>Telemetry Inspector</span>
        </div>

        <div style={{ background: "rgba(0,0,0,0.35)", border: "1px solid rgba(255,255,255,0.06)", borderRadius: 16, padding: "18px 20px", marginBottom: 18 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
            <span style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)", textTransform: "uppercase", letterSpacing: 1 }}>{metric.group}</span>
            <span className="mono" style={{ marginLeft: "auto", fontSize: "var(--text-caption)", fontWeight: 700, color: tierColor(env.tier), border: `1px solid ${tierColor(env.tier)}44`, padding: "1px 6px", borderRadius: 4 }}>{env.tier}</span>
          </div>
          <div style={{ fontSize: "1.05rem", fontWeight: 600, marginBottom: doc ? 6 : 10 }}>{metricLabel(metric.key)}</div>
          {doc?.what && (
            <p style={{ fontSize: "0.7rem", color: "var(--muted-foreground)", lineHeight: 1.5, margin: "0 0 10px" }}>{doc.what}</p>
          )}
          <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
            <span className="mono" style={{ fontSize: "2rem", fontWeight: 700, color }}>{env.is_simulated ? "—" : env.val}</span>
            {env.delta && !env.is_simulated && (
              <span style={{ display: "flex", alignItems: "center", gap: 4, fontSize: "0.75rem", color: "var(--muted-foreground)" }}>
                <TrendMark trend={env.trend} />
                {env.delta}
              </span>
            )}
          </div>
        </div>

        {!env.is_simulated && (
          <div style={{ display: "flex", gap: 16, marginBottom: 18, flexWrap: "wrap" }}>
            <div style={{ flex: "1 1 200px", minWidth: 180 }}>
              <div style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)", textTransform: "uppercase", letterSpacing: 1, marginBottom: 6 }}>History</div>
              <div style={{ padding: "10px 0" }}>
                {env.history && env.history.length >= 2
                  ? <Sparkline history={env.history} color={color} />
                  : <span style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)" }}>no trend yet</span>}
              </div>
            </div>
            <div style={{ flex: "1 1 220px", minWidth: 200 }}>
              <div style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)", textTransform: "uppercase", letterSpacing: 1, marginBottom: 6 }}>{scopeLabel}</div>
              <div style={{ padding: "10px 0" }}>
                <ContributionBars data={contributions} color={color} />
              </div>
            </div>
          </div>
        )}

        {env.mitigation_command && (() => {
          const urgent = !!env.flagged
          const accent = urgent ? "var(--sword)" : "var(--muted-foreground)"
          return (
            <div>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                <Terminal size={14} style={{ color: accent }} />
                <span style={{ fontSize: "0.65rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: 1, color: accent }}>
                  {urgent ? "Needs attention. Run this skill." : "Suggested skill"}
                </span>
                {env.mitigation_skill && (
                  <span className="mono" style={{ marginLeft: "auto", fontSize: "var(--text-caption)", background: urgent ? "rgba(239,68,68,0.12)" : "rgba(255,255,255,0.06)", color: accent, padding: "2px 7px", borderRadius: 4 }}>{env.mitigation_skill}</span>
                )}
              </div>
              <p style={{ fontSize: "0.66rem", color: "var(--muted-foreground)", lineHeight: 1.5, margin: "0 0 8px" }}>
                {urgent && <span style={{ color: "var(--sword)" }}>Past safe range. </span>}
                {doc?.fix ?? (urgent ? "Copy the command and run the skill." : "Run this skill to dig into or improve this metric.")}
              </p>
              {(() => {
                const isActiveExec = dojoProps?.execCommand != null && dojoProps.execCommand === env.mitigation_command
                const execRunning  = isActiveExec && dojoProps?.execStatus === 'running'
                const execDone     = isActiveExec && dojoProps?.execStatus === 'done'
                const execError    = isActiveExec && dojoProps?.execStatus === 'error'
                return (
                  <div style={{ background: "rgba(0,0,0,0.4)", border: "1px solid rgba(255,255,255,0.06)", borderRadius: 12, padding: "12px 14px", display: "flex", alignItems: "center", gap: 10 }}>
                    <code className="mono" style={{ fontSize: "0.7rem", color: "rgba(255,255,255,0.85)", flex: 1, wordBreak: "break-all" }}>{env.mitigation_command}</code>
                    <button
                      disabled={execRunning || !dojoProps}
                      onClick={() => { if (!execRunning) dojoProps?.exec(env.mitigation_command!) }}
                      className="mono"
                      style={{ background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 8, padding: "7px 10px", cursor: execRunning || !dojoProps ? "not-allowed" : "pointer", color: execDone ? "#4ade80" : execError ? "#ef4444" : "var(--muted-foreground)", flexShrink: 0, fontSize: "0.65rem", opacity: !dojoProps ? 0.4 : 1 }}
                    >
                      {execRunning ? "⏳" : execDone ? "✓" : execError ? "✗" : "⚡ Run"}
                    </button>
                  </div>
                )
              })()}
            </div>
          )
        })()}

        <button onClick={onClose} className="mono"
          style={{ marginTop: 18, width: "100%", fontSize: "0.65rem", letterSpacing: 1, textTransform: "uppercase",
            color, background: `${color}0a`, border: `1px solid ${color}30`, borderRadius: 10, padding: "9px 0", cursor: "pointer" }}>
          Close
        </button>
      </motion.div>
    </div>
  )
}

// ── App root ─────────────────────────────────────────────────────────────────
export default function App() {
  const [mode, setMode] = useState<"landing" | "dashboard">(() => {
    if (typeof window !== "undefined") {
      const q = window.location.search.toLowerCase()
      const h = window.location.hash.toLowerCase()
      const p = window.location.pathname.toLowerCase()
      if (q.includes("landing") || h.includes("landing") || p.includes("landing")) {
        return "landing"
      }
    }
    return "dashboard"
  })
  const [payload, setPayload] = useState<WIDPayload | null>(null)
  const [loadedAt, setLoadedAt] = useState<number | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [view, setView] = useState<View>("overview")
  const [selected, setSelected] = useState<{ metric: FlatMetric; color: string } | null>(null)
  const [dismissed, setDismissed] = useState<Set<string>>(new Set())
  const dojoProps = useDojo()
  const isDemo = typeof window !== "undefined" && (
    window.location.search.toLowerCase().includes("demo") ||
    window.location.hash.toLowerCase().includes("demo") ||
    window.location.pathname.toLowerCase().includes("demo")
  )

  useEffect(() => {
    const load = () => loadPayload().then((p) => { setPayload(p); setLoadedAt(Date.now()) }).catch((e) => setErr(String(e)))
    load()
    const id = setInterval(load, 60_000)
    return () => clearInterval(id)
  }, [])

  // Re-fetch after a skill exec completes — refresh_dashboard.py runs in ~3s,
  // so wait 4s before polling so the new wid_payload.json is ready.
  useEffect(() => {
    if (dojoProps.execStatus !== 'done') return
    const t = setTimeout(() => {
      loadPayload().then((p) => { setPayload(p); setLoadedAt(Date.now()) }).catch(() => {})
    }, 4000)
    return () => clearTimeout(t)
  }, [dojoProps.execStatus])

  if (mode === "landing") {
    return <LandingPage onOpenDashboard={() => setMode("dashboard")} />
  }

  if (err) return <div className="mono" style={{ color: "var(--sword)", padding: 40 }}>Failed to load wid_payload.json: {err}</div>
  if (!payload) return (
    <div style={{ display: "flex", minHeight: "100vh", background: "var(--background)" }}>
      <aside className="glass" style={{ width: 230 }} />
      <main style={{ flex: 1, padding: "2rem 2.5rem" }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))", gap: 18 }}>
          {(["sword", "bow", "brush", "arts"] as const).map((pk) => (
            <div key={pk} className={`glass-${pk}-card metric-graded`}
              style={{ borderRadius: 24, padding: "26px 28px", height: 220 }} />
          ))}
        </div>
      </main>
    </div>
  )

  const dismiss = (id: string) => setDismissed((prev) => new Set(prev).add(id))
  const openReflex = (r: Reflex) => { const hit = reflexMetric(payload, r); if (hit) setSelected(hit) }
  const stuckReflexIds = new Set(
    (payload.remediation_efficacy?.stuck_remediations ?? []).map(s => s.reflex_id)
  )
  const reflexProps = { dismissed, onDismiss: dismiss, onSelect: openReflex, dojoProps, stuckReflexIds, lastAutoRemediationPillar: dojoProps.lastAutoRemediationPillar }

  // Payload freshness: age at the moment we fetched it (refreshed by the 60s
  // poll — keeps render pure, no Date.now() during render)
  const payloadAgeMin = (() => {
    if (loadedAt == null) return null
    const t = new Date(payload.timestamp)
    if (Number.isNaN(t.getTime())) return null
    return Math.round((loadedAt - t.getTime()) / 60_000)
  })()

  const { live, sim } = liveSimCounts(payload.pillars)
  const reflexCrit = (payload.reflexes ?? []).filter((r) => r.tier === "CRITICAL" && !dismissed.has(r.id)).length

  let modalContributions: { project: string; value: number }[] = []
  let modalScopeLabel = "By project"
  if (selected) {
    const key = selected.metric.key
    const proj = Object.entries(payload.by_project)
      .filter(([, i]) => i.has_data && i.metrics && i.metrics[key] != null)
      .map(([name, i]) => ({ project: name, value: i.metrics![key] }))
      .sort((a, b) => Math.abs(b.value) - Math.abs(a.value))
    const eco = proj.length ? [] : Object.entries(payload.by_platform)
      .map(([name, pillars]) => ({ project: name, value: platformMetricVal(pillars, key) }))
      .filter((d) => d.value !== 0)
      .sort((a, b) => Math.abs(b.value) - Math.abs(a.value))
    const sysVal = num(selected.metric.env.val)
    const sys = (proj.length || eco.length || selected.metric.env.is_simulated || !sysVal)
      ? [] : [{ project: "all systems", value: sysVal }]
    modalContributions = proj.length ? proj : eco.length ? eco : sys
    if (proj.length) modalScopeLabel = "By project"
    else if (eco.length) modalScopeLabel = "By ecosystem"
    else if (sys.length) modalScopeLabel = "System-wide"
    else modalScopeLabel = "By project"
  }

  const navItems: { key: View; label: string; glyph: string; accent: string }[] = [
    { key: "overview", label: "Bushido Overview", glyph: "☯", accent: "var(--overview)" },
    ...PILLARS.map((p) => ({ key: p.key as View, label: p.label, glyph: p.glyph, accent: p.accent })),
    { key: "reports", label: "Reports", glyph: "📜", accent: "var(--arts)" },
  ]

  const HARNESS_CONFIG: { key: string; label: string; unit: string; color: string }[] = [
    { key: "claude", label: "Claude Code", unit: "sessions", color: "var(--brush)" },
    { key: "codex", label: "Codex CLI", unit: "tasks", color: "var(--arts)" },
    { key: "cursor", label: "Cursor", unit: "sessions", color: "#38bdf8" },
    { key: "antigravity", label: "Antigravity", unit: "runs", color: "var(--bow)" },
    { key: "local", label: "Local Ollama", unit: "sessions", color: "#4ade80" },
  ]

  return (
    <div style={{ display: "flex", flexDirection: "column", minHeight: "100vh", background: "var(--background)", color: "var(--foreground)" }}>
      {/* FREE DEMO Banner — only rendered in hosted demo mode */}
      {isDemo && (
        <div style={{
          display: "flex", alignItems: "center", gap: 16, padding: "10px 24px",
          background: "rgba(239,68,68,0.08)", borderBottom: "1px solid rgba(239,68,68,0.3)",
          fontFamily: "var(--font-mono)", fontSize: 11, letterSpacing: 1, zIndex: 10
        }}>
          <span style={{ color: "#ef4444", fontWeight: 700 }}>INTERACTIVE DEMO</span>
          <span style={{ color: "rgba(255,255,255,0.5)" }}>Sample Telemetry Preview</span>
          <div style={{ marginLeft: "auto", display: "flex", gap: 20 }}>
            <a href="../" style={{ color: "rgba(255,255,255,0.6)", textDecoration: "none", fontFamily: "inherit" }}>
              ← Back to site
            </a>
            <a href="https://jemakaib1.gumroad.com/l/sqwomh" target="_blank" rel="noopener noreferrer" style={{ color: "#facc15", textDecoration: "none", fontWeight: 600, fontFamily: "inherit" }}>
              Unlock Pro Lifetime — $199 →
            </a>
          </div>
        </div>
      )}

      <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
        <AsciiBackground view={view} />
        <aside className={`glass nav-slide sidebar-glow-${view}`} style={{ width: 230, padding: "1.5rem 1rem", display: "flex", flexDirection: "column", gap: 8, borderRadius: 0, position: "relative", overflow: "hidden",
            borderTop: `2px solid ${navItems.find(n => n.key === view)?.accent ?? "var(--sword)"}`,
            transition: "border-top-color 0.4s ease, box-shadow 0.4s ease" }}>
        <SidebarParticles key={view} pillar={view} />
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 20, position: "relative", zIndex: 1,
          paddingBottom: 14, borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
          <img src={logoImg} alt="Order Samurai Logo" style={{ height: 52, width: "auto", objectFit: "contain", borderRadius: 6, background: "#000" }} />
          <span className="mono" style={{ fontSize: "0.85rem", letterSpacing: 2, color: "var(--sword)", fontWeight: 700,
            textShadow: "0 0 22px var(--sword)55" }}>ORDER&nbsp;SAMURAI</span>
        </div>
        {navItems.map((p) => {
          const on = p.key === view
          return (
            <button key={p.key} onClick={() => setView(p.key)} className={on ? undefined : "nav-btn-inactive"}
              style={{
                display: "flex", alignItems: "center", gap: 12, padding: "0.85rem 1rem", borderRadius: 16, cursor: "pointer",
                textTransform: "uppercase", letterSpacing: 1.5, fontSize: "0.8rem", textAlign: "left", transition: "0.3s",
                background: on ? "rgba(255,255,255,0.04)" : "transparent",
                color: on ? p.accent : "var(--muted-foreground)",
                border: `1px solid ${on ? `${p.accent}55` : "transparent"}`,
                boxShadow: on ? `0 0 18px ${p.accent}30, inset 0 0 14px ${p.accent}0c` : "none",
              }}>
              <NavIcon k={p.key as GlyphKey} size={18} /> {p.label}
            </button>
          )
        })}
        <div style={{ marginTop: "auto", fontSize: "0.65rem", color: "var(--muted-foreground)", lineHeight: 1.6,
          borderTop: "1px solid rgba(255,255,255,0.06)", paddingTop: 14 }}>
          {reflexCrit > 0 && (
            <button onClick={() => setView("overview")} className="mono"
              style={{ display: "flex", alignItems: "center", gap: 6, color: "#ef4444", background: "rgba(239,68,68,0.10)", border: "1px solid #ef4444", borderRadius: 8, padding: "4px 8px", marginBottom: 8, cursor: "pointer", width: "100%" }}>
              <Zap size={12} /> {reflexCrit} CRITICAL REFLEX{reflexCrit > 1 ? "ES" : ""}
            </button>
          )}
          {(payload.window?.records ?? 0) === 0 ? (
            <>
              <div className="mono" style={{ color: "var(--muted-foreground)" }}>0 LIVE</div>
              <div className="mono" style={{ color: "rgba(255,255,255,0.4)" }}>COLD START</div>
            </>
          ) : (
            <>
              <div className="mono" style={{ color: "var(--bow)" }}>{live} LIVE</div>
              <div className="mono">{sim} SIMULATED</div>
            </>
          )}
          <div className="mono" style={{ marginTop: 6, color: dojoProps.connected ? "rgba(34,197,94,0.8)" : "rgba(255,255,255,0.2)" }}>
            {dojoProps.connected
              ? <><span className="status-dot-live">●</span>{" DOJO ONLINE"}</>
              : "○ DOJO OFFLINE"}
          </div>
          <a href="mailto:support@agentica.biz" className="mono" style={{ display: "block", marginTop: 10, fontSize: "0.6rem", color: "rgba(255,255,255,0.4)", textDecoration: "none" }}>
            🐛 Bug Report: support@agentica.biz
          </a>
        </div>
      </aside>

      <main className="scroll-fade-bottom" style={{ flex: 1, padding: "2rem 2.5rem", overflow: "auto", minWidth: 0 }}>
        {/* Stale-data banner — refresh runs every ~15 min, so >2h means the refresh
            pipeline is down. A corner badge alone was missable: the 2026-06-14 outage
            left the dashboard silently showing 4-day-old data. This makes it unmissable. */}
        {payloadAgeMin != null && payloadAgeMin > 120 && (
          <div className="mono" role="alert" style={{
            marginBottom: 16, padding: "11px 16px", borderRadius: 8,
            border: "1px solid var(--sword)", background: "rgba(239,68,68,0.09)",
            color: "var(--sword)", fontSize: "0.72rem", display: "flex",
            alignItems: "center", gap: 12, flexWrap: "wrap",
          }}>
            <span style={{ fontWeight: 700, letterSpacing: 1.5 }}>⚠ STALE DATA</span>
            <span style={{ color: "rgba(255,255,255,0.78)" }}>
              Dashboard last refreshed{" "}
              {payloadAgeMin < 1440 ? `${Math.round(payloadAgeMin / 60)}h` : `${Math.round(payloadAgeMin / 1440)}d`} ago —
              the refresh pipeline may be down. Metrics below may not reflect current system state.
            </span>
          </div>
        )}
        <header style={{ display: "flex", alignItems: "baseline", gap: 20, marginBottom: 14, flexWrap: "wrap",
          borderBottom: "1px solid rgba(255,255,255,0.055)", paddingBottom: 12 }}>
          <h1 style={{ fontSize: "1rem", letterSpacing: 1, margin: 0, whiteSpace: "nowrap",
            textShadow: `0 0 40px ${navItems.find(n => n.key === view)?.accent ?? "transparent"}22`,
            transition: "text-shadow 0.4s ease" }}>ORDER SAMURAI · GOVERNANCE</h1>
          {view !== "overview" && view !== "reports" && (
            <span className="mono" style={{
              fontSize: "0.65rem", letterSpacing: 2, padding: "2px 8px", borderRadius: 5,
              color: navItems.find(n => n.key === view)?.accent,
              border: `1px solid ${navItems.find(n => n.key === view)?.accent ?? "transparent"}40`,
              background: `${navItems.find(n => n.key === view)?.accent ?? "transparent"}0a`,
            }}>
              {navItems.find(n => n.key === view)?.label.toUpperCase()}
            </span>
          )}
          <span className="mono" style={{ fontSize: "0.7rem", display: "inline-flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
            {HARNESS_CONFIG.map((h, idx) => {
              const count = payload.record_counts?.[h.key] ?? 0
              const isLast = idx === HARNESS_CONFIG.length - 1
              return (
                <span key={h.key} style={{ color: "var(--muted-foreground)" }}>
                  <span style={{ color: h.color, fontWeight: 600 }}>{h.label}</span>
                  {" · "}
                  <span style={{ color: count > 0 ? "var(--foreground)" : "var(--muted-foreground)" }}>{count}</span>
                  {" "}{h.unit}
                  {!isLast && <span style={{ color: "rgba(255,255,255,0.15)", marginLeft: 10 }}>|</span>}
                </span>
              )
            })}
          </span>
          <div className="mono" style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 6, fontSize: "var(--text-caption)" }}>
            <span style={{ color: "var(--muted-foreground)", marginRight: 2 }}>WINDOW:</span>
            <span style={{ padding: "2px 8px", borderRadius: 4, background: "rgba(74,222,128,0.12)", border: "1px solid rgba(74,222,128,0.35)", color: "#4ade80", fontWeight: 700 }}>7d (FREE)</span>
            {(["14d", "30d", "90d"] as const).map((w) => (
              <span key={w} title="14d-90d history available in Pro Lifetime ($199)" style={{ padding: "2px 6px", borderRadius: 4, background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.08)", color: "rgba(255,255,255,0.35)", cursor: "not-allowed" }}>
                {w} 🔒
              </span>
            ))}
          </div>
        </header>

        <AnimatePresence>
          <motion.div
            key={view}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.18, ease: "easeOut" }}
          >
            {view === "overview" && <Overview payload={payload} onSelect={setView} reflexProps={reflexProps} dojoProps={dojoProps} onUnlock={() => setMode("landing")} />}
            {view === "reports" && <Reports payload={payload} />}
            {view !== "overview" && view !== "reports" && (
              <PillarPage payload={payload} pk={view} reflexProps={reflexProps} onSelectMetric={setSelected} dojoProps={dojoProps} />
            )}
          </motion.div>
        </AnimatePresence>
      </main>

      <AnimatePresence>
        {selected && (
          <MetricModal metric={selected.metric} color={selected.color}
            contributions={modalContributions} scopeLabel={modalScopeLabel} onClose={() => setSelected(null)} dojoProps={dojoProps} />
        )}
      </AnimatePresence>
      </div>
    </div>
  )
}

function ProLockedPanels(_props: { onUnlock?: () => void }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 16, marginTop: 40 }}>
      {/* Autonomous Remediation Queue (Pro) */}
      <div style={{ position: "relative", borderRadius: 16, overflow: "hidden", border: "1px solid rgba(250,204,21,0.25)" }}>
        <div style={{ filter: "blur(6px)", opacity: 0.5, pointerEvents: "none", userSelect: "none", background: "rgba(255,255,255,0.02)", padding: 24 }}>
          <div className="mono" style={{ fontSize: 11, letterSpacing: 2, color: "rgba(255,255,255,0.55)", marginBottom: 14 }}>REMEDIATION QUEUE · OVERNIGHT DOJO</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8, fontSize: 13 }}>
            <div style={{ padding: "10px 14px", borderRadius: 8, background: "rgba(74,222,128,0.05)" }}>reflex loop_breaker → agent #7 chain severed · verified · $38.20 spent</div>
            <div style={{ padding: "10px 14px", borderRadius: 8, background: "rgba(74,222,128,0.05)" }}>reflex zombie_reaper → 4 orphaned processes reaped · verified</div>
            <div style={{ padding: "10px 14px", borderRadius: 8, background: "rgba(250,204,21,0.05)" }}>reflex threshold_recalibrate → awaiting maker-checker approval</div>
          </div>
        </div>
        <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 10, background: "rgba(5,5,5,0.45)", backdropFilter: "blur(4px)" }}>
          <div style={{ fontSize: 22 }}>🔒</div>
          <div className="mono" style={{ fontSize: 11, letterSpacing: 2, color: "#facc15" }}>AUTONOMOUS REMEDIATION · PRO</div>
          <a href="https://jemakaib1.gumroad.com/l/sqwomh" target="_blank" rel="noopener noreferrer" style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "#050505", background: "#facc15", padding: "7px 16px", borderRadius: 6, fontWeight: 700, textDecoration: "none", cursor: "pointer" }}>
            UNLOCK PRO LIFETIME — $199
          </a>
        </div>
      </div>

      {/* Cross-Harness Fleet View (Pro) */}
      <div style={{ position: "relative", borderRadius: 16, overflow: "hidden", border: "1px solid rgba(250,204,21,0.25)" }}>
        <div style={{ filter: "blur(6px)", opacity: 0.5, pointerEvents: "none", userSelect: "none", background: "rgba(255,255,255,0.02)", padding: 24 }}>
          <div className="mono" style={{ fontSize: 11, letterSpacing: 2, color: "rgba(255,255,255,0.55)", marginBottom: 14 }}>FLEET AGGREGATION · 3 HARNESSES</div>
          <div className="mono" style={{ display: "flex", flexDirection: "column", gap: 10, fontSize: 12 }}>
            <div style={{ display: "flex", gap: 12 }}><span style={{ color: "#f472b6" }}>claude-code</span><span style={{ color: "rgba(255,255,255,0.5)" }}>14 agents · drift 89 · $412/wk</span></div>
            <div style={{ display: "flex", gap: 12 }}><span style={{ color: "#facc15" }}>codex</span><span style={{ color: "rgba(255,255,255,0.5)" }}>6 agents · drift 81 · $188/wk</span></div>
            <div style={{ display: "flex", gap: 12 }}><span style={{ color: "#38bdf8" }}>gemini-cli</span><span style={{ color: "rgba(255,255,255,0.5)" }}>3 agents · drift 90 · $64/wk</span></div>
          </div>
        </div>
        <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 10, background: "rgba(5,5,5,0.45)", backdropFilter: "blur(4px)" }}>
          <div style={{ fontSize: 22 }}>🔒</div>
          <div className="mono" style={{ fontSize: 11, letterSpacing: 2, color: "#facc15" }}>CROSS-HARNESS FLEET VIEW · PRO</div>
          <a href="https://jemakaib1.gumroad.com/l/sqwomh" target="_blank" rel="noopener noreferrer" style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "#050505", background: "#facc15", padding: "7px 16px", borderRadius: 6, fontWeight: 700, textDecoration: "none", cursor: "pointer" }}>
            UNLOCK PRO LIFETIME — $199
          </a>
        </div>
      </div>

      {/* Compliance Packs (Pro) */}
      <div style={{ position: "relative", borderRadius: 16, overflow: "hidden", border: "1px solid rgba(250,204,21,0.25)" }}>
        <div style={{ filter: "blur(6px)", opacity: 0.5, pointerEvents: "none", userSelect: "none", background: "rgba(255,255,255,0.02)", padding: 24 }}>
          <div className="mono" style={{ fontSize: 11, letterSpacing: 2, color: "rgba(255,255,255,0.55)", marginBottom: 14 }}>COMPLIANCE PACKS</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 10, fontSize: 13 }}>
            <div>NIST AI RMF — 41/47 controls evidenced</div>
            <div>OWASP Agentic Top 10 — 9/10 mapped</div>
            <div>EU AI Act Art. 15 — runtime evidence bundle</div>
          </div>
        </div>
        <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 10, background: "rgba(5,5,5,0.45)", backdropFilter: "blur(4px)" }}>
          <div style={{ fontSize: 22 }}>🔒</div>
          <div className="mono" style={{ fontSize: 11, letterSpacing: 2, color: "#facc15" }}>COMPLIANCE PACKS · PRO</div>
          <a href="https://jemakaib1.gumroad.com/l/sqwomh" target="_blank" rel="noopener noreferrer" style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "#050505", background: "#facc15", padding: "7px 16px", borderRadius: 6, fontWeight: 700, textDecoration: "none", cursor: "pointer" }}>
            UNLOCK PRO LIFETIME — $199
          </a>
        </div>
      </div>
    </div>
  )
}

// ── Top usage panel (skills / connections / agents) ───────────────────────────
function TopUsagePanel({ usage }: { usage: WIDPayload["top_usage"] }) {
  if (!usage) return null
  type Item = { name: string; count: number }
  const sections: { title: string; emoji: string; items: Item[] }[] = [
    { title: "Top Skills", emoji: "⚔️", items: usage.skills },
    { title: "Top Connections", emoji: "🔌", items: usage.connections },
    { title: "Top Agents", emoji: "🤖", items: usage.agents },
  ]
  return (
    <div style={{ marginTop: 40 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16,
        paddingBottom: 12, borderBottom: "1px solid rgba(255,255,255,0.055)" }}>
        <span style={{ fontSize: "1.3rem" }}>📊</span>
        <h2 style={{ fontSize: "1.1rem", margin: 0, letterSpacing: 2, textTransform: "uppercase" }}>Usage Leaderboard</h2>
        <span className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)" }}>top 5 · all-time telemetry</span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 16 }}>
        {sections.map(({ title, emoji, items }) => (
          <div key={title} className="glass" style={{ borderRadius: 16, padding: "18px 20px" }}>
            <div className="mono" style={{ fontSize: "var(--text-caption)", letterSpacing: 1.5, textTransform: "uppercase",
              color: "var(--muted-foreground)", marginBottom: 14 }}>
              {emoji} {title}
            </div>
            {items.length === 0 ? (
              <div className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)" }}>no data yet</div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {items.map((item, i) => {
                  const max = items[0]?.count ?? 1
                  const pct = Math.round((item.count / max) * 100)
                  const rankColor = i === 0 ? "var(--bow)" : i === 1 ? "var(--brush)" : "var(--muted-foreground)"
                  return (
                    <div key={item.name} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span className="mono" style={{ fontSize: "var(--text-caption)", color: rankColor, minWidth: 14, fontWeight: 700 }}>#{i + 1}</span>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--foreground)",
                          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", marginBottom: 3 }}>
                          {item.name}
                        </div>
                        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                          <div style={{ flex: 1, height: 3, background: "rgba(255,255,255,0.08)", borderRadius: 2 }}>
                            <div style={{ width: `${pct}%`, height: "100%", background: rankColor, borderRadius: 2, opacity: 0.8, boxShadow: `0 0 10px color-mix(in srgb, ${rankColor} 73%, transparent)` }} />
                          </div>
                          <span className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)", minWidth: 24, textAlign: "right" }}>
                            {item.count}
                          </span>
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Overview page ─────────────────────────────────────────────────────────────
function Overview({ payload, onSelect, reflexProps, dojoProps, onUnlock }: { payload: WIDPayload; onSelect: (v: View) => void; reflexProps: ReflexProps; dojoProps: DojoProps; onUnlock: () => void }) {
  const scope: ScoreScope = "window"

  const scores = scoreMap(payload, scope)
  // Worst pillar by tier rollup (CRITICAL > HIGH > PASS), ties broken by fewest passing.
  const tierRank = { CRITICAL: 0, HIGH: 1, PASS: 2 } as const
  const worstKey = [...PILLARS].sort((a, b) => {
    const ra = scores[a.key].rollup, rb = scores[b.key].rollup
    const ta = ra ? tierRank[ra.worst] : 2, tb = rb ? tierRank[rb.worst] : 2
    if (ta !== tb) return ta - tb
    return (ra ? ra.passing / Math.max(ra.graded, 1) : 1) - (rb ? rb.passing / Math.max(rb.graded, 1) : 1)
  })[0].key
  const isActuallyDegraded = scores[worstKey].rollup?.worst === "CRITICAL"

  return (
    <section className="page-enter">
      {/* Needs-attention triage is folded into the Reflexes panel header (count + all-clear);
          the per-metric chips were dropped — each breaching metric is already a reflex card. */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 24,
        paddingBottom: 16, borderBottom: "1px solid rgba(255,255,255,0.055)" }}>
        <span style={{ fontSize: "1.8rem" }}>☯</span>
        <h2 style={{ fontSize: "1.25rem", margin: 0, letterSpacing: 2.5, textTransform: "uppercase", whiteSpace: "nowrap" }}>Bushido Overview</h2>
        <span className="mono" style={{ marginLeft: "auto", fontSize: "var(--text-caption)", letterSpacing: 1.5,
          color: "var(--muted-foreground)", padding: "3px 9px", borderRadius: 6,
          border: "1px solid rgba(255,255,255,0.08)", background: "rgba(255,255,255,0.03)",
          whiteSpace: "nowrap", flexShrink: 0 }}>
          {PILLARS.length} PILLARS
        </span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))", gap: 18 }}>
        {PILLARS.map((p, i) => {
          const sc = scoreMap(payload, scope)[p.key]
          const hero = resolveHero(payload.pillars[p.key], p)
          const tierMix = payload.tier_mix?.[p.key]
          const movers = topMovers(payload.pillars, p.key)
          const isWorstCard = p.key === worstKey && isActuallyDegraded
          return (
            <motion.div
              key={p.key}
              className={`glass-${p.key}`}
              data-tip={`Open ${hero.label} dashboard — ${hero.desc}`}
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              whileHover={{ scale: 1.015, boxShadow: `0 8px 40px ${p.accent}22` }}
              transition={{ duration: 0.24, delay: i * 0.09, ease: "easeOut" }}
              onClick={() => onSelect(p.key)}
              style={{
                borderRadius: 24, padding: "26px 28px", cursor: "pointer", borderTop: `2px solid ${p.accent}99`,
                ...(isWorstCard ? {
                  animation: "card-alert-pulse 1.4s ease-out 0.8s 1 forwards",
                  "--pulse-color": p.accent + "66",
                } as React.CSSProperties : {}),
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
                <NavIcon k={p.key as GlyphKey} size={28} color={p.accent} />
                <div>
                  <div className="mono tip-wide" data-tip={hero.desc} style={{ fontSize: "var(--text-caption)", color: "rgba(255,255,255,0.3)", letterSpacing: 1.5, textTransform: "uppercase", marginBottom: 3, cursor: "help" }}>{hero.label} Overview</div>
                  <div className="mono" style={{ fontSize: "0.65rem", fontWeight: 700, letterSpacing: 2, textTransform: "uppercase", color: p.accent }}>Way of the {p.label}</div>
                </div>
                <span style={{ marginLeft: "auto", display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
                  <span style={{ display: "inline-flex", alignItems: "center" }}>
                    <ScoreNumber score={hero.val} color={p.accent} calibrated={hero.calibrated} />
                    <ScoreDelta delta={hero.delta} />
                  </span>
                  <span className="mono tip-wide" data-tip={hero.desc}
                    onClick={e => e.stopPropagation()}
                    style={{ fontSize: "var(--text-caption)", letterSpacing: 1.2, textTransform: "uppercase",
                      color: "rgba(255,255,255,0.45)", cursor: "help",
                      borderBottom: "1px dotted rgba(255,255,255,0.25)", paddingBottom: 1 }}>
                    {hero.label}{hero.fallbackActive && " · interim"}
                  </span>
                  {hero.detail && !hero.fallbackActive && (
                    <span className="mono" style={{ fontSize: "var(--text-caption)", letterSpacing: 0.6,
                      color: "rgba(255,255,255,0.35)", textAlign: "right" }}>
                      {hero.detail}
                    </span>
                  )}
                  <PillarStatus rollup={sc.rollup} />
                </span>
              </div>

              <p style={{
                fontSize: "0.82rem", color: "rgba(255,255,255,0.75)", lineHeight: 1.65,
                margin: "0 0 14px", padding: "12px 14px",
                background: "rgba(0,0,0,0.22)", borderRadius: 12,
                borderLeft: `3px solid ${p.accent}`,
              }}>
                {(() => {
                  const raw = payload.summaries[p.key] ?? ""
                  const text = raw.replace(/^This pillar scores[\d\s.]+ out of \d+\.\s*/i, "")
                  const dotSpaceIdx = text.indexOf(". ")
                  const firstSentence = dotSpaceIdx !== -1 ? text.slice(0, dotSpaceIdx + 2) : text
                  const rest = dotSpaceIdx !== -1 ? text.slice(dotSpaceIdx + 2) : ""
                  return (
                    <>
                      <strong style={{ color: "rgba(255,255,255,0.95)", display: "block", marginBottom: 6, fontWeight: 600 }}>{firstSentence}</strong>
                      {rest && <span style={{ color: "rgba(255,255,255,0.62)" }}>{rest}</span>}
                    </>
                  )
                })()}
              </p>

              <DojoPanel pillar={p.key} dojoProps={dojoProps} />

              {tierMix?.slices && (
                <div style={{ marginBottom: 12 }}><TierMixMini mix={tierMix} accent={p.accent} /></div>
              )}

              <TrendChips movers={movers} />

              <div style={{
                marginTop: 12, display: "inline-flex", alignItems: "center", gap: 4,
                fontSize: "var(--text-caption)", color: p.accent,
                textTransform: "uppercase", letterSpacing: 1,
                padding: "3px 10px", borderRadius: 6,
                border: `1px solid ${p.accent}30`,
                background: `${p.accent}08`,
              }}>
                inspect →
              </div>
            </motion.div>
          )
        })}
      </div>

      <div style={{ marginTop: 40 }}>
        <ReflexPanel reflexes={payload.reflexes} na={payload.needs_attention} {...reflexProps} />
      </div>

      <TopUsagePanel usage={payload.top_usage} />

      <div style={{ position: "relative", borderRadius: 16, overflow: "hidden", marginTop: 40, border: "1px solid rgba(250,204,21,0.25)" }}>
        <div style={{ filter: "blur(6px)", opacity: 0.5, pointerEvents: "none", userSelect: "none" }}>
          <RemediationPanel eff={payload.remediation_efficacy} />
        </div>
        <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 10, background: "rgba(5,5,5,0.45)", backdropFilter: "blur(4px)" }}>
          <div style={{ fontSize: 22 }}>🔒</div>
          <div className="mono" style={{ fontSize: 11, letterSpacing: 2, color: "#facc15" }}>REMEDIATION EFFICACY & DOJO HISTORY · PRO</div>
          <a href="https://jemakaib1.gumroad.com/l/sqwomh" target="_blank" rel="noopener noreferrer" style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "#050505", background: "#facc15", padding: "7px 16px", borderRadius: 6, fontWeight: 700, textDecoration: "none", cursor: "pointer" }}>
            UNLOCK PRO LIFETIME — $199
          </a>
        </div>
      </div>

      <ProLockedPanels onUnlock={onUnlock} />
    </section>
  )
}
