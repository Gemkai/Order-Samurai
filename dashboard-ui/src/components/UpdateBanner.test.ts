import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

import { UPDATE_COMMAND, UpdateBanner } from "@/components/UpdateBanner"

const render = (version: Parameters<typeof UpdateBanner>[0]["version"], isDemo = false) =>
  renderToStaticMarkup(createElement(UpdateBanner, { version, isDemo }))

describe("update-available banner", () => {
  it("shows both versions and a copy button for the update command when a newer build ships", () => {
    const html = render({ current_version: "2.2.1", latest_version: "2.2.2", update_available: true })
    expect(html).toContain("UPDATE AVAILABLE")
    expect(html).toContain("v2.2.2")
    expect(html).toContain("v2.2.1")
    expect(html).toContain(UPDATE_COMMAND)
    expect(html).toMatch(/<button[^>]*>Copy update command<\/button>/)
  })

  it("uses the public installer command", () => {
    expect(UPDATE_COMMAND).toBe("curl -fsSL https://www.ordersamurai.ai/install.sh | bash")
  })

  it("renders nothing when up to date, unknown (offline or an older payload), or in the demo", () => {
    expect(render({ current_version: "2.2.1", latest_version: "2.2.1", update_available: false })).toBe("")
    expect(render({ current_version: "2.2.1", latest_version: null, update_available: false })).toBe("")
    expect(render(undefined)).toBe("")
    expect(render({ current_version: "2.2.1", latest_version: "2.2.2", update_available: true }, true)).toBe("")
  })
})
