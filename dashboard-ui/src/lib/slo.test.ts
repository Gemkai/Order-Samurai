import { describe, it, expect } from "vitest"
import { metricStatus, needsAttention, type SloStatus } from "./slo"
import type { MetricEnvelope } from "@/types"

const env = (e: Partial<MetricEnvelope>): MetricEnvelope => ({
  val: "0", delta: "0", trend: "neutral", history: [], is_percent: false,
  is_count: false, is_simulated: false, tier: "DERIVED", timestamp: "", ...e,
})
const rule = { dir: "lower" as const, warn: 2, fail: 5, weight: 1, per: null }

describe("metricStatus", () => {
  it("reads the server-set status verbatim", () => {
    const cases: [MetricEnvelope["status"], SloStatus][] = [["OK", "OK"], ["WARN", "WARN"], ["FAIL", "FAIL"]]
    for (const [s, want] of cases) expect(metricStatus(env({ status: s, rule }))).toBe(want)
  })
  it("simulated → NODATA even with a status", () => {
    expect(metricStatus(env({ is_simulated: true, status: "OK", rule }))).toBe("NODATA")
  })
  it("no rule → INFO (informational / no-target, never invented)", () => {
    expect(metricStatus(env({}))).toBe("INFO")
  })
  it("rule present but no server status → NODATA (no false 0)", () => {
    expect(metricStatus(env({ rule }))).toBe("NODATA")
  })
})

describe("needsAttention", () => {
  it("true only for WARN and FAIL", () => {
    expect(needsAttention(env({ status: "FAIL", rule }))).toBe(true)
    expect(needsAttention(env({ status: "WARN", rule }))).toBe(true)
    expect(needsAttention(env({ status: "OK", rule }))).toBe(false)
    expect(needsAttention(env({}))).toBe(false)              // INFO
    expect(needsAttention(env({ is_simulated: true }))).toBe(false)  // NODATA
  })
})
