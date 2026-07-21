import { useState, type MouseEvent, type CSSProperties } from "react"
import { X, Zap, Swords, AlertTriangle } from "lucide-react"
import type { Reflex, NeedsAttention } from "@/types"
import { REFLEX_TIER } from "@/components/reflex-values"
import type { DojoProps } from "@/hooks/useDojo"

// Pulse shadow colors per reflex tier — matches the REFLEX_TIER hex colors at ~65% alpha
const TIER_PULSE: Record<string, string> = {
  CRITICAL: 'rgba(239, 68, 68, 0.65)',
  HIGH:     'rgba(251, 146, 60, 0.65)',
  MEDIUM:   'rgba(250, 204, 21, 0.55)',
  LOW:      'rgba(255, 255, 255, 0.25)',
  INFO:     'rgba(255, 255, 255, 0.2)',
}

export type ReflexProps = {
  dismissed: Set<string>
  onDismiss: (id: string) => void
  onSelect: (r: Reflex) => void
  dojoProps?: DojoProps
  /** Reflex IDs whose autonomous remediation hit the loop-breaker — needs human review */
  stuckReflexIds?: Set<string>
  /** Pillar that most recently received an auto-remediation run from the Dojo engine */
  lastAutoRemediationPillar?: string | null
  /** SLO-breach triage (merged from the former standalone NeedsAttention section): the count +
   *  all-clear state are folded into this panel's header; the per-metric chips are dropped because
   *  each breaching metric already appears as a reflex card below. */
  na?: NeedsAttention
}

