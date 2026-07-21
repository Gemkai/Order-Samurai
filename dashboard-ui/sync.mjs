// Refresh the dashboard's data: aggregate telemetry, regenerate reports, then copy
// the payload/report files into public/.
import { execSync } from "node:child_process"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const here = dirname(fileURLToPath(import.meta.url)) // dashboard-ui/
const governance = resolve(here, "..") // Governance/  (where agentica_core is importable)

execSync("python refresh_dashboard.py", { cwd: governance, stdio: "inherit" })
