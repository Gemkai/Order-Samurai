import { useMemo } from "react"
import { Group } from "@visx/group"
import { LinePath, AreaClosed } from "@visx/shape"
import { scaleLinear, scaleBand } from "@visx/scale"
import { curveMonotoneX } from "@visx/curve"
import { LinearGradient } from "@visx/gradient"
import { ParentSize } from "@visx/responsive"
import { motion } from "motion/react"

export type VizKind =
  | "gauge" | "ring" | "area" | "line" | "liveline"
  | "bars" | "candle" | "scatter" | "sankey"

interface MetricVizProps {
  kind: VizKind
  history: number[]
  /** current numeric value — used by gauge/ring (0–100 or ratio) */
  value?: number
  /** for ring: the max the value is a fraction of (default 100) */
  max?: number
  /** for sankey: parts-of-whole slices */
  slices?: { label: string; value: number; color: string }[]
  color: string
  height?: number
}

let gid = 0
const nextId = () => `mv${gid++}`

// ── Gauge: radial arc, percent-toward-ceiling ────────────────────────────────
function Gauge({ value, color, size, suffix = "%" }: { value: number; color: string; size: number; suffix?: string }) {
  const sw = 7
  const r = (size - sw) / 2
  const c = 2 * Math.PI * r
  const v = Math.max(0, Math.min(100, value))
  const offset = c - (v / 100) * c
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", width: "100%", height: size }}>
      <div style={{ position: "relative", width: size, height: size }}>
        <svg width={size} height={size} style={{ transform: "rotate(-90deg)" }}>
          <circle cx={size / 2} cy={size / 2} r={r} fill="transparent" stroke="rgba(255,255,255,0.08)" strokeWidth={sw} />
          <motion.circle cx={size / 2} cy={size / 2} r={r} fill="transparent" stroke={color} strokeWidth={sw}
            strokeDasharray={c} strokeLinecap="round"
            initial={{ strokeDashoffset: c }} animate={{ strokeDashoffset: offset }}
            transition={{ duration: 1.4, ease: "easeOut" }}
            style={{ filter: `drop-shadow(0 0 5px ${color}88)` }} />
        </svg>
        <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <span className="mono" style={{ fontSize: "0.7rem", fontWeight: 700, color }}>{v.toFixed(0)}{suffix}</span>
        </div>
      </div>
    </div>
  )
}

// ── Ring: concentric proportion (value of max), distinct from gauge ──────────
function RingViz({ value, max, color, size }: { value: number; max: number; color: string; size: number }) {
  const sw = 9
  const r = (size - sw) / 2
  const c = 2 * Math.PI * r
  const frac = max > 0 ? Math.max(0, Math.min(1, value / max)) : 0
  const offset = c - frac * c
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", width: "100%", height: size }}>
      <div style={{ position: "relative", width: size, height: size }}>
        <svg width={size} height={size} style={{ transform: "rotate(-90deg)" }}>
          <circle cx={size / 2} cy={size / 2} r={r} fill="transparent" stroke="rgba(255,255,255,0.06)" strokeWidth={sw} />
          <circle cx={size / 2} cy={size / 2} r={r - sw - 2} fill="transparent" stroke="rgba(255,255,255,0.05)" strokeWidth={2} />
          <motion.circle cx={size / 2} cy={size / 2} r={r} fill="transparent" stroke={color} strokeWidth={sw}
            strokeDasharray={c} strokeLinecap="round"
            initial={{ strokeDashoffset: c }} animate={{ strokeDashoffset: offset }}
            transition={{ duration: 1.4, ease: "easeOut" }}
            style={{ filter: `drop-shadow(0 0 6px ${color})` }} />
        </svg>
        <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <span className="mono" style={{ fontSize: "var(--text-caption)", fontWeight: 700, color }}>{(frac * 100).toFixed(0)}%</span>
        </div>
      </div>
    </div>
  )
}

