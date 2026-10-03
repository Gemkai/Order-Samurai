import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

import { RemediationPanel } from "./RemediationPanel"
import type { WIDPayload } from "@/types"
import payloadSource from "../../public/wid_payload.json?raw"


const payload = JSON.parse(payloadSource) as WIDPayload


describe("shipped demo repair history", () => {
  it("carries the complete no-data schema without false repair claims", () => {
    const eff = payload.remediation_efficacy

    expect(eff).toMatchObject({
      attempted: 0,
      completed: 0,
      applied: 0,
      improved: 0,
      regressed: 0,
      flat: 0,
      success_rate: null,
      by_skill: {},
      events: [],
      note: expect.any(String),
    })
    expect(Array.isArray(eff.events)).toBe(true)
  })

  it("renders the actual no-data payload as not evaluated", () => {
    const html = renderToStaticMarkup(
      createElement(RemediationPanel, { eff: payload.remediation_efficacy }),
    )

    expect(html).toContain("No repair attempts in this window — not evaluated.")
    expect(html).not.toContain("Success rate")
    expect(html).not.toContain(">Applied<")
  })
})
