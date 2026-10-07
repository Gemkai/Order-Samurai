import { spawnSync } from 'child_process'
import fs from 'fs'
import os from 'os'
import path from 'path'

/** The interpreter for running the repo's Python helpers (refresh_dashboard.py, bin/*.py).
 *  On PEP 668 "externally managed" Pythons (Homebrew, Debian/Ubuntu) bin/install.sh installs
 *  jsonschema/requests into ${SAMURAI_HOME:-~/.samurai}/venv, which plain python3 cannot
 *  import — so prefer that venv's python when it exists and runs, else the system one.
 *  Probed on every call so a venv created or broken after server start is picked up.
 *  Claude Code hooks are NOT routed here: they stay stdlib-only on system python3. */
export function resolvePythonBin(
  env: NodeJS.ProcessEnv = process.env,
  platform: NodeJS.Platform = process.platform,
): string {
  const samuraiHome = env['SAMURAI_HOME'] || path.join(env['HOME'] || os.homedir(), '.samurai')
  const venvPython = path.join(samuraiHome, 'venv', 'bin', 'python')
  if (fs.existsSync(venvPython)) {
    const probe = spawnSync(venvPython, ['-c', ''], { stdio: 'ignore', timeout: 5_000 })
    if (probe.status === 0) return venvPython
  }
  return platform === 'win32' ? 'python' : 'python3'
}
