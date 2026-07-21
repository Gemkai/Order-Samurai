import { PieChart } from "@/components/charts/pie-chart"
import { PieSlice } from "@/components/charts/pie-slice"
import { MetricViz } from "@/components/MetricViz"
import { metricLabel, parseMix, TIER_MODEL_LABELS, type FlatMetric } from "@/lib/data"
import { trendColor, trendArrow } from "@/components/helper-values"

const TREND_MARK: Record<string, string> = { up: "▲", down: "▼", neutral: "•" }

export function TrendMark({ trend }: { trend: string }) {
  return <span style={{ color: trendColor(trend), fontSize: "0.7rem" }}>{TREND_MARK[trend] ?? TREND_MARK.neutral}</span>
}

export function TrendBadge({ env }: { env: FlatMetric["env"] }) {
  if (env.is_simulated) return null
  const word: Record<string, string> = { up: "rising", down: "falling", neutral: "flat" }
  return (
    <span className="mono" style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: "var(--text-caption)", color: trendColor(env.trend) }}>
      <span>{trendArrow(env.trend)}</span>
      <span>{env.delta && env.delta !== "+0.0" ? env.delta : (word[env.trend] ?? word.neutral)}</span>
    </span>
  )
}

export function TrendChips({ movers }: { movers: FlatMetric[] }) {
  if (!movers.length) return <span style={{ fontSize: "0.65rem", color: "var(--muted-foreground)" }}>no movement yet</span>
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
      {movers.map((m) => {
        const col = trendColor(m.env.trend)
        const d = String(m.env.delta).replace(/^\+/, "")
        return (
          <span key={`${m.group}::${m.key}`} className="mono" style={{ fontSize: "var(--text-caption)", padding: "2px 7px", borderRadius: 6, border: `1px solid ${col}`, color: col }}>
            {metricLabel(m.key)} {trendArrow(m.env.trend)}{d}
          </span>
        )
      })}
    </div>
  )
}

function gradeColor(grade: string | null | undefined, fallback: string): string {
  switch (grade) {
    case "A": return "#4ade80"
    case "B": return "#86efac"
    case "C": return "#facc15"
    case "D": return "#fb923c"
    case "F": return "#ef4444"
    default:  return fallback
  }
}

export function ScoreNumber({ score, grade, graded, total, color, big, calibrated = true }: {
  score: number | string | null
  grade?: string | null
  graded?: number
  total?: number
  color: string
  big?: boolean
  calibrated?: boolean
}) {
  // Null = no live graded metrics — show em-dash, not a misleading number
  if (score === null) {
    return big ? (
      <span className="mono" style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
        <span style={{ fontSize: "4.6rem", fontWeight: 700, color: "var(--muted-foreground)", lineHeight: 1 }}>—</span>
        <span style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)" }}>no graded metrics</span>
      </span>
    ) : (
      <span className="mono" title="No graded metrics in this view"
        style={{ fontSize: "2.4rem", fontWeight: 700, color: "var(--muted-foreground)", lineHeight: 1 }}>—</span>
    )
  }

  const coverageLabel = graded !== undefined && total !== undefined
    ? `${graded}/${total} metrics` : undefined

  const isUncalibrated = calibrated === false
  const displayStyle = isUncalibrated
    ? { color: "var(--muted-foreground)", fontStyle: "italic" as const }
    : { color: gradeColor(grade, color), textShadow: grade ? `0 0 28px ${gradeColor(grade, color)}55, 0 0 56px ${gradeColor(grade, color)}22` : undefined }

  const tooltipText = isUncalibrated
    ? "Industry benchmark — replaces with real data after 20 measurements or 4 weeks"
    : `Score: ${score}/100`

  if (big) {
    return (
      <span className="mono" style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 3 }}>
        <span title={tooltipText} style={{ display: "inline-flex", alignItems: "baseline", gap: 10, cursor: "default" }}>
          <span style={{ fontSize: "4.6rem", fontWeight: 700, lineHeight: 1, ...displayStyle }}>
            {grade || score}{isUncalibrated && "*"}
          </span>
          {!grade && typeof score === 'number' && (
            <span style={{ fontSize: "1rem", color: "var(--muted-foreground)", alignSelf: "center" }}>
              /100
            </span>
          )}
        </span>
        {coverageLabel && (
          <span style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)" }}>{coverageLabel} graded</span>
        )}
      </span>
    )
  }

  // Compact (overview cards) — grade letter + tooltip with full score + coverage
  const compactStyle = isUncalibrated
    ? { color: "var(--muted-foreground)", fontStyle: "italic" as const }
    : { color: gradeColor(grade, color), textShadow: grade ? `0 0 18px ${gradeColor(grade, color)}50, 0 0 36px ${gradeColor(grade, color)}20` : undefined }
  const compactTooltip = isUncalibrated
    ? "Industry benchmark — replaces with real data after 20 measurements or 4 weeks"
    : `Score: ${score}/100${coverageLabel ? ` · ${coverageLabel}` : ""}`

  return (
    <span className="mono" title={compactTooltip} style={{ display: "inline-flex", alignItems: "baseline", gap: 3, cursor: "default" }}>
      <span style={{ fontSize: "2.4rem", fontWeight: 700, lineHeight: 1, ...compactStyle }}>
        {grade ?? score}{isUncalibrated && "*"}
      </span>
      {!grade && typeof score === 'number' && (
        <span style={{ fontSize: "0.9rem", color: "var(--muted-foreground)" }}>
          /100
        </span>
      )}
    </span>
  )
}

