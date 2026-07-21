import { motion } from "motion/react"

export interface RingScore {
  label: string
  value: number // 0–100
  color: string
}

/** Concentric multi-ring chart — one ring per pillar score (itemized). */
export function ProjectRings({ rings, size = 130 }: { rings: RingScore[]; size?: number }) {
  const cx = size / 2
  const cy = size / 2
  const sw = 8
  const gap = 4
  return (
    <svg width={size} height={size} style={{ overflow: "visible" }}>
      {rings.map((r, i) => {
        const radius = size / 2 - sw / 2 - i * (sw + gap)
        if (radius < sw) return null
        const c = 2 * Math.PI * radius
        const frac = Math.max(0, Math.min(100, r.value)) / 100
        return (
          <g key={r.label} transform={`rotate(-90 ${cx} ${cy})`}>
            <circle cx={cx} cy={cy} r={radius} fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth={sw} />
            <motion.circle
              cx={cx} cy={cy} r={radius} fill="none" stroke={r.color} strokeWidth={sw} strokeLinecap="round"
              strokeDasharray={c}
              initial={{ strokeDashoffset: c }} animate={{ strokeDashoffset: c - frac * c }}
              transition={{ duration: 1.1, delay: i * 0.1, ease: "easeOut" }}
              style={{ filter: `drop-shadow(0 0 4px ${r.color}88)` }} />
          </g>
        )
      })}
    </svg>
  )
}

export interface RadarAxis {
  label: string
  value: number // 0–100
  color: string
}

interface RadarChartProps {
  axes: RadarAxis[]
  size?: number
  stroke: string // polygon stroke/fill accent
}

/** Lightweight SVG radar (spider) chart — N axes, values normalized 0–100. */
export function RadarChart({ axes, size = 240, stroke }: RadarChartProps) {
  const cx = size / 2
  const cy = size / 2
  const r = size / 2 - 34
  const n = axes.length
  if (n < 3) return null

  const angle = (i: number) => (Math.PI * 2 * i) / n - Math.PI / 2
  const point = (i: number, frac: number) => {
    const a = angle(i)
    return [cx + Math.cos(a) * r * frac, cy + Math.sin(a) * r * frac] as const
  }

  const rings = [0.25, 0.5, 0.75, 1]
  const polygon = (frac: (i: number) => number) =>
    axes.map((_, i) => point(i, frac(i)).join(",")).join(" ")

  const valuePoly = polygon((i) => Math.max(0, Math.min(100, axes[i].value)) / 100)

  return (
    <svg width={size} height={size} style={{ overflow: "visible" }}>
      {/* grid rings */}
      {rings.map((rf) => (
        <polygon key={rf} points={polygon(() => rf)} fill="none" stroke="rgba(255,255,255,0.07)" strokeWidth={1} />
      ))}
      {/* spokes + labels */}
      {axes.map((ax, i) => {
        const [x, y] = point(i, 1)
        const [lx, ly] = point(i, 1.18)
        return (
          <g key={ax.label}>
            <line x1={cx} y1={cy} x2={x} y2={y} stroke="rgba(255,255,255,0.07)" strokeWidth={1} />
            <text x={lx} y={ly} fill={ax.color} fontSize={11} fontFamily="monospace" fontWeight={700}
              textAnchor={lx < cx - 5 ? "end" : lx > cx + 5 ? "start" : "middle"}
              dominantBaseline="middle">
              {ax.label} {Math.round(ax.value)}
            </text>
          </g>
        )
      })}
      {/* value polygon */}
      <motion.polygon
        points={valuePoly} fill={stroke} fillOpacity={0.18} stroke={stroke} strokeWidth={2}
        initial={{ opacity: 0, scale: 0.85 }} animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.6, ease: "easeOut" }}
        style={{ transformOrigin: "center", filter: `drop-shadow(0 0 6px ${stroke}88)` }} />
      {/* vertices */}
      {axes.map((ax, i) => {
        const [x, y] = point(i, Math.max(0, Math.min(100, ax.value)) / 100)
        return <circle key={i} cx={x} cy={y} r={3} fill={ax.color} style={{ filter: `drop-shadow(0 0 3px ${ax.color})` }} />
      })}
    </svg>
  )
}
