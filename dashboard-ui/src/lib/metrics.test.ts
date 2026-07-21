import { describe, it, expect } from "vitest"
import { num, volatility, vizScores, assignViz, ringMax, scoreMap, topMovers, platformMetricVal, reflexMetric, resolveHero } from "./metrics"
import type { FlatMetric } from "@/lib/data"
import type { WIDPayload, Pillars, Reflex } from "@/types"

const fm = (key: string, env: Partial<FlatMetric["env"]> = {}): FlatMetric => ({
  group: "G", key,
  env: { val: "0", delta: "0", trend: "neutral", history: [], is_percent: false,
         is_count: false, is_simulated: false, tier: "DERIVED", timestamp: "", ...env },
})

describe("num", () => {
  it("parses messy strings", () => {
    expect(num("1,234")).toBe(1234)
    expect(num("18.97")).toBeCloseTo(18.97)
    expect(num("-")).toBe(0)
  })
})

describe("volatility", () => {
  it("0 for flat or <2 points", () => {
    expect(volatility([5, 5, 5])).toBe(0)
    expect(volatility([5])).toBe(0)
  })
  it(">0 for varied series", () => {
    expect(volatility([10, 20, 30])).toBeGreaterThan(0)
  })
})

describe("vizScores", () => {
  it("scores percent/grade metrics toward gauge", () => {
    const s = vizScores(fm("Governance_Pass_Rate", { is_percent: true }))
    expect(s.gauge).toBeGreaterThan(s.bars)
  })
  it("scores mix metrics toward sankey", () => {
    expect(vizScores(fm("Model_Tier_Mix")).sankey).toBeGreaterThan(0)
  })
})

describe("assignViz diversifies", () => {
  it("avoids assigning every event-y metric the same chart", () => {
    const sword = ["Open_CVEs", "Gate_Fires", "Rule_Violations", "Boundary_Violations"]
      .map((k) => fm(k, { is_count: true }))
    const kinds = new Set([...assignViz(sword).values()])
    expect(kinds.size).toBeGreaterThan(1)   // not all collapsed to scatter
  })
})

describe("ringMax", () => {
  it("is current value vs historical peak", () => {
    expect(ringMax(fm("X", { val: "5", history: [2, 8, 3] }))).toBe(8)
  })
})

describe("scoreMap", () => {
  it("picks window vs lifetime", () => {
    const w = { worst: "PASS", passing: 1, graded: 1 }
    const l = { worst: "HIGH", passing: 2, graded: 3 }
    const p = { category_scores: { bow: { rollup: w } }, category_scores_lifetime: { bow: { rollup: l } } } as unknown as WIDPayload
    expect(scoreMap(p, "window").bow.rollup).toEqual(w)
    expect(scoreMap(p, "all").bow.rollup).toEqual(l)
  })
})

const emptyPillars = (): Pillars => ({ bow: {}, sword: {}, brush: {}, arts: {} })

describe("topMovers", () => {
  it("returns metrics sorted by absolute delta, biggest first", () => {
    const pillars: Pillars = {
      bow: { G: {
        Error_Rate: { ...fm("Error_Rate").env, delta: "+5", val: "5" },
        Latency_P50: { ...fm("Latency_P50").env, delta: "+1", val: "1" },
        Latency_P95: { ...fm("Latency_P95").env, delta: "-8", val: "8" },
      }},
      sword: {}, brush: {}, arts: {},
    }
    const movers = topMovers(pillars, "bow", 2)
    expect(movers).toHaveLength(2)
    expect(movers[0].key).toBe("Latency_P95")   // |−8| > |+5|
    expect(movers[1].key).toBe("Error_Rate")
  })

  it("excludes simulated metrics", () => {
    const pillars: Pillars = {
      bow: { G: {
        Error_Rate: { ...fm("Error_Rate").env, delta: "+99", val: "99", is_simulated: true },
      }},
      sword: {}, brush: {}, arts: {},
    }
    expect(topMovers(pillars, "bow")).toHaveLength(0)
  })

  it("excludes metrics with zero delta", () => {
    const pillars: Pillars = {
      bow: { G: { Error_Rate: { ...fm("Error_Rate").env, delta: "0", val: "5" } } },
      sword: {}, brush: {}, arts: {},
    }
    expect(topMovers(pillars, "bow")).toHaveLength(0)
  })
})

