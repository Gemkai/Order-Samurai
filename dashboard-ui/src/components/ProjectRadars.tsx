import { RadarChart } from "@/components/RadarChart"
import { PieChart } from "@/components/charts/pie-chart"
import { PieSlice } from "@/components/charts/pie-slice"
import { parseMix } from "@/lib/data"
import type { PieData } from "@/components/charts/pie-context"
import { PILLARS, type PillarKey, type WIDPayload } from "@/types"

export function ProjectRadars({ payload }: { payload: WIDPayload }) {
  const platforms = Object.keys(payload.by_platform_scores || {})
  if (!platforms.length) return null
  const PROJECT_ACCENT: Record<string, string> = { claude: "var(--brush)", antigravity: "var(--bow)" }
  return (
    <div style={{ marginTop: 40 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 18 }}>
        <span style={{ fontSize: "1.5rem" }}>🛰️</span>
        <h2 style={{ fontSize: "1.2rem", margin: 0, letterSpacing: 2, textTransform: "uppercase" }}>Per-Model Health Radar</h2>
        <span className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)" }}>weekly · {payload.radar_week?.week ?? ""} · graded-metric pass rate per pillar, one shape per model</span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 18 }}>
        {platforms.map((plat) => {
          const scores = payload.by_platform_scores[plat]
          const accent = PROJECT_ACCENT[plat] ?? "var(--arts)"
          // Axis = pass rate (100 × passing/graded) — a decomposable count ratio,
          // not the retired weighted-mean pillar score.
          const axes = PILLARS.map((p) => {
            const r = scores[p.key]?.rollup
            return { label: p.label, value: r && r.graded ? Math.round((100 * r.passing) / r.graded) : 0, color: p.accent }
          })
          const passing = PILLARS.reduce((a, p) => a + (scores[p.key]?.rollup?.passing ?? 0), 0)
          const graded = PILLARS.reduce((a, p) => a + (scores[p.key]?.rollup?.graded ?? 0), 0)
          return (
            <div key={plat} className="glass" style={{ borderRadius: 20, padding: "22px 18px", display: "flex", flexDirection: "column", alignItems: "center" }}>
              <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 6, alignSelf: "flex-start" }}>
                <span className="mono" style={{ fontSize: "0.8rem", fontWeight: 700, letterSpacing: 1.5, textTransform: "uppercase", color: accent }}>{plat}</span>
                <span className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)" }}>{passing}/{graded} graded passing · {payload.radar_week?.records?.[plat] ?? 0} records this week</span>
              </div>
              <RadarChart axes={axes} stroke={accent} size={250} />
            </div>
          )
        })}
      </div>
    </div>
  )
}

/** Donut pie of a project's relative model-tier mix — mirrors the hero TierMixMini. */
function TierMixPie({ slices }: { slices: PieData[] }) {
  return (
    <div style={{ width: 108, flexShrink: 0, display: "flex", flexDirection: "column", alignItems: "center", gap: 6 }}>
      <PieChart data={slices} size={96} innerRadius={26} padAngle={0.03} cornerRadius={3}>
        {slices.map((s, i) => (
          <PieSlice key={s.label} index={i} />
        ))}
      </PieChart>
      <div style={{ display: "flex", flexDirection: "column", gap: 1, alignSelf: "stretch" }}>
        {slices.slice(0, 3).map((s) => (
          <span key={s.label} className="mono" style={{ fontSize: "var(--text-caption)", color: s.color, fontWeight: 700, display: "flex", alignItems: "center", gap: 4 }}>
            <span style={{ width: 6, height: 6, borderRadius: 1, background: s.color, flexShrink: 0 }} />
            {s.label} {s.value.toFixed(0)}%
          </span>
        ))}
      </div>
    </div>
  )
}

