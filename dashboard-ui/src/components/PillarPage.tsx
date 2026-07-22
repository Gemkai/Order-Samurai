import { useState } from "react"
import { motion } from "motion/react"
import { MetricViz } from "@/components/MetricViz"
import { PILLARS, type PillarKey, type WIDPayload } from "@/types"
import { flatten, metricLabel, type FlatMetric } from "@/lib/data"
import { num, assignViz, resolveHero, ringMax, scoreMap } from "@/lib/metrics"
import { ScoreNumber, PillarStatus, TierMixMini, TrendBadge, StatusBadge } from "@/components/helpers"
import { ThresholdSparkline } from "@/components/ThresholdSparkline"
import { metricStatus } from "@/lib/slo"
import { tierColor } from "@/components/helper-values"
import { ReflexPanel, type ReflexProps } from "@/components/ReflexList"
import { ArchitecturePanel } from "@/components/ArchitecturePanel"
import { PillarProjects } from "@/components/ProjectRadars"
import { DojoPanel } from "@/components/DojoPanel"
import type { DojoProps } from "@/hooks/useDojo"
import { IconShuriken, IconKatana, IconFan, IconArmor } from "@/components/SamuraiIcons"

const FREE_UNLOCKED_METRICS: Record<string, Set<string>> = {
  sword: new Set([
    "kill_chains_disrupted", "rule_violations", "open_cves",
    "boundary_violations", "secrets_detected", "guardrail_blocks", "kill_chains_detected"
  ]),
  bow: new Set([
    "estimated_agent_time_saved", "error_rate", "complexity_weighted_throughput",
    "session_count", "avg_session_turns", "agent_autonomy_ratio", "governance_pass_rate", "mcp_smoke_fails"
  ]),
  brush: new Set([
    "estimated_cost_savings", "total_cost", "token_spend",
    "local_routing_share", "revision_ratio", "subagent_efficiency_index"
  ]),
  arts: new Set([
    "human_hours_saved", "slop_density", "rework_loops",
    "simplify_runs", "doc_parity_issues", "craft_improvements", "wiki_health_score"
  ]),
}

