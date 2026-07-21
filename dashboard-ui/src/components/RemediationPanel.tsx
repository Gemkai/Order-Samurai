import { useState } from "react"
import { AlertTriangle, ChevronDown, ChevronRight } from "lucide-react"
import type { WIDPayload, StuckRemediation } from "@/types"
import { metricLabel } from "@/lib/data"

const PILLAR_COLOR: Record<string, string> = {
  bow: "var(--bow)", sword: "var(--sword)", brush: "var(--brush)", arts: "var(--arts)",
}

function Stat({ label, val, color }: { label: string; val: number | string; color?: string }) {
  return (
    <div>
      <div className="mono" style={{ fontSize: "1.8rem", fontWeight: 700, color: color ?? "var(--foreground)" }}>{val}</div>
      <div className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)", textTransform: "uppercase", letterSpacing: 1 }}>{label}</div>
    </div>
  )
}

function ImpactBar({ rate }: { rate: number | null }) {
  if (rate === null) return <span className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)" }}>no data</span>
  const pct = Math.round(rate * 100)
  const color = pct === 0 ? "var(--sword)" : pct < 50 ? "rgba(251,146,60,0.8)" : "var(--bow)"
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <div style={{ width: 60, height: 4, background: "rgba(255,255,255,0.1)", borderRadius: 2 }}>
        <div style={{ width: `${pct}%`, height: "100%", background: color, borderRadius: 2, boxShadow: `0 0 10px color-mix(in srgb, ${color} 73%, transparent)` }} />
      </div>
      <span className="mono" style={{ fontSize: "var(--text-caption)", color }}>{pct}%</span>
    </div>
  )
}

// Hex (not rgba) so call sites can suffix alpha digits: `${color}18`, `${color}55`.
const FAILURE_MODE_META: Record<string, { label: string; color: string; title: string }> = {
  audit_only:   { label: "audit only",   color: "#93c5fd", title: "Skill diagnoses and reports — the actual fix requires a human action or sprint." },
  accumulation: { label: "accumulation", color: "#fb923c", title: "Skill improves the metric, but new instances regenerate between runs faster than the skill can resolve them." },
  behavioral:   { label: "behavioral",   color: "#c4b5fd", title: "Metric reflects human workflow choices that no skill can change autonomously." },
  auto_fixable: { label: "investigate",  color: "#9ca3af", title: "Skill modifies code or config directly. Root cause may have changed or the skill needs updating." },
}

