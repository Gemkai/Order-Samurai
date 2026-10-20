import type { Request, RequestHandler, Response } from "express"
import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { beforeEach, describe, expect, it, vi } from "vitest"

const execFileMock = vi.hoisted(() => vi.fn())
// spawnSync backs resolvePythonBin's venv probe; a failed probe falls back to python3.
const spawnSyncMock = vi.hoisted(() => vi.fn(() => ({ status: 1 })))
vi.mock("node:child_process", () => ({ execFile: execFileMock, spawnSync: spawnSyncMock }))
vi.mock("child_process", () => ({ execFile: execFileMock, spawnSync: spawnSyncMock }))

type VersionModule = { createVersionHandler?: (root: string) => RequestHandler }

async function createHandler(): Promise<RequestHandler> {
  const mod: VersionModule = await import("./version.js").catch(() => ({}))
  expect(mod.createVersionHandler, "version.ts must export createVersionHandler").toBeTypeOf("function")
  return mod.createVersionHandler!("/repo-root")
}

function invoke(handler: RequestHandler) {
  return new Promise<{ status: number; body: unknown }>((resolve, reject) => {
    const response = {
      statusCode: 200,
      status(code: number) { this.statusCode = code; return this },
      json(body: unknown) { resolve({ status: this.statusCode, body }); return this },
    }
    handler({} as Request, response as unknown as Response, reject)
  })
}

beforeEach(() => {
  execFileMock.mockReset()
  vi.resetModules()
})

describe("API version handler", () => {
  it("is mounted as an intentionally public GET route", () => {
    const source = readFileSync(fileURLToPath(new URL("./server.ts", import.meta.url)), "utf8")
    expect(source).toMatch(/app\.get\(["']\/api\/version["']\s*,\s*createVersionHandler\(/)
    expect(source).toMatch(/intentionally public[^\n]*version strings/i)
  })

  it("returns validated CLI output and caches it for the handler lifetime", async () => {
    execFileMock.mockImplementation((_bin, _args, _options, callback) => callback(null, JSON.stringify({
      current_version: "2.3.0", latest_version: "2.4.0", update_available: true,
    }), ""))
    const handler = await createHandler()

    await expect(invoke(handler)).resolves.toEqual({ status: 200, body: {
      current_version: "2.3.0", latest_version: "2.4.0", update_available: true,
    } })
    await invoke(handler)
    expect(execFileMock).toHaveBeenCalledTimes(1)

    const [bin, args, options] = execFileMock.mock.calls[0]
    expect(String(bin)).toMatch(/python3?$/)
    expect(args).toEqual(["/repo-root/bin/samurai", "version", "--json"])
    expect(options).toMatchObject({ timeout: expect.any(Number) })
    expect(options.timeout).toBeGreaterThanOrEqual(1_000)
    expect(options.timeout).toBeLessThanOrEqual(8_000)
    expect(options.shell).not.toBe(true)
  })

  it("deduplicates concurrent subprocesses", async () => {
    let callback: ((error: Error | null, stdout: string, stderr: string) => void) | undefined
    execFileMock.mockImplementation((_bin, _args, _options, cb) => { callback = cb })
    const handler = await createHandler()

    const first = invoke(handler)
    const second = invoke(handler)
    expect(execFileMock).toHaveBeenCalledTimes(1)
    callback!(null, '{"current_version":"2.3.0","latest_version":null,"update_available":false}', "")
    await expect(Promise.all([first, second])).resolves.toEqual([
      { status: 200, body: { current_version: "2.3.0", latest_version: null, update_available: false } },
      { status: 200, body: { current_version: "2.3.0", latest_version: null, update_available: false } },
    ])
  })

  it.each([
    ["process failure", new Error("spawn failed"), ""],
    ["invalid JSON", null, "{"],
    ["invalid shape", null, '{"current_version":"2.3.0","latest_version":[],"update_available":false}'],
  ])("returns the fail-silent shape on %s", async (_label, error, stdout) => {
    execFileMock.mockImplementation((_bin, _args, _options, callback) => callback(error, stdout, ""))
    const handler = await createHandler()

    await expect(invoke(handler)).resolves.toEqual({ status: 200, body: {
      current_version: null,
      latest_version: null,
      update_available: false,
    } })
  })

  it("re-checks after the cache expires: 6 h after a result, 10 min after a failure", async () => {
    const now = vi.spyOn(Date, "now").mockReturnValue(1_000_000)
    execFileMock.mockImplementationOnce((_b, _a, _o, callback) => callback(new Error("offline"), "", ""))
    execFileMock.mockImplementation((_b, _a, _o, callback) => callback(null, JSON.stringify({
      current_version: "2.4.0", latest_version: "2.5.0", update_available: true,
    }), ""))
    const handler = await createHandler()
    await invoke(handler)
    now.mockReturnValue(1_000_000 + 9 * 60_000)
    await invoke(handler)
    expect(execFileMock).toHaveBeenCalledTimes(1)
    now.mockReturnValue(1_000_000 + 11 * 60_000)
    await expect(invoke(handler)).resolves.toMatchObject({ body: { latest_version: "2.5.0" } })
    expect(execFileMock).toHaveBeenCalledTimes(2)
    now.mockReturnValue(1_000_000 + 11 * 60_000 + 5 * 3_600_000)
    await invoke(handler)
    expect(execFileMock).toHaveBeenCalledTimes(2)
    now.mockReturnValue(1_000_000 + 11 * 60_000 + 6 * 3_600_000 + 1)
    await invoke(handler)
    expect(execFileMock).toHaveBeenCalledTimes(3)
    now.mockRestore()
  })
})