const TIER_STYLE: Record<string, { color: string; bg: string }> = {
  PASS:     { color: "#4ade80", bg: "rgba(74,222,128,0.08)" },
  HIGH:     { color: "var(--bow)", bg: "rgba(250,204,21,0.08)" },
  CRITICAL: { color: "var(--sword)", bg: "rgba(239,68,68,0.10)" },
}

/** Pillar STATUS chip — worst metric tier + passing fraction. This is the pillar's
 *  only rollup (the weighted-mean drift index was retired 2026-07-19).
 *  Worst tier wins: one CRITICAL metric marks the pillar CRITICAL — no averaging. */
export function PillarStatus({ rollup }: {
  rollup?: { worst: "PASS" | "HIGH" | "CRITICAL"; passing: number; graded: number }
}) {
  if (!rollup) return null
  const t = TIER_STYLE[rollup.worst]
  return (
    <span className="mono" style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
      <span title={`Worst metric tier in this pillar — ${rollup.passing} of ${rollup.graded} graded metrics passing`}
        style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "3px 9px", borderRadius: 6,
          background: t.bg, border: `1px solid ${t.color}44`, cursor: "default" }}>
        <span style={{ width: 7, height: 7, borderRadius: "50%", background: t.color, flexShrink: 0 }} />
        <span style={{ fontSize: "var(--text-caption)", fontWeight: 700, letterSpacing: 1, color: t.color }}>{rollup.worst}</span>
        <span style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)" }}>{rollup.passing}/{rollup.graded}</span>
      </span>
    </span>
  )
}

/** Trend arrow + numeric delta for a pillar score.
 *  null  → no prior baseline, renders nothing.
 *  0     → baseline exists, score held steady, renders "—".
 *  nonzero → renders colored arrow + value. */
export function ScoreDelta({ delta, big }: { delta?: number | null; big?: boolean }) {
  if (delta == null) return null
  if (delta === 0) return (
    <span className="mono" style={{
      fontSize: big ? "1rem" : "0.68rem",
      color: "var(--muted-foreground)",
      marginLeft: big ? 12 : 6,
    }}>—</span>
  )
  const up = delta > 0
  const arrow = up ? "▲" : "▼"
  const color = up ? "#4ade80" : "var(--sword)"
  const sign = up ? "+" : ""
  return (
    <span className="mono" style={{
      display: "inline-flex", alignItems: "center", gap: 3,
      fontSize: big ? "1rem" : "0.68rem", color,
      marginLeft: big ? 12 : 6,
    }}>
      <span style={{ fontSize: big ? "0.9rem" : "var(--text-caption)" }}>{arrow}</span>
      <span>{sign}{delta}</span>
    </span>
  )
}

