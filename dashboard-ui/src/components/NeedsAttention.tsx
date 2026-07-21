import { metricLabel } from "@/lib/data"
import type { NeedsAttention as NA, PillarKey } from "@/types"

// The top-level triage signal (plan Phase 2). The count is a TRIAGE LABEL beside an always-visible
// list — deliberately NOT a hero KPI number, which would visually re-create the aggregate score we
// removed. Status by color + glyph (colorblind-safe). The list is the decomposition; the count is
// just its length.
const SEV: Record<string, { color: string; glyph: string }> = {
  FAIL: { color: "var(--sword)", glyph: "✕" },
  WARN: { color: "var(--bow)", glyph: "▲" },
}
const PILLAR_COLOR: Record<PillarKey, string> = {
  bow: "var(--bow)", sword: "var(--sword)", brush: "var(--brush)", arts: "var(--arts)",
}

export function NeedsAttention({ na, onSelect }: { na: NA | undefined; onSelect: (pk: PillarKey) => void }) {
  if (!na) return null   // no-data / loading — App renders a skeleton instead of a false "0"

  if (na.count === 0) {
    // All-clear is a feature, not a blank: success state, explicitly stated.
    return (
      <div className="mono" style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 22,
        padding: "11px 16px", borderRadius: 12, border: "1px solid #4ade8033", background: "rgba(74,222,128,0.06)" }}>
        <span aria-hidden style={{ color: "#4ade80", fontWeight: 700 }}>✓</span>
        <span style={{ fontSize: "0.72rem", color: "#4ade80", letterSpacing: 0.5 }}>All SLOs met</span>
        <span style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)" }}>0 metrics need attention</span>
      </div>
    )
  }

  return (
    <div style={{ marginBottom: 22, padding: "14px 18px", borderRadius: 14,
      border: "1px solid var(--sword)44", background: "rgba(239,68,68,0.05)" }}>
      <div className="mono" style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 10, flexWrap: "wrap" }}>
        {/* Triage label — count as inline text, never a giant stat. */}
        <span style={{ fontSize: "0.9rem", fontWeight: 700, color: "var(--sword)", letterSpacing: 0.5 }}>
          {na.count} need attention
        </span>
        <span style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)", letterSpacing: 1, textTransform: "uppercase" }}>
          breaching SLO · sorted by severity
        </span>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 7 }}>
        {na.items.map((it) => {
          const s = SEV[it.status] ?? SEV.WARN
          return (
            <button key={`${it.pillar}:${it.metric}`} onClick={() => onSelect(it.pillar)} className="mono"
              data-tip={`${it.status} · ${metricLabel(it.metric)} = ${it.val} — open ${it.pillar} pillar`}
              style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "4px 9px", borderRadius: 8,
                cursor: "pointer", textAlign: "left", background: "rgba(0,0,0,0.25)",
                border: `1px solid ${s.color}40`, borderLeft: `3px solid ${PILLAR_COLOR[it.pillar]}` }}>
              <span aria-hidden style={{ color: s.color, fontSize: "var(--text-caption)", fontWeight: 700 }}>{s.glyph}</span>
              <span style={{ fontSize: "var(--text-caption)", color: "var(--foreground)" }}>{metricLabel(it.metric)}</span>
              <span style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)" }}>{it.val}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