describe("platformMetricVal", () => {
  it("returns numeric value for a live metric", () => {
    const pillars: Pillars = {
      bow: { G: { Error_Rate: { ...fm("Error_Rate").env, val: "3.5" } } },
      sword: {}, brush: {}, arts: {},
    }
    expect(platformMetricVal(pillars, "Error_Rate")).toBeCloseTo(3.5)
  })

  it("returns 0 for a simulated metric", () => {
    const pillars: Pillars = {
      bow: { G: { Error_Rate: { ...fm("Error_Rate").env, val: "3.5", is_simulated: true } } },
      sword: {}, brush: {}, arts: {},
    }
    expect(platformMetricVal(pillars, "Error_Rate")).toBe(0)
  })

  it("returns 0 for missing metric key", () => {
    expect(platformMetricVal(emptyPillars(), "Nonexistent")).toBe(0)
  })
})

describe("reflexMetric", () => {
  it("returns metric and pillar color for a valid metric reflex", () => {
    const pillars: Pillars = {
      bow: { G: { Error_Rate: { ...fm("Error_Rate").env, val: "2" } } },
      sword: {}, brush: {}, arts: {},
    }
    const payload = { pillars } as unknown as WIDPayload
    const reflex = { id: "metric:bow:Error_Rate" } as Reflex
    const result = reflexMetric(payload, reflex)
    expect(result).not.toBeNull()
    expect(result!.metric.key).toBe("Error_Rate")
    expect(result!.color).toBe("var(--bow)")
  })

  it("returns null for non-metric reflex ids", () => {
    const payload = { pillars: emptyPillars() } as unknown as WIDPayload
    const reflex = { id: "nudge:some:nudge" } as Reflex
    expect(reflexMetric(payload, reflex)).toBeNull()
  })

  it("returns null when metric key not found in pillar", () => {
    const payload = { pillars: emptyPillars() } as unknown as WIDPayload
    const reflex = { id: "metric:bow:Nonexistent" } as Reflex
    expect(reflexMetric(payload, reflex)).toBeNull()
  })
})

describe("resolveHero data-gap gating", () => {
  const meta = {
    headline: "Estimated_Cost_Savings",
    headlineLabel: "Est. Cost Savings",
    headlineDesc: "savings estimate",
    fallback: { key: "Cost_Per_Task", label: "Cost per Task", desc: "measured cpt" },
  } as unknown as Parameters<typeof resolveHero>[1]
  const groupsWith = (savingsEnv: object) => ({
    "Token Efficiency": {
      Estimated_Cost_Savings: { val: "0.0", delta: "+0.0", trend: "neutral", history: [],
        is_percent: false, is_count: false, is_simulated: false, tier: "AUTO", timestamp: "", ...savingsEnv },
      Cost_Per_Task: { val: "8.4", delta: "0", trend: "neutral", history: [],
        is_percent: false, is_count: false, is_simulated: false, tier: "DERIVED", timestamp: "", calibrated: true },
    },
  })

  it("falls back to the measured metric when the headline has a data gap", () => {
    const hero = resolveHero(groupsWith({ calibrated: true, data_gap: true }) as unknown as Parameters<typeof resolveHero>[0], meta)
    expect(hero.fallbackActive).toBe(true)
    expect(hero.label).toBe("Cost per Task")
    expect(hero.val).toBe("8.4")
  })

  it("keeps the headline when calibrated with no data gap", () => {
    const hero = resolveHero(groupsWith({ val: "12.5", calibrated: true, data_gap: false }) as unknown as Parameters<typeof resolveHero>[0], meta)
    expect(hero.fallbackActive).toBe(false)
    expect(hero.label).toBe("Est. Cost Savings")
  })
})
