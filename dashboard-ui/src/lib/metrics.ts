// Pure metric/visualization helpers extracted from App.tsx (testable, no React).
import type { VizKind } from "@/components/MetricViz"
import type { PillarKey, WIDPayload, Pillars, Reflex, PillarMeta, Group } from "@/types"
import { flatten, type FlatMetric } from "@/lib/data"

export const num = (s: string): number => {
  const n = parseFloat(String(s).replace(/[^0-9.-]/g, ""))
  return Number.isFinite(n) ? n : 0
}

export interface HeroResolved {
  label: string
  desc: string
  val: string | null
  calibrated: boolean
  delta: number | null
  fallbackActive: boolean
  /** Live component breakdown for composite heroes (env.detail), when present. */
  detail?: string
}

/** Resolve a pillar's hero metric with calibration gating: the headline estimate
 *  holds the slot only when calibrated; until then the real measured fallback is
 *  shown. Keeps modeled numbers off the most prominent dashboard position. */
export function resolveHero(groups: Record<string, Group> | undefined, meta: PillarMeta): HeroResolved {
  const find = (key: string) => {
    for (const g of Object.values(groups ?? {})) if (g[key]) return g[key]
    return undefined
  }
  // Compact display string — always a string so ScoreNumber never appends "/100".
  const fmt = (raw: string): string => {
    if (!raw) return "0"
    if (/[h$kM%]/.test(raw)) return raw
    const n = parseFloat(String(raw).replace(/[^0-9.-]/g, ""))
    if (!Number.isFinite(n)) return String(raw)
    if (Math.abs(n) >= 10000) return `${Math.round(n / 1000)}k`
    if (Math.abs(n) >= 100) return String(Math.round(n))
    return String(+n.toFixed(Math.abs(n) >= 10 ? 1 : 2))
  }
  let env = meta.headline ? find(meta.headline) : undefined
  let label = meta.headlineLabel ?? meta.headline ?? ""
  let desc = meta.headlineDesc ?? ""
  let fallbackActive = false
  // Fall back to the measured metric when the headline estimate is either
  // uncalibrated OR has a data gap (a producer that never emitted this week —
  // e.g. zero mechanism_run events). A data gap is an absence of signal, not a
  // real reading, so it must not hold the hero slot as a confident value.
  const untrustworthy = env?.calibrated === false || env?.data_gap === true
  if (untrustworthy && meta.fallback) {
    const fb = find(meta.fallback.key)
    if (fb) { env = fb; label = meta.fallback.label; desc = meta.fallback.desc; fallbackActive = true }
  }
  if (!env) return { label, desc, val: null, calibrated: true, delta: null, fallbackActive }
  const d = parseFloat(env.delta)
  return {
    label, desc, val: fmt(env.val),
    calibrated: env.calibrated !== false,
    delta: isNaN(d) ? null : d,
    fallbackActive,
    detail: env.detail,
  }
}

// top trending metrics in a pillar (live, |delta|>0), biggest movers first
export function topMovers(pillars: Pillars, pk: PillarKey, n = 3): FlatMetric[] {
  return flatten(pillars, pk)
    .filter((m) => !m.env.is_simulated && m.env.delta && num(m.env.delta) !== 0)
    .sort((a, b) => Math.abs(num(b.env.delta)) - Math.abs(num(a.env.delta)))
    .slice(0, n)
}

// coefficient of variation of history — a volatility signal
export function volatility(h: number[]): number {
  if (!h || h.length < 2) return 0
  const mean = h.reduce((a, b) => a + b, 0) / h.length
  if (mean === 0) return 0
  const sd = Math.sqrt(h.reduce((a, b) => a + (b - mean) ** 2, 0) / h.length)
  return sd / Math.abs(mean)
}

// Preference scoring: each chart type scores against the metric's attributes.
export function vizScores(m: FlatMetric): Record<VizKind, number> {
  const k = m.key.toLowerCase()
  const env = m.env
  const cov = volatility(env.history || [])
  const s: Record<VizKind, number> = {
    gauge: 0, ring: 0, area: 0, line: 0, liveline: 0, bars: 0, candle: 0, scatter: 0, sankey: 0,
  }
  s.gauge += env.is_percent ? 3 : 0
  s.gauge += /\b(grade|score|pass_rate|integrity)\b/.test(k) ? 4 : 0
  s.ring += /(diversity|coverage|parity|revision|ratio|hygiene)/.test(k) ? 4 : 0
  s.area += /(latency|density|spend|cost|tokens?|throughput|turns)/.test(k) ? 3 : 0
  s.area += cov > 0 && cov < 0.15 ? 1 : 0
  s.liveline += /(tool_calls|throughput|process|frequency|rate)\b/.test(k) ? 2.5 : 0
  s.candle += cov >= 0.15 ? 3 : 0
  s.candle += /(latency|cost|spend|density|price|calls|spawn|count)/.test(k) ? 1 : 0
  s.scatter += /(violation|incident|failure|orphan|signal|cve|secret|fires|reaped|canary|drift)/.test(k) ? 3.5 : 0
  s.sankey += /(mix|distribution|breakdown)/.test(k) ? 6 : 0
  s.bars += env.is_count ? 1.5 : 0
  s.line += 0.5
  return s
}

// Pillar-level assignment: greedily pick the best-scoring kind per metric while
// penalizing kinds already used in this pillar, so a pillar shows real variety.
export function assignViz(metrics: FlatMetric[]): Map<string, VizKind> {
  const out = new Map<string, VizKind>()
  const used: Record<string, number> = {}
  const PENALTY = 1.6
  const order = metrics
    .map((m) => ({ m, s: vizScores(m) }))
    .sort((a, b) => Math.max(...Object.values(b.s)) - Math.max(...Object.values(a.s)))
  for (const { m, s } of order) {
    let best: VizKind = "line", bestVal = -Infinity
    for (const kind of Object.keys(s) as VizKind[]) {
      const adj = s[kind] - PENALTY * (used[kind] || 0)
      if (adj > bestVal) { bestVal = adj; best = kind }
    }
    used[best] = (used[best] || 0) + 1
    out.set(m.group + m.key, best)
  }
  return out
}

// ring needs a max; show current vs historical peak
export function ringMax(m: FlatMetric): number {
  const h = m.env.history || []
  return Math.max(num(m.env.val), ...h, 1)
}

// numeric value of a metric within one platform's pillars (scans all groups; 0 if absent)
export function platformMetricVal(pillars: Pillars, key: string): number {
  for (const groups of Object.values(pillars)) {
    for (const g of Object.values(groups)) {
      const env = g[key]
      if (env && !env.is_simulated) return num(env.val)
    }
  }
  return 0
}

// find the FlatMetric a metric-reflex points at (id = "metric:<pk>:<Metric_Name>")
export function reflexMetric(payload: WIDPayload, r: Reflex): { metric: FlatMetric; color: string } | null {
  const parts = r.id.split(":")
  if (parts[0] !== "metric") return null
  const pk = parts[1] as PillarKey
  const key = parts.slice(2).join(":")
  const m = flatten(payload.pillars, pk).find((x) => x.key === key)
  return m ? { metric: m, color: `var(--${pk})` } : null
}

export type ScoreScope = "window" | "all"
export const scoreMap = (p: WIDPayload, scope: ScoreScope) =>
  scope === "all" ? p.category_scores_lifetime : p.category_scores