function ReflexDeck({ group, items, onSelect, onDismiss, dojoProps, stuckReflexIds }: {
  group: string; items: Reflex[]; onSelect: (r: Reflex) => void; onDismiss: (id: string) => void; dojoProps?: DojoProps; stuckReflexIds?: Set<string>
}) {
  const [top, setTop] = useState(0)
  const n = items.length
  // Clamp derived position when the underlying list shrinks (e.g. after a
  // dismiss) — deriving in render avoids a setState-in-effect cascade.
  const idx = n > 0 ? Math.min(top, n - 1) % n : 0
  const r = items[idx]
  const t = REFLEX_TIER[r.tier] ?? REFLEX_TIER.INFO

  const isActiveExec = dojoProps?.execCommand === r.command && r.command != null
  const execRunning = isActiveExec && dojoProps?.execStatus === 'running'
  const execDone    = isActiveExec && dojoProps?.execStatus === 'done'
  const execError   = isActiveExec && dojoProps?.execStatus === 'error'
  // ReflexEngine is autonomously handling this reflex (bridged from ronin mode or direct watch)
  const isRoninHandling = dojoProps?.activeReflexIds?.has(r.id) ?? false
  const pendingApproval = dojoProps?.reflexPendingApprovals?.get(r.id) ?? null
  const roninOutput = dojoProps?.reflexOutput?.[r.id] ?? []
  // Autonomous remediation was attempted but hit the loop-breaker — human must investigate
  const isStuck = stuckReflexIds?.has(r.id) ?? false
  const behind = Math.min(n - 1, 2)
  const OFF = 7
  const RESERVE = 2 * OFF

  return (
    <div style={{ position: "relative", marginBottom: RESERVE, marginRight: RESERVE }}>
      {Array.from({ length: behind }).map((_, k) => {
        const i = behind - 1 - k
        const behindIdx = (idx + i + 1) % n
        const bt = REFLEX_TIER[items[behindIdx].tier] ?? REFLEX_TIER.INFO
        const d = (i + 1) * OFF
        return (
          <div key={items[behindIdx].id} style={{
            position: "absolute", inset: 0, transform: `translate(${d}px, ${d}px)`,
            background: bt.bg, border: `1px solid ${bt.color}`, borderRadius: 12,
            zIndex: 0,
          }} />
        )
      })}
      <div onClick={() => onSelect(r)}
        className={isRoninHandling ? 'metric-graded' : undefined}
        style={{
          position: "relative", zIndex: 1, cursor: "pointer",
          background: t.bg,
          border: `1px solid ${t.color}`,
          borderRadius: 12, padding: "10px 12px",
          boxShadow: behind ? "0 4px 14px rgba(0,0,0,0.5)" : undefined,
          minHeight: 132, display: "flex", flexDirection: "column",
          ...(isRoninHandling ? { '--pulse': TIER_PULSE[r.tier] ?? 'rgba(255,255,255,0.3)' } as CSSProperties : {}),
        }}>
        {/* flexWrap: chip overflow (tier+group+ronin+stuck+approval+stack+status+dismiss)
            must wrap to a second line, not overflow the card (2026-07-19 layout sweep). */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 5, flexWrap: "wrap", rowGap: 4 }}>
          <span className="mono" style={{ fontSize: "var(--text-caption)", fontWeight: 700, color: t.color, border: `1px solid ${t.color}`, borderRadius: 4, padding: "1px 5px" }}>{r.tier}</span>
          <span className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)", textTransform: "uppercase", letterSpacing: 0.5 }}>{group}</span>
          {isRoninHandling && (
            <span style={{ display: "flex", alignItems: "center", gap: 3, fontSize: "var(--text-caption)", color: t.color, background: `${t.color}22`, border: `1px solid ${t.color}66`, borderRadius: 4, padding: "1px 5px" }}
              className="mono">
              <Swords size={8} /> ronin handling
            </span>
          )}
          {isStuck && (
            <span title="Auto-remediation was attempted but did not improve this metric. Scroll to the Remediation Efficacy report for details and next steps."
              style={{ display: "flex", alignItems: "center", gap: 3, fontSize: "var(--text-caption)", color: "rgba(251,146,60,0.95)", background: "rgba(251,146,60,0.1)", border: "1px solid rgba(251,146,60,0.45)", borderRadius: 4, padding: "1px 5px" }}
              className="mono">
              <AlertTriangle size={7} /> investigate
            </span>
          )}
          {r.stuck && (
            <span title="Autonomous remediation is frozen for this reflex — loop-breaker tripped. Human intervention required."
              style={{ display: "flex", alignItems: "center", gap: 3, fontSize: "var(--text-caption)", color: "rgba(148,163,184,0.9)", background: "rgba(148,163,184,0.08)", border: "1px solid rgba(148,163,184,0.3)", borderRadius: 4, padding: "1px 5px" }}
              className="mono">
              ⏸ stuck
            </span>
          )}
          {pendingApproval && (
            <button
              onClick={(e) => { e.stopPropagation(); dojoProps!.cancelReflex(pendingApproval.cancelKey) }}
              title={`ReflexEngine is awaiting approval to run: ${pendingApproval.command}\nClick to cancel.`}
              style={{ display: "flex", alignItems: "center", gap: 3, fontSize: "var(--text-caption)", color: "#facc15", background: "rgba(250,204,21,0.1)", border: "1px solid rgba(250,204,21,0.45)", borderRadius: 4, padding: "1px 5px", cursor: "pointer" }}
              className="mono">
              ⏳ approval pending — cancel?
            </button>
          )}
          {n > 1 && (
            <button onClick={(e) => { e.stopPropagation(); setTop((idx + 1) % n) }} className="mono"
              title="next reflex in stack"
              style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)", background: "rgba(255,255,255,0.05)", border: "1px solid var(--card-border)", borderRadius: 5, padding: "1px 5px", cursor: "pointer" }}>
              ↻ {idx + 1}/{n}
            </button>
          )}
          <span className="mono" style={{ marginLeft: "auto", fontSize: "var(--text-caption)", color: r.source === "metric" ? t.color : "var(--muted-foreground)" }}>{r.source === "metric" ? "● live" : r.status}</span>
          <button onClick={(e) => { e.stopPropagation(); onDismiss(r.id) }} title="dismiss reflex"
            style={{ display: "flex", alignItems: "center", gap: 3, background: "rgba(255,255,255,0.06)", border: "1px solid var(--card-border)", borderRadius: 5, cursor: "pointer", color: "var(--muted-foreground)", padding: "1px 5px", fontSize: "var(--text-caption)" }}
            className="mono"
            onMouseEnter={(e) => { e.currentTarget.style.color = t.color; e.currentTarget.style.borderColor = t.color }}
            onMouseLeave={(e) => { e.currentTarget.style.color = "var(--muted-foreground)"; e.currentTarget.style.borderColor = "var(--card-border)" }}>
            <X size={10} /> dismiss
          </button>
        </div>
        <div style={{
          fontSize: "0.72rem", color: "rgba(255,255,255,0.85)", lineHeight: 1.4,
          display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden",
        }}>{r.message}</div>
        {r.trigger && <div className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)", marginTop: 3, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>trigger: {r.trigger}</div>}
        {r.command && (r.auto_remediable === false ? (
          // Advisory metric (auto_remediable:False): the routed skill is preventive / circular /
          // wrong-domain and won't move the metric, so never offer a run button — just label it.
          <div className="mono"
            title="Advisory — this metric is not auto-remediable (the routed skill can't move it). See the Remediation panel for what to do manually."
            style={{ marginTop: "auto", fontSize: "var(--text-caption)", color: "var(--muted-foreground)",
              background: "rgba(255,255,255,0.03)", border: "1px dashed var(--card-border)", borderRadius: 7,
              padding: "4px 8px", textAlign: "left", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            advisory — no auto-fix
          </div>
        ) : (
          <button
            onClick={(e: MouseEvent) => {
              e.stopPropagation()
              if (dojoProps && !execRunning && !isRoninHandling) dojoProps.exec(r.command!, r.scope)
            }}
            disabled={execRunning}
            className="mono"
            title={isRoninHandling ? "Ronin mode is already addressing this — manual execution locked" : undefined}
            style={{
              marginTop: "auto",
              fontSize: "var(--text-caption)",
              color: isRoninHandling
                ? `${t.color}77`
                : execRunning ? "rgba(255,255,255,0.5)"
                : execDone ? "#4ade80"
                : execError ? "var(--sword)"
                : t.color,
              background: isRoninHandling ? `${t.color}0a` : execRunning ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.3)",
              border: `1px solid ${isRoninHandling ? `${t.color}2a` : execRunning ? "rgba(255,255,255,0.15)" : execDone ? "#4ade8055" : execError ? "var(--sword)" : t.color}`,
              borderRadius: 7, padding: "4px 8px",
              cursor: isRoninHandling ? "default" : execRunning ? "not-allowed" : "pointer",
              width: "100%", textAlign: "left", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
            }}
          >
            {isRoninHandling
              ? `⚔ ronin mode is handling this…`
              : execRunning ? `⏳ running…`
              : execDone ? `✓ done`
              : execError ? `✗ failed — retry?`
              : `⚡ ${r.command}`}
          </button>
        ))}
        {/* Streaming output from the ReflexEngine's autonomous execution */}
        {isRoninHandling && roninOutput.length > 0 && (
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              marginTop: 6,
              background: "rgba(0,0,0,0.35)",
              border: `1px solid ${t.color}22`,
              borderRadius: 6,
              padding: "5px 8px",
              maxHeight: 80,
              overflowY: "auto",
            }}
          >
            {roninOutput.slice(-8).map((line, i) => (
              <div key={i} style={{ fontSize: "var(--text-caption)", color: t.color, opacity: 0.55, lineHeight: 1.6, fontFamily: "JetBrains Mono, monospace" }}>
                {line}
              </div>
            ))}
          </div>
        )}
        {/* Streaming output terminal — only shown for the active exec on this card */}
        {isActiveExec && dojoProps!.execOutput.length > 0 && (
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              marginTop: 6,
              background: "rgba(0,0,0,0.35)",
              border: `1px solid ${t.color}22`,
              borderRadius: 6,
              padding: "5px 8px",
              maxHeight: 80,
              overflowY: "auto",
            }}
          >
            {dojoProps!.execOutput.slice(-8).map((line, i) => (
              <div key={i} style={{ fontSize: "var(--text-caption)", color: t.color, opacity: 0.55, lineHeight: 1.6, fontFamily: "JetBrains Mono, monospace" }}>
                {line}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export function ReflexPanel({ reflexes, dismissed, onDismiss, onSelect, dojoProps, stuckReflexIds, lastAutoRemediationPillar, na }: { reflexes: Reflex[] } & ReflexProps) {
  // Dedupe by id — reflex ids are React keys; a transient backend duplicate
  // (e.g. mid-regeneration payload) must not break child identity.
  const uniq = [...new Map((reflexes ?? []).map((r) => [r.id, r])).values()]
  const live = uniq.filter((r) => !dismissed.has(r.id))
  // Render even with zero reflexes when triage data is present, so the "All SLOs met" success
  // state (folded in from the former NeedsAttention section) still shows. Only bail when there
  // is genuinely nothing to say (no reflexes AND no triage data = loading).
  if (!live.length && !na) return null
  const crit = live.filter((r) => r.tier === "CRITICAL").length
  const allClear = na != null && na.count === 0
  const groups: { key: string; items: Reflex[] }[] = []
  for (const r of live) {
    const gk = r.source === "metric" ? r.category : "System reflexes"
    let g = groups.find((x) => x.key === gk)
    if (!g) { g = { key: gk, items: [] }; groups.push(g) }
    g.items.push(r)
  }
  const accent = crit ? "#ef4444" : allClear ? "#4ade80" : "var(--bow)"

  return (
    <div className="glass page-enter" style={{ borderRadius: 20, padding: "20px 22px", marginBottom: 26, borderTop: `2px solid ${accent}` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: live.length ? 14 : 0, flexWrap: "wrap" }}>
        <Zap size={18} style={{ color: accent }} />
        <h2 style={{ fontSize: "1.05rem", margin: 0, letterSpacing: 2, textTransform: "uppercase" }}>Reflexes</h2>
        {/* SLO triage, folded in from the former NeedsAttention strip: count when breaching,
            explicit success state when clear. The per-metric chips are gone — each breaching
            metric is already a reflex card below. */}
        {na != null && (na.count === 0 ? (
          <span className="mono" style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: "var(--text-caption)", color: "#4ade80", background: "rgba(74,222,128,0.08)", border: "1px solid rgba(74,222,128,0.3)", borderRadius: 6, padding: "2px 8px" }}>
            <span aria-hidden style={{ fontWeight: 700 }}>✓</span> All SLOs met
          </span>
        ) : (
          <span className="mono" style={{ fontSize: "var(--text-caption)", fontWeight: 700, color: "var(--sword)", letterSpacing: 0.5 }}>{na.count} need attention</span>
        ))}
        {live.length > 0 && (
          <span className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)" }}>{live.length} active · {crit} critical · σ from mean</span>
        )}
        {lastAutoRemediationPillar && (
          <span className="mono" style={{ marginLeft: "auto", fontSize: "var(--text-caption)", color: "#4ade80", background: "rgba(74,222,128,0.08)", border: "1px solid rgba(74,222,128,0.3)", borderRadius: 4, padding: "1px 6px" }}>
            ✓ auto-remediation ran · {lastAutoRemediationPillar}
          </span>
        )}
      </div>
      {live.length > 0 && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(330px, 1fr))", gap: 14, alignItems: "start" }}>
          {groups.map((g) => (
            <ReflexDeck key={g.key} group={g.key} items={g.items} onSelect={onSelect} onDismiss={onDismiss} dojoProps={dojoProps} stuckReflexIds={stuckReflexIds} />
          ))}
        </div>
      )}
    </div>
  )
}