export function PillarPage({ payload, pk, reflexProps, onSelectMetric, dojoProps }: {
  payload: WIDPayload; pk: PillarKey; reflexProps: ReflexProps
  onSelectMetric: (s: { metric: FlatMetric; color: string }) => void
  dojoProps?: DojoProps
}) {
  const [selectedTier, setSelectedTier] = useState<string | null>(null)
  const [selectedProject, setSelectedProject] = useState<string | null>(null)


  const meta = PILLARS.find((p) => p.key === pk)!

  // Swap both pillars + score when tier selected; fall back to combined view
  const tierPillars = selectedTier ? payload.by_tier?.[selectedTier] : undefined
  const activePillars = tierPillars ?? payload.pillars

  // Project filter: override metric values with per-project data where available
  const projInfo = selectedProject ? payload.by_project?.[selectedProject] : undefined
  const baseMetrics = flatten(activePillars, pk)
  const metrics = projInfo?.metrics
    ? baseMetrics.map((m) => {
        const pv = (projInfo.metrics as Record<string, number>)[m.key]
        if (pv !== undefined) {
          return { ...m, env: { ...m.env, val: String(pv), is_simulated: false, delta: "" as const, trend: "neutral" as const, history: [] } }
        }
        return { ...m, env: { ...m.env, is_simulated: true } }
      })
    : baseMetrics

  const baseSc = (selectedTier ? payload.by_tier_scores?.[selectedTier]?.[pk] : undefined)
    ?? scoreMap(payload, "window")[pk]
  const sc = projInfo
    ? { graded_count: undefined, total_gradeable: undefined, coverage_pct: null as number | null, flags: [] as { name: string; val: string; grade: string }[] }
    : baseSc

  const tierMix = payload.tier_mix?.[pk]
  const hero = resolveHero(activePillars[pk], meta)
  const vizByMetric = assignViz(metrics)

  const groupMap = new Map<string, FlatMetric[]>()
  for (const m of metrics) {
    if (!groupMap.has(m.group)) groupMap.set(m.group, [])
    groupMap.get(m.group)!.push(m)
  }
  const subCategories = [...groupMap.entries()]
    .map(([group, items]) => ({ group, items }))
    .sort((a, b) => b.items.length - a.items.length)

  return (
    <section key={pk} className="page-enter">
      <div
        className={`glass-${pk}`}
        style={{ borderRadius: 28, padding: "28px 32px", marginBottom: 28,
          borderTop: `3px solid ${meta.accent}`, overflow: "hidden" }}
      >
        <div style={{ display: "flex", gap: 32, alignItems: "stretch", flexWrap: "wrap" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 18, paddingRight: 24, borderRight: "1px solid rgba(255,255,255,0.07)",
            backgroundImage: `radial-gradient(circle at 50% 45%, ${meta.accent}0e 0%, transparent 65%)` }}>
            {(() => {
              return (
                <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
                  <div className="mono tip-wide" data-tip={hero.desc}
                    style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)", letterSpacing: 2, textTransform: "uppercase", marginBottom: 6,
                      cursor: hero.desc ? "help" : undefined,
                      borderBottom: hero.desc ? "1px dotted rgba(255,255,255,0.25)" : undefined, paddingBottom: 1 }}>
                    {hero.label || `${meta.category} Status`}{hero.fallbackActive && " · interim"}
                  </div>
                  <ScoreNumber score={hero.val} color={meta.accent} calibrated={hero.calibrated} big />
                  <div style={{ marginTop: 10 }}>
                    {/* Pillar STATUS only (worst-metric tier + passing fraction). The
                        weighted-mean "drift" rollup grade is dropped per the de-aggregation
                        plan — status-first, no pillar score. */}
                    <PillarStatus rollup={sc.rollup} />
                  </div>
                </div>
              )
            })()}
            {tierMix?.slices && (
              <TierMixMini
                mix={tierMix}
                accent={meta.accent}
                selectedTier={selectedTier}
                onSelect={payload.by_tier ? setSelectedTier : undefined}
              />
            )}
          </div>

          <div style={{ flex: "1 1 420px", minWidth: 320 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 12 }}>
              {pk === "bow"   ? <IconShuriken size={44} style={{ color: meta.accent, flexShrink: 0 }} /> :
               pk === "sword" ? <IconKatana   size={44} style={{ color: meta.accent, flexShrink: 0 }} /> :
               pk === "brush" ? <IconFan      size={44} style={{ color: meta.accent, flexShrink: 0 }} /> :
                                <IconArmor    size={44} style={{ color: meta.accent, flexShrink: 0 }} />}
              <div>
                <div className="mono tip-wide" data-tip={hero.desc} style={{ fontSize: "0.65rem", fontWeight: 700, letterSpacing: 2.5, textTransform: "uppercase", color: meta.accent, marginBottom: 4, cursor: "help" }}>{hero.label} Overview</div>
                <h2 style={{ fontSize: "1.4rem", margin: 0, color: meta.accent, textTransform: "uppercase", letterSpacing: 2, textShadow: `0 0 40px ${meta.accent}44` }}>Way of the {meta.label}</h2>
              </div>
            </div>
            <div style={{ display: "flex", gap: 10, alignItems: "stretch" }}>
              <p style={{
                fontSize: "var(--text-body)", color: "rgba(255,255,255,0.82)", lineHeight: 1.75,
                margin: 0, padding: "12px 14px", flex: 1,
                background: "rgba(0,0,0,0.22)", borderRadius: 12,
                borderLeft: `3px solid ${meta.accent}`,
              }}>
                {payload.summaries[pk]}
              </p>
              {dojoProps && (
                <div style={{
                  flexShrink: 0, padding: "10px 12px",
                  background: "rgba(0,0,0,0.18)", borderRadius: 12,
                  border: "1px solid rgba(255,255,255,0.06)",
                  display: "flex", alignItems: "center",
                }}>
                  <DojoPanel pillar={pk} dojoProps={dojoProps} inline />
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      <ReflexPanel reflexes={(payload.reflexes ?? []).filter((r) => r.id.startsWith(`metric:${pk}:`))} {...reflexProps} dojoProps={dojoProps} />

      {pk === "brush" && (
        <div style={{ margin: "0 0 22px" }}>
          <ArchitecturePanel arch={payload.architecture} />
        </div>
      )}

      {selectedTier && (
        <div className="mono" style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16,
          padding: "7px 14px", borderRadius: 10, background: "rgba(255,255,255,0.04)",
          border: `1px solid ${meta.accent}44`, fontSize: "0.65rem" }}>
          <span style={{ color: meta.accent }}>⬡ TIER FILTER: {selectedTier}</span>
          <span style={{ color: "var(--muted-foreground)" }}>— metrics show only sessions using this model tier. Scout/security metrics are unaffected (point-in-time).</span>
          <button
            data-tip="Clear tier filter — show all model tiers"
            onClick={() => setSelectedTier(null)}
            style={{ marginLeft: "auto", background: "none", border: "none", cursor: "pointer",
              color: "var(--muted-foreground)", fontSize: "0.65rem", padding: "2px 6px" }}>
            ✕ clear
          </button>
        </div>
      )}

      {selectedProject && (
        <div className="mono" style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16,
          padding: "7px 14px", borderRadius: 10, background: "rgba(255,255,255,0.04)",
          border: `1px solid ${meta.accent}44`, fontSize: "0.65rem" }}>
          <span style={{ color: meta.accent }}>◉ PROJECT FILTER: {selectedProject}</span>
          <span style={{ color: "var(--muted-foreground)" }}>
            — metrics show values for this project only.{" "}
            {projInfo ? `${projInfo.records} records · ${projInfo.platform}` : ""}
            {" "}Metrics not tracked for this project are greyed out.
          </span>
          <button
            data-tip="Clear project filter — show all projects combined"
            onClick={() => setSelectedProject(null)}
            style={{ marginLeft: "auto", background: "none", border: "none", cursor: "pointer",
              color: "var(--muted-foreground)", fontSize: "0.65rem", padding: "2px 6px" }}>
            ✕ clear
          </button>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(460px, 1fr))", gap: 22, alignItems: "start" }}>
      {subCategories.map((sec) => {
        // Under a project filter, is_simulated means "not tracked for this project —
        // greyed out" (see the metrics override above), so keep everything visible there.
        const shown = sec.items
        return (
        <div key={sec.group}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
            <span className="mono" style={{ fontSize: "0.72rem", fontWeight: 700, letterSpacing: 1.8, textTransform: "uppercase", color: meta.accent }}>
              <span style={{ fontSize: "var(--text-caption)", opacity: 0.5, marginRight: 5, verticalAlign: "middle" }}>◆</span>{sec.group}
            </span>
            <span style={{ flex: 1, height: 1, background: `linear-gradient(90deg, ${meta.accent}28, rgba(255,255,255,0.04))` }} />
            <span className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)" }}>
              {sec.items.length} metrics
            </span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 14 }}>
            {shown.map((m, j) => {
              const kind = vizByMetric.get(`${m.group}::${m.key}`) ?? "line"
              const graded = !!m.env.is_graded
              const isActiveExec = dojoProps?.execCommand != null && dojoProps.execCommand === m.env.mitigation_command
              const execRunning = isActiveExec && dojoProps?.execStatus === 'running'
              const execDone    = isActiveExec && dojoProps?.execStatus === 'done'
              const execError   = isActiveExec && dojoProps?.execStatus === 'error'
              const isProLocked = !FREE_UNLOCKED_METRICS[pk]?.has(m.key.toLowerCase())
              return (
                <div key={`${m.group}::${m.key}`} style={{ position: "relative", borderRadius: 14, overflow: "hidden" }}>
                  <motion.div
                    className={`glass-${pk}-card${graded ? " metric-graded" : ""}`}
                    data-tip={isProLocked ? "Unlock Pro Lifetime ($199) for deep forensic metrics" : `Inspect telemetry detail for ${metricLabel(m.key)}`}
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: m.env.is_simulated ? 0.45 : 1, y: 0 }}
                    transition={{ duration: 0.22, delay: j * 0.035, ease: "easeOut" }}
                    whileHover={{ scale: 1.025, opacity: 1, boxShadow: `0 6px 28px ${meta.accent}1e`, transition: { duration: 0.15, delay: 0 } }}
                    onClick={() => {
                      if (isProLocked) {
                        if (typeof window !== "undefined") {
                          window.open("https://ordersamurai.gumroad.com/l/pro", "_blank")
                        }
                      } else {
                        onSelectMetric({ metric: m, color: meta.accent })
                      }
                    }}
                    style={{
                      padding: "14px 16px", cursor: "pointer",
                      borderLeft: m.env.flagged ? `2px solid ${meta.accent}` : "2px solid transparent",
                      transition: "border-left-color 0.2s ease",
                      ...(isProLocked ? { filter: "blur(4px)", opacity: 0.5, userSelect: "none" } : {}),
                    }}
                  >
                  <div style={{ fontSize: "0.85rem", marginBottom: 6, textTransform: "capitalize", letterSpacing: 0.3 }}>{metricLabel(m.key)}</div>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", minWidth: 0,
                    background: `linear-gradient(135deg, ${meta.accent}0d, transparent)`,
                    borderRadius: 8, padding: "2px 6px 2px 4px", margin: "0 -4px" }}>
                    {/* minWidth 0 + overflowWrap: a 9-digit value must wrap INSIDE the card —
                        without it the flex row overflows and the tier badge (marginLeft auto)
                        renders on top of the neighboring card (2026-07-19 layout sweep). */}
                    <span className="mono" style={{ fontSize: "1.4rem", fontWeight: 700, color: meta.accent, minWidth: 0, overflowWrap: "anywhere", lineHeight: 1.15 }}>
                      {m.env.is_simulated ? "—" : m.env.val}{!m.env.is_simulated && m.env.is_percent ? "%" : ""}
                    </span>
                    {/* SLO status badge — the status-first surface (Phase 1). Shown for graded
                        metrics (OK/WARN/FAIL) and informational ones (NO TARGET); hidden when
                        the metric is simulated (the "—" value already reads as no-data). */}
                    {!m.env.is_simulated && (() => {
                      const st = metricStatus(m.env)
                      return st === "NODATA" ? null : <StatusBadge status={st} />
                    })()}
                    <span className="mono" style={{ marginLeft: "auto", fontSize: "var(--text-caption)", fontWeight: 700,
                      color: tierColor(m.env.tier), letterSpacing: 0.5,
                      background: `${tierColor(m.env.tier)}14`, border: `1px solid ${tierColor(m.env.tier)}38`,
                      borderRadius: 4, padding: "2px 5px" }}>{m.env.tier}</span>
                  </div>

                  {!m.env.is_simulated && (m.env.calibrated === false || m.env.data_gap) && (
                    <div style={{ display: "flex", gap: 5, marginTop: 5 }}>
                      {m.env.calibrated === false && (
                        <span className="mono"
                          data-tip="Estimate from benchmark coefficients — not yet calibrated against measured samples"
                          style={{ fontSize: "var(--text-caption)", fontWeight: 700, letterSpacing: 0.5, color: "#facc15",
                            background: "#facc1514", border: "1px solid #facc1538",
                            borderRadius: 4, padding: "1px 5px" }}>EST · UNCALIBRATED</span>
                      )}
                      {m.env.data_gap && (
                        <span className="mono"
                          data-tip="No source events this period — value may reflect a dead producer, not a real reading"
                          style={{ fontSize: "var(--text-caption)", fontWeight: 700, letterSpacing: 0.5, color: "var(--sword)",
                            background: "rgba(255,80,80,0.08)", border: "1px solid rgba(255,80,80,0.25)",
                            borderRadius: 4, padding: "1px 5px" }}>DATA GAP</span>
                      )}
                    </div>
                  )}

                  <div style={{ marginTop: 4, marginBottom: 6 }}><TrendBadge env={m.env} /></div>

                  {!m.env.is_simulated && (
                    <div style={{ marginTop: 4, color: meta.accent }}>
                      {/* Graded metrics get the 30-day trend with warn/fail lines drawn in
                          (distance-to-breach visible); informational metrics keep the
                          auto-assigned viz since they have no threshold to draw. */}
                      {m.env.rule
                        ? <ThresholdSparkline history={m.env.history} rule={m.env.rule} color={meta.accent} height={40} />
                        : <MetricViz kind={kind} history={m.env.history} value={num(m.env.val)} max={ringMax(m)} color={meta.accent} height={kind === "gauge" || kind === "ring" ? 46 : 40} />}
                    </div>
                  )}

                  {m.env.mitigation_command && (
                    <button
                      data-tip={execRunning ? "Remediation in progress…" : `Run '${m.env.mitigation_command}' to auto-remediate this metric`}
                      onClick={(e) => { e.stopPropagation(); if (dojoProps && !execRunning) dojoProps.exec(m.env.mitigation_command!) }}
                      disabled={execRunning}
                      className="mono"
                      style={{
                        marginTop: 8, display: "block", width: "100%", textAlign: "left",
                        fontSize: "var(--text-caption)",
                        color: execRunning ? "rgba(255,255,255,0.35)" : execDone ? "#4ade80" : execError ? "var(--sword)" : m.env.flagged ? meta.accent : "var(--muted-foreground)",
                        background: "none",
                        border: `1px solid ${execRunning ? "rgba(255,255,255,0.08)" : execDone ? "#4ade8044" : execError ? "var(--sword)44" : "rgba(255,255,255,0.1)"}`,
                        borderRadius: 5, padding: "3px 7px",
                        cursor: execRunning ? "not-allowed" : "pointer",
                        opacity: execRunning ? 0.7 : m.env.flagged ? 0.9 : 0.65,
                        whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                      }}
                    >
                      {execRunning ? `⏳ running…` : execDone ? `✓ done` : execError ? `✗ failed — retry?` : m.env.flagged ? `⚑ fix: ${m.env.mitigation_command}` : `🛠 ${m.env.mitigation_command}`}
                    </button>
                  )}
                  {isActiveExec && dojoProps!.execOutput.length > 0 && (
                    <div
                      onClick={(e) => e.stopPropagation()}
                      style={{
                        marginTop: 6, background: "rgba(0,0,0,0.35)",
                        border: `1px solid ${meta.accent}25`, borderRadius: 6,
                        padding: "5px 8px", maxHeight: 80, overflowY: "auto",
                      }}
                    >
                      {dojoProps!.execOutput.slice(-8).map((line, i) => (
                        <div key={i} style={{ fontSize: "var(--text-caption)", color: meta.accent, opacity: 0.55, lineHeight: 1.6, fontFamily: "JetBrains Mono, monospace" }}>
                          {line}
                        </div>
                      ))}
                    </div>
                  )}
                </motion.div>
                {isProLocked && (
                  <div style={{
                    position: "absolute", inset: 0, display: "flex", flexDirection: "column",
                    alignItems: "center", justifyContent: "center", gap: 4,
                    background: "rgba(5,5,5,0.45)", backdropFilter: "blur(2px)",
                    borderRadius: 14, zIndex: 5, pointerEvents: "none"
                  }}>
                    <div style={{ fontSize: 16 }}>🔒</div>
                    <span className="mono" style={{ fontSize: 9, letterSpacing: 1.5, color: "#facc15", fontWeight: 700 }}>PRO UNLOCK</span>
                  </div>
                )}
              </div>
            )
            })}
          </div>
        </div>
        )
      })}
      </div>

      <PillarProjects
        payload={payload}
        pk={pk}
        accent={meta.accent}
        selectedProject={selectedProject}
        onProjectSelect={setSelectedProject}
      />
    </section>
  )
}