function StuckCard({ entry }: { entry: StuckRemediation }) {
  const [open, setOpen] = useState(false)
  const pillarColor = PILLAR_COLOR[entry.pillar] ?? "var(--muted-foreground)"
  const statusColor = entry.last_status === "done" ? "var(--bow)"
    : entry.last_status === "error" ? "var(--sword)"
    : entry.last_status === "timeout" ? "rgba(251,146,60,0.8)"
    : "var(--muted-foreground)"
  const modeMeta = FAILURE_MODE_META[entry.failure_mode] ?? FAILURE_MODE_META.auto_fixable

  return (
    <div style={{
      background: "rgba(255,255,255,0.03)",
      border: "1px solid rgba(255,255,255,0.08)",
      borderLeft: `3px solid ${pillarColor}`,
      borderRadius: 10,
      overflow: "hidden",
    }}>
      {/* Header row — always visible */}
      <div
        onClick={() => setOpen(o => !o)}
        style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 12px", cursor: "pointer" }}
      >
        <span style={{ color: pillarColor, flexShrink: 0 }}>
          {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
        </span>
        <span className="mono" style={{ fontSize: "var(--text-caption)", fontWeight: 700, color: pillarColor, minWidth: 30, textTransform: "uppercase" }}>{entry.pillar}</span>
        <span className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--foreground)", flex: 1 }}>
          {metricLabel(entry.metric)}
          {entry.reflex_id.startsWith("trajectory:") && (
            <span style={{ marginLeft: 6, fontSize: "var(--text-caption)", color: "rgba(251,146,60,0.7)", background: "rgba(251,146,60,0.1)", border: "1px solid rgba(251,146,60,0.3)", borderRadius: 3, padding: "1px 4px" }}>trajectory</span>
          )}
        </span>
        <span className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)", minWidth: 100 }}>{entry.command}</span>
        <span className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)", minWidth: 52 }}>{entry.runs_attempted}× attempted</span>
        <span className="mono" title={modeMeta.title}
          style={{ fontSize: "var(--text-caption)", color: modeMeta.color, background: `${modeMeta.color}18`,
                   border: `1px solid ${modeMeta.color}55`, borderRadius: 3, padding: "1px 5px",
                   whiteSpace: "nowrap", flexShrink: 0 }}>
          {modeMeta.label}
        </span>
        <ImpactBar rate={entry.impact_rate} />
        {entry.last_status && (
          <span className="mono" style={{ fontSize: "var(--text-caption)", color: statusColor, minWidth: 38, textAlign: "right" }}>
            last: {entry.last_status}
          </span>
        )}
      </div>

      {/* Expanded detail */}
      {open && (
        <div style={{ padding: "0 12px 12px 12px", borderTop: "1px solid rgba(255,255,255,0.06)" }}>
          {/* Run stats row */}
          <div style={{ display: "flex", gap: 20, paddingTop: 10, paddingBottom: 10, flexWrap: "wrap" }}>
            <div>
              <div className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)", textTransform: "uppercase", letterSpacing: 1 }}>Runs</div>
              <div className="mono" style={{ fontSize: "1.1rem", fontWeight: 700 }}>{entry.runs_attempted}</div>
            </div>
            <div>
              <div className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)", textTransform: "uppercase", letterSpacing: 1 }}>Improved</div>
              <div className="mono" style={{ fontSize: "1.1rem", fontWeight: 700, color: entry.improved_count > 0 ? "var(--bow)" : "var(--sword)" }}>{entry.improved_count}</div>
            </div>
            <div>
              <div className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)", textTransform: "uppercase", letterSpacing: 1 }}>Impact rate</div>
              <div className="mono" style={{ fontSize: "1.1rem", fontWeight: 700, color: (entry.impact_rate ?? 0) === 0 ? "var(--sword)" : "rgba(251,146,60,0.9)" }}>
                {entry.impact_rate === null ? "—" : `${Math.round(entry.impact_rate * 100)}%`}
              </div>
            </div>
            {entry.last_run_at && (
              <div>
                <div className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)", textTransform: "uppercase", letterSpacing: 1 }}>Last run</div>
                <div className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)", paddingTop: 4 }}>
                  {new Date(entry.last_run_at).toLocaleString()}
                </div>
              </div>
            )}
          </div>

          {/* Recommendation */}
          <div style={{
            background: "rgba(255,255,255,0.04)",
            border: "1px solid rgba(255,255,255,0.07)",
            borderRadius: 7,
            padding: "9px 11px",
            marginBottom: 9,
          }}>
            <div className="mono" style={{ fontSize: "var(--text-caption)", color: pillarColor, textTransform: "uppercase", letterSpacing: 1, marginBottom: 5 }}>
              Recommendation
            </div>
            <div style={{ fontSize: "var(--text-caption)", color: "rgba(255,255,255,0.75)", lineHeight: 1.55 }}>
              {entry.recommendation}
            </div>
          </div>

          {/* Unstick callout */}
          <div style={{
            display: "flex", alignItems: "center", gap: 8,
            background: `${pillarColor}0d`, border: `1px solid ${pillarColor}33`,
            borderRadius: 6, padding: "6px 10px",
          }}>
            <span className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)", textTransform: "uppercase", letterSpacing: 1 }}>Unstick after manual fix:</span>
            <code style={{ fontSize: "var(--text-caption)", color: pillarColor, background: "rgba(0,0,0,0.3)", padding: "2px 6px", borderRadius: 4 }}>
              {entry.unstick_endpoint}
            </code>
          </div>
        </div>
      )}
    </div>
  )
}