// ── Area / Line / LiveLine shared ────────────────────────────────────────────
function TrendInner({ data, color, width, height, filled, live }: { data: number[]; color: string; width: number; height: number; filled: boolean; live: boolean }) {
  const m = { top: 6, right: 6, bottom: 6, left: 6 }
  const iw = width - m.left - m.right
  const ih = height - m.top - m.bottom
  const id = useMemo(() => nextId(), [])
  const xs = useMemo(() => scaleLinear<number>({ domain: [0, data.length - 1 || 1], range: [0, iw] }), [data, iw])
  const ys = useMemo(() => {
    if (!data.length) return scaleLinear<number>({ domain: [0, 10], range: [ih, 0] })
    const min = Math.min(...data), max = Math.max(...data), d = max - min
    return scaleLinear<number>({ domain: [d === 0 ? min - 1 : min - d * 0.15, d === 0 ? max + 1 : max + d * 0.15], range: [ih, 0] })
  }, [data, ih])
  const pts = useMemo(() => data.map((y, x) => ({ x, y })), [data])
  const last = pts[pts.length - 1]
  return (
    <svg width={width} height={height} style={{ overflow: "visible" }}>
      <defs><LinearGradient id={id} from={color} to={color} fromOpacity={0.4} toOpacity={0} /></defs>
      <Group left={m.left} top={m.top}>
        {filled && pts.length > 0 && (
          <AreaClosed data={pts} x={(d) => xs(d.x)} y={(d) => ys(d.y)} yScale={ys} fill={`url(#${id})`} curve={curveMonotoneX} />
        )}
        <LinePath data={pts} x={(d) => xs(d.x)} y={(d) => ys(d.y)} stroke={color} strokeWidth={2} curve={curveMonotoneX}
          strokeDasharray={live ? "5,4" : undefined} style={{ filter: `drop-shadow(0 0 3px ${color}66)` }} />
        {last && (
          <motion.circle cx={xs(last.x)} cy={ys(last.y)} r={3} fill={color}
            initial={{ scale: 0.8 }} animate={{ scale: live ? [0.8, 1.6, 0.8] : [0.8, 1.3, 0.8] }}
            transition={{ repeat: Infinity, duration: live ? 1.4 : 2.4, ease: "easeInOut" }}
            style={{ filter: `drop-shadow(0 0 4px ${color})` }} />
        )}
      </Group>
    </svg>
  )
}

// ── Bars: discrete recent buckets ────────────────────────────────────────────
function BarsInner({ data, color, width, height }: { data: number[]; color: string; width: number; height: number }) {
  const m = { top: 4, right: 2, bottom: 4, left: 2 }
  const iw = width - m.left - m.right
  const ih = height - m.top - m.bottom
  const xs = useMemo(() => scaleBand<number>({ domain: data.map((_, i) => i), range: [0, iw], padding: 0.3 }), [data, iw])
  const ys = useMemo(() => {
    const max = data.length ? Math.max(...data) : 10
    return scaleLinear<number>({ domain: [0, max <= 0 ? 10 : max * 1.1], range: [ih, 0] })
  }, [data, ih])
  return (
    <svg width={width} height={height} style={{ overflow: "visible" }}>
      <Group left={m.left} top={m.top}>
        {data.map((v, i) => {
          const bw = xs.bandwidth(), bx = xs(i), by = ys(v), bh = ih - by
          if (bw === undefined || bx === undefined) return null
          return (
            <motion.rect key={i} x={bx} y={by} width={bw} height={Math.max(0, bh)} fill={color} rx={2}
              initial={{ scaleY: 0 }} animate={{ scaleY: 1 }}
              transition={{ duration: 0.7, delay: i * 0.05, ease: "easeOut" }}
              style={{ transformBox: "fill-box", transformOrigin: "bottom", filter: `drop-shadow(0 0 2px ${color}55)` }} />
          )
        })}
      </Group>
    </svg>
  )
}

// ── Candle: open=prev, close=current per step; pillar color, up solid / down faded ──
function CandleInner({ data, color, width, height }: { data: number[]; color: string; width: number; height: number }) {
  const m = { top: 6, right: 2, bottom: 6, left: 2 }
  const iw = width - m.left - m.right
  const ih = height - m.top - m.bottom
  const candles = useMemo(() => data.slice(1).map((close, i) => ({ open: data[i], close })), [data])
  const ys = useMemo(() => {
    const all = data.length ? data : [0, 10]
    const min = Math.min(...all), max = Math.max(...all), d = max - min || 1
    return scaleLinear<number>({ domain: [min - d * 0.1, max + d * 0.1], range: [ih, 0] })
  }, [data, ih])
  const xs = useMemo(() => scaleBand<number>({ domain: candles.map((_, i) => i), range: [0, iw], padding: 0.4 }), [candles, iw])
  return (
    <svg width={width} height={height} style={{ overflow: "visible" }}>
      <Group left={m.left} top={m.top}>
        {candles.map((c, i) => {
          const up = c.close >= c.open
          const bx = xs(i), bw = xs.bandwidth()
          if (bx === undefined) return null
          const yo = ys(c.open), yc = ys(c.close)
          const top = Math.min(yo, yc), h = Math.max(2, Math.abs(yc - yo))
          const cx = bx + bw / 2
          // pillar color throughout; direction shown by fill density (up solid, down hollow/faded)
          return (
            <g key={i}>
              <line x1={cx} x2={cx} y1={top - 3} y2={top + h + 3} stroke={color} strokeWidth={1} opacity={0.55} />
              <motion.rect x={bx} y={top} width={bw} height={h} rx={1}
                fill={up ? color : "transparent"} stroke={color} strokeWidth={up ? 0 : 1.4} fillOpacity={up ? 1 : 0.25}
                initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.4, delay: i * 0.06 }}
                style={{ filter: `drop-shadow(0 0 2px ${color}66)` }} />
            </g>
          )
        })}
      </Group>
    </svg>
  )
}

