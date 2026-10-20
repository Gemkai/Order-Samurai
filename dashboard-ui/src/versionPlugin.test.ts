import { beforeEach, describe, expect, it, vi } from "vitest"

const execFileMock = vi.hoisted(() => vi.fn())
vi.mock("node:child_process", () => ({ execFile: execFileMock }))
vi.mock("child_process", () => ({ execFile: execFileMock }))

type Middleware = (
  req: { url?: string; method?: string },
  res: { statusCode: number; setHeader(name: string, value: unknown): void; end(body?: string): void },
  next: () => void,
) => void

async function loadPlugin() {
  const mod = await import("../versionPlugin").catch(() => ({} as { default?: () => unknown }))
  expect(mod.default, "versionPlugin.ts must default-export versionPlugin").toBeTypeOf("function")
  return mod.default!() as {
    configureServer?: (server: { middlewares: { use: (handler: Middleware) => void } }) => void
    configurePreviewServer?: (server: { middlewares: { use: (handler: Middleware) => void } }) => void
  }
}

function capture(register: ((server: { middlewares: { use: (handler: Middleware) => void } }) => void) | undefined): Middleware {
  expect(register, "plugin must register middleware").toBeTypeOf("function")
  let handler: Middleware | undefined
  register!({ middlewares: { use: (value) => { handler = value } } })
  expect(handler).toBeTypeOf("function")
  return handler!
}

function request(handler: Middleware, url = "/api/version", method = "GET") {
  return new Promise<{ status: number; headers: Record<string, unknown>; body: unknown; next: boolean }>((resolve) => {
    const headers: Record<string, unknown> = {}
    let next = false
    const res = {
      statusCode: 0,
      setHeader: (name: string, value: unknown) => { headers[name.toLowerCase()] = value },
      end: (body = "") => resolve({ status: res.statusCode, headers, body: body ? JSON.parse(String(body)) : undefined, next }),
    }
    handler({ url, method }, res, () => {
      next = true
      resolve({ status: res.statusCode, headers, body: undefined, next })
    })
  })
}

beforeEach(() => {
  execFileMock.mockReset()
  vi.resetModules()
})

describe("Vite version middleware", () => {
  it("is available to both the dev and preview servers", async () => {
    const plugin = await loadPlugin()
    expect(plugin.configureServer).toBeTypeOf("function")
    expect(plugin.configurePreviewServer).toBeTypeOf("function")
  })

  it("serves exact and query GET routes, caches success, and uses a bounded argument-list exec", async () => {
    execFileMock.mockImplementation((_bin, _args, _options, callback) => callback(null, JSON.stringify({
      current_version: "2.3.0", latest_version: "2.4.0", update_available: true,
    }), ""))
    const plugin = await loadPlugin()
    const handler = capture(plugin.configureServer)

    const first = await request(handler)
    const again = await request(handler, "/api/version?fresh=1")

    expect(first).toMatchObject({ status: 200, body: { current_version: "2.3.0", latest_version: "2.4.0", update_available: true }, next: false })
    expect(again.body).toEqual(first.body)
    expect(execFileMock).toHaveBeenCalledTimes(1)
    const [bin, args, options] = execFileMock.mock.calls[0]
    expect(String(bin)).toMatch(/python3?$/)
    expect(args).toEqual([expect.stringMatching(/\/bin\/samurai$/), "version", "--json"])
    expect(options).toMatchObject({ timeout: expect.any(Number) })
    expect(options.timeout).toBeGreaterThanOrEqual(1_000)
    expect(options.timeout).toBeLessThanOrEqual(8_000)
    expect(options.shell).not.toBe(true)
  })

  it("deduplicates concurrent requests and passes unrelated requests onward", async () => {
    let callback: ((error: Error | null, stdout: string, stderr: string) => void) | undefined
    execFileMock.mockImplementation((_bin, _args, _options, cb) => { callback = cb })
    const handler = capture((await loadPlugin()).configureServer)

    const one = request(handler)
    const two = request(handler, "/api/version?tab=2")
    expect(execFileMock).toHaveBeenCalledTimes(1)
    callback!(null, '{"current_version":"2.3.0","latest_version":null,"update_available":false}', "")
    await expect(Promise.all([one, two])).resolves.toHaveLength(2)
    await expect(request(handler, "/api/version/extra")).resolves.toMatchObject({ next: true })
    await expect(request(handler, "/api/version", "POST")).resolves.toMatchObject({ next: true })
  })

  it.each([
    ["process failure", new Error("no python"), ""],
    ["invalid JSON", null, "not json"],
    ["invalid shape", null, '{"current_version":23,"latest_version":null,"update_available":false}'],
  ])("fails silently with a stable shape on %s", async (_label, error, stdout) => {
    execFileMock.mockImplementation((_bin, _args, _options, callback) => callback(error, stdout, ""))
    const handler = capture((await loadPlugin()).configurePreviewServer)

    await expect(request(handler)).resolves.toMatchObject({
      status: 200,
      body: { current_version: null, latest_version: null, update_available: false },
      next: false,
    })
  })

  it("re-checks after the cache expires: 6 h after a result, 10 min after a failure", async () => {
    const now = vi.spyOn(Date, "now").mockReturnValue(1_000_000)
    execFileMock.mockImplementationOnce((_b, _a, _o, callback) => callback(new Error("offline"), "", ""))
    execFileMock.mockImplementation((_b, _a, _o, callback) => callback(null, JSON.stringify({
      current_version: "2.4.0", latest_version: "2.5.0", update_available: true,
    }), ""))
    const handler = capture((await loadPlugin()).configureServer)
    await request(handler)
    now.mockReturnValue(1_000_000 + 9 * 60_000)
    await request(handler)
    expect(execFileMock).toHaveBeenCalledTimes(1)
    now.mockReturnValue(1_000_000 + 11 * 60_000)
    expect((await request(handler)).body).toMatchObject({ latest_version: "2.5.0" })
    expect(execFileMock).toHaveBeenCalledTimes(2)
    now.mockReturnValue(1_000_000 + 11 * 60_000 + 6 * 3_600_000 + 1)
    await request(handler)
    expect(execFileMock).toHaveBeenCalledTimes(3)
    now.mockRestore()
  })
})
