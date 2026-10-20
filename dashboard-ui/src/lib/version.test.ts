import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { afterEach, describe, expect, it, vi } from "vitest"

import { UPDATE_COMMAND, UpdateBanner } from "@/components/UpdateBanner"
import type { VersionStatus } from "@/types"

type VersionModule = {
  fetchVersionStatus?: () => Promise<VersionStatus | undefined>
  resolveVersionStatus?: (live: VersionStatus | undefined, payload: VersionStatus | undefined) => VersionStatus | undefined
}

async function loadVersionModule(): Promise<VersionModule> {
  return import("./version").catch(() => ({}))
}

const current: VersionStatus = {
  current_version: "2.3.0",
  latest_version: "2.3.0",
  update_available: false,
}
const update: VersionStatus = {
  current_version: "2.3.0",
  latest_version: "2.4.0",
  update_available: true,
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe("dashboard live version status", () => {
  it("fetches and accepts the exact version-status shape", async () => {
    const mod = await loadVersionModule()
    expect(mod.fetchVersionStatus, "version.ts must export fetchVersionStatus").toBeTypeOf("function")
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => update }))

    await expect(mod.fetchVersionStatus!()).resolves.toEqual(update)
    expect(fetch).toHaveBeenCalledWith("/api/version", expect.anything())
  })

  it.each([
    ["malformed fields", { current_version: 23, latest_version: "2.4.0", update_available: true }],
    ["unknown versions", { current_version: null, latest_version: null, update_available: false }],
  ])("treats %s as unavailable", async (_label, body) => {
    const mod = await loadVersionModule()
    expect(mod.fetchVersionStatus, "version.ts must export fetchVersionStatus").toBeTypeOf("function")
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => body }))

    await expect(mod.fetchVersionStatus!()).resolves.toBeUndefined()
  })

  it("fails silently when the request or response parsing fails", async () => {
    const mod = await loadVersionModule()
    expect(mod.fetchVersionStatus, "version.ts must export fetchVersionStatus").toBeTypeOf("function")
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")))

    await expect(mod.fetchVersionStatus!()).resolves.toBeUndefined()
  })

  it("prefers a known live result and falls back to the payload when live is unavailable", async () => {
    const mod = await loadVersionModule()
    expect(mod.resolveVersionStatus, "version.ts must export resolveVersionStatus").toBeTypeOf("function")

    expect(mod.resolveVersionStatus!(update, current)).toEqual(update)
    expect(mod.resolveVersionStatus!(undefined, current)).toEqual(current)
    expect(mod.resolveVersionStatus!({ current_version: null, latest_version: null, update_available: false }, current)).toEqual(current)
  })

  it("lets a live-only update render, while demo mode stays hidden and the command remains constant", async () => {
    const mod = await loadVersionModule()
    expect(mod.resolveVersionStatus, "version.ts must export resolveVersionStatus").toBeTypeOf("function")
    const resolved = mod.resolveVersionStatus!(update, undefined)

    const liveHtml = renderToStaticMarkup(createElement(UpdateBanner, { version: resolved, isDemo: false }))
    expect(liveHtml).toContain("UPDATE AVAILABLE")
    expect(liveHtml).toContain(UPDATE_COMMAND)
    expect(UPDATE_COMMAND).toBe("curl -fsSL https://www.ordersamurai.ai/install.sh | bash")
    expect(renderToStaticMarkup(createElement(UpdateBanner, { version: resolved, isDemo: true }))).toBe("")
  })
})