// ── Scatter: discrete event points, no smoothing ─────────────────────────────
function ScatterInner({ data, color, width, height }: { data: number[]; color: string; width: number; height: number }) {
  const m = { top: 6, right: 8, bottom: 6, left: 8 }
  const iw = width - m.left - m.right
  const ih = height - m.top - m.bottom
  const xs = useMemo(() => scaleLinear<number>({ domain: [0, data.length - 1 || 1], range: [0, iw] }), [data, iw])
  const ys = useMemo(() => {
    const min = Math.min(...data), max = Math.max(...data), d = max - min || 1
    return scaleLinear<number>({ domain: [min - d * 0.2, max + d * 0.2], range: [ih, 0] })
  }, [data, ih])
  return (
    <svg width={width} height={height} style={{ overflow: "visible" }}>
      <Group left={m.left} top={m.top}>
        {data.map((v, i) => (
          <motion.circle key={i} cx={xs(i)} cy={ys(v)} r={3.2} fill={color}
            initial={{ scale: 0, opacity: 0 }} animate={{ scale: 1, opacity: 0.85 }}
            transition={{ duration: 0.4, delay: i * 0.05 }}
            style={{ filter: `drop-shadow(0 0 3px ${color})` }} />
        ))}
      </Group>
    </svg>
  )
}

// ── Sankey-lite: proportional horizontal flow bar of parts-of-whole ──────────
function SankeyInner({ slices, width, height }: { slices: { label: string; value: number; color: string }[]; width: number; height: number }) {
  const total = slices.reduce((s, x) => s + x.value, 0) || 1
  // cumulative x offsets with no render-time mutation (prefix sum via slice)
  const ws = slices.map((s) => (s.value / total) * width)
  const segs = slices.map((s, i) => ({ ...s, w: ws[i], x: ws.slice(0, i).reduce((a, b) => a + b, 0) }))
  return (
    <svg width={width} height={height} style={{ overflow: "visible" }}>
      {segs.map((s, i) => (
        <motion.rect key={i} x={s.x} y={height * 0.25} width={Math.max(0, s.w - 1)} height={height * 0.5} fill={s.color} rx={2}
          initial={{ scaleX: 0 }} animate={{ scaleX: 1 }} transition={{ duration: 0.6, delay: i * 0.08 }}
          style={{ transformBox: "fill-box", transformOrigin: "left", filter: `drop-shadow(0 0 2px ${s.color}66)` }} />
      ))}
    </svg>
  )
}

export function MetricViz({ kind, history, value, max = 100, slices, color, height = 40 }: MetricVizProps) {
  if (kind === "gauge") return <Gauge value={value ?? 0} color={color} size={height + 12} />
  if (kind === "ring") return <RingViz value={value ?? 0} max={max} color={color} size={height + 12} />
  if (kind === "sankey" && slices?.length) {
    return <div style={{ width: "100%", height }}><ParentSize>{({ width }) => width < 10 ? null : <SankeyInner slices={slices} width={width} height={height} />}</ParentSize></div>
  }

  if (!history || history.length < 2) {
    return <span style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)" }}>no trend yet</span>
  }

  return (
    <div style={{ width: "100%", height }}>
      <ParentSize>
        {({ width }) => {
          if (width < 10) return null
          if (kind === "bars") return <BarsInner data={history} color={color} width={width} height={height} />
          if (kind === "candle") return <CandleInner data={history} color={color} width={width} height={height} />
          if (kind === "scatter") return <ScatterInner data={history} color={color} width={width} height={height} />
          return <TrendInner data={history} color={color} width={width} height={height} filled={kind === "area"} live={kind === "liveline"} />
        }}
      </ParentSize>
    </div>
  )
}
