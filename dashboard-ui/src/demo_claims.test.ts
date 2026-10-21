import { readFileSync } from "node:fs"
import { resolve } from "node:path"
import { describe, expect, it } from "vitest"

import { HARNESS_CONFIG } from "@/App"

const read = (rel: string) => readFileSync(resolve(__dirname, rel), "utf8")
const app = read("App.tsx")
const landing = read("components/LandingPage.tsx")

describe("hosted demo claims", () => {
  it("header strip lists only telemetry platforms the aggregator can count", () => {
    const platforms = Object.keys(JSON.parse(read("../../agentica_core/platforms.json")).platforms)
    for (const h of HARNESS_CONFIG) expect(platforms).toContain(h.key)
    expect(HARNESS_CONFIG.map((h) => h.key)).toEqual(["claude", "codex"])
  })

  it("does not tease Pro views that do not ship", () => {
    for (const banned of ["NIST AI RMF", "OWASP", "EU AI Act", "FLEET AGGREGATION", "chain severed", "threshold_recalibrate", "ProLockedPanels", "REPAIR REVIEW"]) {
      expect(app).not.toContain(banned)
      expect(landing).not.toContain(banned)
    }
  })

  it("never labels synthetic sample figures as measured", () => {
    expect(app).not.toMatch(/\(MEASURED\)/)
    expect(landing).not.toMatch(/^\s*MEASURED\s*$/m)
  })

  it("keeps the demo out of the in-app marketing page", () => {
    expect(app).toContain('if (!isDemo) setMode("landing")')
  })
})
