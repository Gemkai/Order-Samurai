import assert from "node:assert/strict"
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join, resolve } from "node:path"
import process from "node:process"
import test, { after, beforeEach, mock } from "node:test"

const calls = { execFile: [], exec: [], spawn: [] }
let probeStatus = 0

mock.module("node:child_process", {
  exports: {
    execFileSync: (...args) => calls.execFile.push(args),
    execSync: (...args) => calls.exec.push(args),
    spawnSync: (...args) => {
      calls.spawn.push(args)
      return { status: probeStatus }
    },
  },
})

const originalPlatform = process.platform
const originalSamuraiHome = process.env.SAMURAI_HOME
const originalHome = process.env.HOME
let importId = 0

beforeEach(() => {
  calls.execFile.length = 0
  calls.exec.length = 0
  calls.spawn.length = 0
  probeStatus = 0
  Object.defineProperty(process, "platform", { value: originalPlatform })
})

after(() => {
  Object.defineProperty(process, "platform", { value: originalPlatform })
  if (originalSamuraiHome === undefined) delete process.env.SAMURAI_HOME
  else process.env.SAMURAI_HOME = originalSamuraiHome
  if (originalHome === undefined) delete process.env.HOME
  else process.env.HOME = originalHome
  mock.restoreAll()
})

async function runSync({ venv = "missing", platform = "darwin", useHomeDefault = false } = {}) {
  const home = mkdtempSync(join(tmpdir(), "samurai-sync-home-"))
  process.env.HOME = home
  process.env.SAMURAI_HOME = useHomeDefault ? "" : join(home, "custom-samurai")
  const samuraiHome = useHomeDefault ? join(home, ".samurai") : process.env.SAMURAI_HOME
  Object.defineProperty(process, "platform", { value: platform })
  const venvPython = join(samuraiHome, "venv", "bin", "python")
  if (venv !== "missing") {
    mkdirSync(resolve(venvPython, ".."), { recursive: true })
    writeFileSync(venvPython, "")
  }
  probeStatus = venv === "working" ? 0 : 1
  await import(`./sync.mjs?acceptance=${importId++}`)
  return venvPython
}

function assertRefresh(executable) {
  assert.equal(calls.execFile.length, 1)
  const [bin, args, options] = calls.execFile[0]
  assert.equal(bin, executable)
  assert.deepEqual(args, ["refresh_dashboard.py"])
  assert.equal(options.cwd, resolve(import.meta.dirname, ".."))
  assert.equal(options.stdio, "inherit")
  assert.equal(calls.exec.length, 0, "sync must not interpolate a shell command")
}

test("uses a working Samurai venv after probing it", async () => {
  const venvPython = await runSync({ venv: "working" })
  assert.equal(calls.spawn.length, 1)
  assert.deepEqual(calls.spawn[0], [venvPython, ["-c", ""], { stdio: "ignore", timeout: 5_000 }])
  assertRefresh(venvPython)
})

test("falls back to python3 when the Samurai venv is missing", async () => {
  await runSync()
  assert.equal(calls.spawn.length, 0)
  assertRefresh("python3")
})

test("falls back to python3 when the Samurai venv probe fails", async () => {
  await runSync({ venv: "broken" })
  assert.equal(calls.spawn.length, 1)
  assertRefresh("python3")
})

test("uses the HOME default when SAMURAI_HOME is empty", async () => {
  const venvPython = await runSync({ venv: "working", useHomeDefault: true })
  assertRefresh(venvPython)
})

test("uses python on Windows", async () => {
  await runSync({ platform: "win32" })
  assertRefresh("python")
})