export function RemediationPanel({ eff }: { eff: WIDPayload["remediation_efficacy"] }) {
  if (!eff) return null
  const oc = (o: string) => o === "improved" ? "var(--bow)" : o === "regressed" ? "var(--sword)" : "var(--muted-foreground)"
  const stuck = eff.stuck_remediations ?? []
  // Older payloads predate attempt counting — treat absence as 0 so the panel
  // degrades to the previous applied-only behavior.
  const attempted = eff.attempted ?? 0
  const completed = eff.completed ?? 0

  return (
    <div style={{ marginTop: 40 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 10 }}>
        <span style={{ fontSize: "1.5rem" }}>🩺</span>
        <h2 style={{ fontSize: "1.2rem", margin: 0, letterSpacing: 2, textTransform: "uppercase" }}>Remediation Efficacy</h2>
        <span className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)" }}>{eff.note}</span>
      </div>

      {/* ── Summary stats + event log ── */}
      {eff.applied === 0 && attempted === 0 ? (
        <div className="glass" style={{ borderRadius: 16, padding: 18, fontSize: "0.7rem", color: "var(--muted-foreground)" }}>
          No remediation skill has run in response to a flagged metric yet.
        </div>
      ) : (
        <div className="glass" style={{ borderRadius: 18, padding: "20px 22px" }}>
          <div style={{ display: "flex", gap: 28, marginBottom: 16, flexWrap: "wrap" }}>
            <Stat label="Attempted" val={attempted} />
            <Stat label="Completed" val={completed} />
            <Stat label="Applied" val={eff.applied} />
            <Stat label="Improved" val={eff.improved} color="var(--bow)" />
            <Stat label="Regressed" val={eff.regressed} color="var(--sword)" />
            <Stat label="Success rate" val={eff.success_rate == null ? "—" : `${eff.success_rate}%`} color="var(--brush)" />
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {[...eff.events].sort((a, b) => new Date(b.used_at).getTime() - new Date(a.used_at).getTime()).map((e, idx) => {
              const d = new Date(e.used_at)
              const dateStr = d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
              const timeStr = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
              const isRonin = e.actor === "ronin"
              return (
                <div key={`${e.metric}-${e.before}-${e.after}-${idx}`} className="mono" style={{ fontSize: "var(--text-caption)", display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                  <span style={{ color: oc(e.outcome), minWidth: 70 }}>{e.outcome}</span>
                  <span style={{ minWidth: 180 }}>{metricLabel(e.metric)}</span>
                  <span style={{ color: "var(--muted-foreground)" }}>{e.before} → {e.after}</span>
                  <span style={{ color: "var(--muted-foreground)", fontSize: "var(--text-caption)" }}>{dateStr} {timeStr}</span>
                  {e.actor && (
                    <span style={{
                      fontSize: "var(--text-caption)", padding: "1px 5px", borderRadius: 3,
                      color: isRonin ? "var(--bow)" : "rgba(255,255,255,0.45)",
                      background: isRonin ? "rgba(250,204,21,0.1)" : "rgba(255,255,255,0.06)",
                      border: `1px solid ${isRonin ? "rgba(250,204,21,0.3)" : "rgba(255,255,255,0.15)"}`,
                    }}>
                      {isRonin ? "⚡ ronin" : "👤 human"}
                    </span>
                  )}
                  <span style={{ marginLeft: "auto", color: "var(--muted-foreground)", minWidth: 0, overflowWrap: "anywhere", textAlign: "right" }}>{e.command}</span>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* ── No-impact report ── */}
      {stuck.length > 0 && (
        <div style={{ marginTop: 28 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
            <AlertTriangle size={15} style={{ color: "var(--sword)", flexShrink: 0 }} />
            <h3 style={{ fontSize: "0.82rem", margin: 0, letterSpacing: 2, textTransform: "uppercase", color: "var(--sword)" }}>
              No-Impact Skills — Requires Human Review
            </h3>
            <span className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)" }}>
              {stuck.length} skill{stuck.length !== 1 ? "s" : ""} hit the loop-breaker · autonomous retries halted
            </span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {stuck.map(entry => (
              // One reflex can be stuck under multiple commands (e.g. after a
              // remediation remap) — reflex_id alone is not unique here.
              <StuckCard key={`${entry.reflex_id}::${entry.command}`} entry={entry} />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