export function TierMixMini({ mix, accent, selectedTier, onSelect }: {
  mix: { backing: string; slices: string | null }
  accent: string
  selectedTier?: string | null
  onSelect?: (tier: string | null) => void
}) {
  if (!mix?.slices) return null
  const slices = parseMix(mix.slices).filter((s) => s.value > 0)
  if (!slices.length) return null
  return (
    <div>
      <div className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)", marginBottom: 8, textAlign: "center" }}>
        MODEL TIER MIX · by {mix.backing}
        {selectedTier && (
          <span style={{ marginLeft: 6, color: accent }}>· {selectedTier} filtered</span>
        )}
      </div>
      <div style={{ display: "flex", gap: 14, alignItems: "center" }}>
        <PieChart data={slices} size={120} innerRadius={32} padAngle={0.03} cornerRadius={3}>
          {slices.map((s, i) => (
            <PieSlice
              key={s.label}
              index={i}
              onClick={onSelect ? (idx) => {
                const tier = slices[idx]?.label ?? null
                onSelect(selectedTier === tier ? null : tier)
              } : undefined}
            />
          ))}
        </PieChart>
        <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
          {slices.slice(0, 5).map((s) => {
            const active = selectedTier === s.label
            return (
              <div
                key={s.label}
                data-tip={onSelect ? (active ? `Clear ${s.label} tier filter` : `Filter metrics to ${s.label} model tier only`) : undefined}
                onClick={onSelect ? () => onSelect(selectedTier === s.label ? null : s.label) : undefined}
                style={{ display: "flex", alignItems: "center", gap: 6, cursor: onSelect ? "pointer" : "default",
                  opacity: selectedTier && !active ? 0.4 : 1 }}
              >
                <span style={{ width: 8, height: 8, borderRadius: 2, background: s.color,
                  flexShrink: 0, outline: active ? `2px solid ${s.color}` : "none", outlineOffset: 1 }} />
                <span className="mono" style={{ fontSize: "var(--text-caption)", color: s.color, fontWeight: 700, minWidth: 70 }}>{s.label} {s.value.toFixed(0)}%</span>
                <span className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)" }}>{TIER_MODEL_LABELS[s.label] ?? ""}</span>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

export function Sparkline({ history, color }: { history: number[]; color: string }) {
  return <div style={{ width: 200 }}><MetricViz kind="area" history={history} color={color} height={48} /></div>
}

// SLO status badge — color + a distinct shape (glyph), never color alone (colorblind-safe).
// INFO = informational/no-target (neutral, NOT green — untargeted ≠ passing).
import type { SloStatus } from "@/lib/slo"
const SLO_STYLE: Record<SloStatus, { color: string; glyph: string; label: string }> = {
  OK:     { color: "#4ade80",                 glyph: "✓", label: "OK" },
  WARN:   { color: "var(--bow)",              glyph: "▲", label: "WARN" },
  FAIL:   { color: "var(--sword)",            glyph: "✕", label: "FAIL" },
  "needs:human": { color: "#facc15",          glyph: "🔧", label: "NEEDS HUMAN" },
  INFO:   { color: "var(--muted-foreground)", glyph: "○", label: "NO TARGET" },
  NODATA: { color: "var(--muted-foreground)", glyph: "—", label: "NO DATA" },
}

export function StatusBadge({ status, title }: { status: SloStatus; title?: string }) {
  const s = SLO_STYLE[status]
  return (
    <span className="mono" title={title ?? s.label}
      style={{ display: "inline-flex", alignItems: "center", gap: 4, padding: "1px 6px", borderRadius: 5,
        background: `${s.color}14`, border: `1px solid ${s.color}40`, cursor: "default", whiteSpace: "nowrap" }}>
      <span aria-hidden style={{ color: s.color, fontSize: "var(--text-caption)", fontWeight: 700, lineHeight: 1 }}>{s.glyph}</span>
      <span style={{ fontSize: "var(--text-caption)", fontWeight: 700, letterSpacing: 0.6, color: s.color }}>{s.label}</span>
    </span>
  )
}
