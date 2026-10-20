import { isReportLocked, type ReportEntry } from "@/lib/reports"

const ACCENT: Record<string, string> = { audit: "var(--sword)", claude: "var(--brush)", antigravity: "var(--bow)", codex: "var(--arts)" }
const accentOf = (r: ReportEntry) => ACCENT[r.kind === "audit" ? "audit" : r.platform ?? ""] ?? "var(--arts)"
const dateOf = (r: ReportEntry) => r.kind === "weekly" && r.week ? r.week : r.generated.slice(0, 10)

export interface ReportsLogProps {
  entries: ReportEntry[]
  activeIdx: number
  html: string
  isPro: boolean
  onSelect: (idx: number) => void
}

/** The Reports tab's log: real reports from public/reports/index.json. */
export function ReportsLog({ entries, activeIdx, html, isPro, onSelect }: ReportsLogProps) {
  if (entries.length === 0) {
    return (
      <div className="glass" style={{ borderRadius: 18, padding: 36, textAlign: "center", border: "1px dashed rgba(255,255,255,0.15)", background: "rgba(255,255,255,0.01)" }}>
        <h3 className="mono" style={{ margin: "0 0 10px", fontSize: "0.95rem", color: "var(--foreground)", letterSpacing: 1 }}>No reports yet</h3>
        <p className="mono" style={{ margin: "0 auto", fontSize: "0.75rem", color: "var(--muted-foreground)", lineHeight: 1.6, maxWidth: 520 }}>
          Run <code>samurai audit</code> for a full audit of this machine. Its report appears here, and weekly
          reports join it as local telemetry accumulates.
        </p>
      </div>
    )
  }

  const current = entries[activeIdx] ?? entries[0]
  const locked = isReportLocked(entries.indexOf(current), isPro)

  return (
    <div style={{ display: "flex", gap: 20, alignItems: "flex-start", flexWrap: "wrap" }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 220 }}>
        {entries.map((r, idx) => {
          const on = current === r
          return (
            <button key={r.file} onClick={() => onSelect(idx)} className="mono"
              style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", borderRadius: 8, cursor: "pointer", textAlign: "left", width: "100%",
                background: on ? "rgba(255,255,255,0.05)" : "rgba(255,255,255,0.01)",
                border: `1px solid ${on ? "rgba(255,255,255,0.12)" : "rgba(255,255,255,0.04)"}`,
                borderLeft: `3px solid ${on ? accentOf(r) : "transparent"}`,
                color: on ? accentOf(r) : "rgba(255,255,255,0.6)", fontSize: "var(--text-caption)" }}>
              <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                <span>{r.title}</span>
                <span style={{ fontSize: 10, opacity: 0.7 }}>{dateOf(r)}</span>
              </span>
              {isReportLocked(idx, isPro) && <span style={{ marginLeft: "auto", fontSize: 10 }}>🔒</span>}
            </button>
          )
        })}
      </div>

      <div style={{ flex: 1, minWidth: 360, position: "relative", borderRadius: 18, overflow: "hidden" }}>
        <div className="glass report-md" style={{
          borderRadius: 18, padding: "24px 30px",
          borderLeft: `3px solid ${accentOf(current)}`,
          ...(locked ? { filter: "blur(6px)", opacity: 0.4, pointerEvents: "none", userSelect: "none" } : {}),
        }} dangerouslySetInnerHTML={{ __html: html }} />

        {locked && (
          <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "flex-start", paddingTop: 96, gap: 10, background: "rgba(5,5,5,0.55)", backdropFilter: "blur(4px)" }}>
            <div style={{ fontSize: 24 }}>🔒</div>
            <div className="mono" style={{ fontSize: 12, letterSpacing: 2, color: "#facc15", fontWeight: 700 }}>PAST REPORTS · PRO</div>
            <div className="mono" style={{ fontSize: 11, color: "rgba(255,255,255,0.6)" }}>Your latest report is included in Free. Report history ships with Pro.</div>
          </div>
        )}
      </div>
    </div>
  )
}
