import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { expect, it, vi } from "vitest"

import type { WIDPayload } from "@/types"
import payloadSource from "../public/wid_payload.json?raw"


const payload = JSON.parse(payloadSource) as WIDPayload


it("renders the full dashboard from the shipped demo payload", async () => {
  const actualReact = await vi.importActual<typeof import("react")>("react")
  let stateCall = 0
  vi.doMock("react", () => ({
    ...actualReact,
    useState: <T,>(initial: T | (() => T)) => {
      stateCall += 1
      if (stateCall === 2) return [payload, vi.fn()] as const
      return actualReact.useState(initial)
    },
  }))

  const { default: App } = await import("./App")

  expect(() => renderToStaticMarkup(createElement(App))).not.toThrow()
})
