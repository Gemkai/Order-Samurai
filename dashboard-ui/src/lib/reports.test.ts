import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it, vi } from "vitest"

import { isReportLocked, loadReportHtml, loadReportIndex, type ReportEntry } from "@/lib/reports"
import { ReportsLog } from "@/components/ReportsLog"

const audit: ReportEntry = { file: "audit__20261019T120000Z.md", kind: "audit", title: "Full Audit", generated: "2026-10-19T12:00:00Z" }
const weekly: ReportEntry = { file: "2026-W41__claude.md", kind: "weekly", title: "Weekly Report — claude", generated: "2026-10-12T09:00:00Z", week: "2026-W41", platform: "claude", records: 12 }

const respond = (body: unknown, ok = true, status = 200) =>
  vi.fn().mockResolvedValue({ ok, status, json: async () => body, text: async () => String(body) })

describe("loadReportIndex", () => {
  it("reads reports/index.json from the public dir, uncached", async () => {
    const fetcher = respond([audit, weekly])
    expect(await loadReportIndex(fetcher)).toEqual([audit, weekly])
    expect(fetcher).toHaveBeenCalledWith("reports/index.json", { cache: "no-store" })
  })

  it("is empty, not an error, when no audit has run yet", async () => {
    expect(await loadReportIndex(respond(null, false, 404))).toEqual([])
    expect(await loadReportIndex(vi.fn().mockRejectedValue(new Error("offline")))).toEqual([])
    expect(await loadReportIndex(respond({ not: "a list" }))).toEqual([])
  })

  it("drops rows in the pre-audit index format (v2.2.1 shipped one)", async () => {
    const legacy = [{ file: "2026-W29__claude.md", week: "2026-W29", platform: "claude", records: 465 }, audit]
    expect(await loadReportIndex(respond(legacy))).toEqual([audit])
  })

  it("drops entries whose file is not a plain .md name", async () => {
    const bad = [{ ...audit, file: "../../secrets.md" }, { ...audit, file: "x.html" }, weekly]
    expect(await loadReportIndex(respond(bad))).toEqual([weekly])
  })
})

describe("loadReportHtml", () => {
  it("fetches the markdown and renders it", async () => {
    const fetcher = respond("# Full Audit\n\nSessions scanned: **3**")
    const html = await loadReportHtml(audit.file, fetcher)
    expect(fetcher).toHaveBeenCalledWith(`reports/${audit.file}`, { cache: "no-store" })
    expect(html).toContain("<h1>Full Audit</h1>")
    expect(html).toContain("<strong>3</strong>")
  })

  it("refuses a path outside the reports dir", async () => {
    await expect(loadReportHtml("../wid_payload.json", respond(""))).rejects.toThrow()
  })
})

describe("free / pro lock", () => {
  it("free sees only the newest report; pro sees all", () => {
    expect(isReportLocked(0, false)).toBe(false)
    expect(isReportLocked(1, false)).toBe(true)
    expect(isReportLocked(1, true)).toBe(false)
  })
})

const render = (props: Partial<Parameters<typeof ReportsLog>[0]>) =>
  renderToStaticMarkup(createElement(ReportsLog, {
    entries: [audit, weekly], activeIdx: 0, html: "<h1>Full Audit</h1>", isPro: false, onSelect: vi.fn(), ...props,
  }))

describe("ReportsLog", () => {
  it("shows an honest empty state that names the command", () => {
    const html = render({ entries: [], html: "" })
    expect(html).toContain("No reports yet")
    expect(html).toContain("samurai audit")
    expect(html).not.toContain("Weekly Governance Dispatch")
  })

  it("lists real reports and renders the newest one unblurred on Free", () => {
    const html = render({})
    expect(html).toContain("Full Audit")
    expect(html).toContain("Weekly Report — claude")
    expect(html).toContain("2026-W41")
    expect(html).not.toContain("blur(6px)")
    expect(html).not.toContain("· PRO")
  })

  it("blurs an older report behind the Pro lock on Free, without its content", () => {
    const html = render({ activeIdx: 1, html: "<p>SECRET-HISTORY</p>" })
    expect(html).toContain("blur(6px)")
    expect(html).toContain("PAST REPORTS · PRO")
    expect(html).not.toContain("SECRET-HISTORY")
  })

  it("does not lock older reports on Pro", () => {
    const html = render({ activeIdx: 1, isPro: true })
    expect(html).not.toContain("blur(6px)")
    expect(html).not.toContain("🔒")
  })
})
