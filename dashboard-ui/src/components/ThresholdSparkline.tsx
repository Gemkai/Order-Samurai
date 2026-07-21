import { useMemo } from "react"
import { ParentSize } from "@visx/responsive"

interface Rule { dir: "lower" | "higher"; warn: number; fail: number }

// 30-day trend with the warn/fail threshold lines drawn INSIDE the chart, so distance-to-breach
// is visible at a glance (plan Phase 1 design requirement). The y-domain is widened to include
// the thresholds so the lines never fall off-canvas, even when the series sits far from them.
function Inner({ history, rule, color, width, height }: {
  history: number[]; rule: Rule; color: string; width: number; height: number
}) {
  const pad = { t: 5, r: 4, b: 5, l: 4 }
  const iw = width - pad.l - pad.r
  const ih = height - pad.t - pad.b

  const { lo, hi } = useMemo(() => {
    const vals = [...history, rule.warn, rule.fail]
    const min = Math.min(...vals), max = Math.max(...vals)
    const span = max - min || 1
    return { lo: min - span * 0.1, hi: max + span * 0.1 }
  }, [history, rule.warn, rule.fail])

  const x = (i: number) => pad.l + (history.length > 1 ? (i / (history.length - 1)) * iw : iw / 2)
  const y = (v: number) => pad.t + (hi === lo ? ih / 2 : (1 - (v - lo) / (hi - lo)) * ih)

  const line = history.map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ")
  const area = history.length
    ? `${line} L${x(history.length - 1).toFixed(1)},${(pad.t + ih).toFixed(1)} L${x(0).toFixed(1)},${(pad.t + ih).toFixed(1)} Z`
    : ""
  const last = history.length ? { x: x(history.length - 1), y: y(history[history.length - 1]) } : null
  const gid = useMemo(() => `tsl${Math.round(history.reduce((a, b) => a + b, history.length) * 100)}`, [history])
  return (
    <svg width={width} height={height} style={{ overflow: "visible" }}>
      <defs><linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stopColor={color} stopOpacity={0.35} /><stop offset="100%" stopColor={color} stopOpacity={0} />
      </linearGradient></defs>
      {/* fail line (red, denser dash) + warn line (amber) — drawn under the series */}
      <line x1={pad.l} x2={pad.l + iw} y1={y(rule.fail)} y2={y(rule.fail)} stroke="var(--sword)" strokeWidth={1} strokeDasharray="2,2" opacity={0.65} />
      <line x1={pad.l} x2={pad.l + iw} y1={y(rule.warn)} y2={y(rule.warn)} stroke="var(--bow)" strokeWidth={1} strokeDasharray="3,3" opacity={0.65} />
      {area && <path d={area} fill={`url(#${gid})`} />}
      {history.length > 1 && <path d={line} fill="none" stroke={color} strokeWidth={1.6} style={{ filter: `drop-shadow(0 0 2px ${color}66)` }} />}
      {last && <circle cx={last.x} cy={last.y} r={2.6} fill={color} style={{ filter: `drop-shadow(0 0 3px ${color})` }} />}
    </svg>
  )
}

export function ThresholdSparkline({ history, rule, color, height = 40 }: {
  history: number[]; rule: Rule; color: string; height?: number
}) {
  if (!history || history.length < 2) {
    return <span style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)" }}>no trend yet</span>
  }
  return (
    <div style={{ width: "100%", height }}>
      <ParentSize>{({ width }) => (width < 10 ? null : <Inner history={history} rule={rule} color={color} width={width} height={height} />)}</ParentSize>
    </div>
  )
}