export function PillarProjects({ payload, pk, accent, selectedProject, onProjectSelect }: {
  payload: WIDPayload
  pk: PillarKey
  accent: string
  selectedProject?: string | null
  onProjectSelect?: (name: string | null) => void
}) {
  const projects = Object.entries(payload.by_project || {})
  if (!projects.length) return null
  const withData = projects.filter(([, i]) => i.has_data)
  const noData = projects.filter(([, i]) => !i.has_data)
  const backing = payload.tier_mix?.[pk]?.backing ?? "task volume"
  const mixFor = (info: (typeof withData)[number][1]): PieData[] => {
    const raw = info.tier_mix?.[pk]?.slices
    return raw ? parseMix(raw) : []
  }
  // Tier legend: union of tiers present across projects for this pillar, in descending share.
  const tierTotals = new Map<string, { value: number; color: string }>()
  for (const [, info] of withData) {
    for (const s of mixFor(info)) {
      const cur = tierTotals.get(s.label) ?? { value: 0, color: s.color ?? "#71717a" }
      cur.value += s.value
      tierTotals.set(s.label, cur)
    }
  }
  const legendTiers = [...tierTotals.entries()].sort((a, b) => b[1].value - a[1].value)

  return (
    <div style={{ marginTop: 36 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
        <span style={{ fontSize: "1.2rem" }}>📦</span>
        <h3 className="mono" style={{ fontSize: "0.8rem", margin: 0, fontWeight: 700, letterSpacing: 2, textTransform: "uppercase", color: accent }}>By Project</h3>
        <span className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)" }}>
          relative model-tier mix per project · weighted by {backing}
          {onProjectSelect && <span style={{ color: accent, marginLeft: 8 }}>· click to filter</span>}
        </span>
      </div>
      <div style={{ display: "flex", gap: 14, marginBottom: 16, flexWrap: "wrap" }}>
        {legendTiers.map(([label, t]) => (
          <span key={label} style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <span style={{ width: 9, height: 9, borderRadius: 2, background: t.color }} />
            <span className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)" }}>{label}</span>
          </span>
        ))}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 14 }}>
        {withData.map(([name, info]) => {
          const sel = selectedProject === name
          const mix = mixFor(info)
          return (
            <div
              key={name}
              className="glass"
              data-tip={onProjectSelect ? (sel ? `Clear filter — show all projects` : `Filter this pillar to ${name} metrics only`) : undefined}
              onClick={onProjectSelect ? () => onProjectSelect(sel ? null : name) : undefined}
              style={{
                borderRadius: 16, padding: "16px 14px", display: "flex", gap: 14, alignItems: "center",
                cursor: onProjectSelect ? "pointer" : "default",
                border: sel ? `1px solid ${accent}88` : undefined,
                background: sel ? `color-mix(in srgb, ${accent} 8%, transparent)` : undefined,
                boxShadow: sel ? `0 0 20px ${accent}22, inset 0 0 14px ${accent}0a` : undefined,
                transition: "border-color 0.2s, box-shadow 0.2s, background 0.2s",
              }}
            >
              {mix.length
                ? <TierMixPie slices={mix} />
                : <div style={{ width: 108, flexShrink: 0, fontSize: "var(--text-caption)", color: "var(--muted-foreground)", textAlign: "center" }} className="mono">no tier data</div>}
              <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
                <span className="mono" style={{ fontSize: "0.72rem", fontWeight: 700, color: accent, wordBreak: "break-word" }}>{name}</span>
                <span className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)" }}>{info.platform} · {info.records} records</span>
                {sel && (
                  <span className="mono" style={{ marginTop: 6, fontSize: "var(--text-caption)", color: accent, display: "flex", alignItems: "center", gap: 3 }}>
                    ◉ filtered
                  </span>
                )}
              </div>
            </div>
          )
        })}
      </div>
      {noData.length > 0 && (payload.window?.records ?? 0) > 0 && (
        <div style={{ marginTop: 12, fontSize: "var(--text-caption)", color: "var(--muted-foreground)" }} className="mono">
          No telemetry yet: {noData.map(([n]) => n).join(" · ")}
        </div>
      )}
    </div>
  )
}
