import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it, vi } from "vitest"

import { ReflexPanel } from "@/components/ReflexList"
import { PILLARS, type Reflex } from "@/types"


const reflex: Reflex = {
  id: "metric:arts:Doc_Parity_Issues",
  tier: "CRITICAL",
  category: "Arts pillar",
  source: "metric",
  trigger: "past the 1 limit",
  target: "docs",
  message: "Doc Parity Issues is at 4.",
  command: "/wiki",
  last_fired: null,
  status: "active",
  scope: "docs",
}


function renderReflexPanel(isDemo: boolean) {
  return renderToStaticMarkup(
    createElement(ReflexPanel, {
      reflexes: [reflex],
      dismissed: new Set<string>(),
      onDismiss: vi.fn(),
      onSelect: vi.fn(),
      isDemo,
    }),
  )
}


describe("public demo labels", () => {
  it("labels metric reflexes as sample data, never live data, in demo mode", () => {
    const html = renderReflexPanel(true)

    expect(html.toLowerCase()).toContain("sample")
    expect(html.toLowerCase()).not.toContain("live")
  })

  it("retains the live label outside demo mode", () => {
    expect(renderReflexPanel(false).toLowerCase()).toContain("live")
  })

  it("uses the measured Craft Improvements metric for the Arts hero", () => {
    const arts = PILLARS.find((pillar) => pillar.key === "arts")

    expect(arts?.headline).toBe("Craft_Improvements")
    expect(arts?.headlineLabel).toBe("Craft Improvements")
  })
})
