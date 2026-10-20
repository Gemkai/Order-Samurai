// Refresh the dashboard's data: aggregate telemetry, regenerate reports, then copy
// the payload/report files into public/.
import { execFileSync, spawnSync } from "node:child_process"
import { existsSync } from "node:fs"
import { homedir } from "node:os"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"

// Match api/src/python-bin.ts: PEP 668 installs keep dependencies in this venv.
function resolvePythonBin(env = process.env, platform = process.platform) {
  const samuraiHome = env.SAMURAI_HOME || join(env.HOME || homedir(), ".samurai")
  const venvPython = join(samuraiHome, "venv", "bin", "python")
  if (existsSync(venvPython)) {
    const probe = spawnSync(venvPython, ["-c", ""], { stdio: "ignore", timeout: 5_000 })
    if (probe.status === 0) return venvPython
  }
  return platform === "win32" ? "python" : "python3"
}

const here = dirname(fileURLToPath(import.meta.url)) // dashboard-ui/
const governance = resolve(here, "..") // Governance/  (where agentica_core is importable)

execFileSync(resolvePythonBin(), ["refresh_dashboard.py"], { cwd: governance, stdio: "inherit" })
