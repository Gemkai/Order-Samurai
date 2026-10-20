import { execFile } from 'node:child_process'
import path from 'node:path'
import type { RequestHandler } from 'express'
import { resolvePythonBin } from './python-bin.js'

interface VersionStatus {
  current_version: string | null
  latest_version: string | null
  update_available: boolean
}

const UNKNOWN: VersionStatus = { current_version: null, latest_version: null, update_available: false }

// A long-running server re-checks: 6 h after a result (the CLI itself caches for a day),
// 10 min after a failure. Concurrent requests share the in-flight check.
export const KNOWN_TTL_MS = 6 * 3_600_000
export const UNKNOWN_TTL_MS = 10 * 60_000

export function createVersionHandler(root: string): RequestHandler {
  let cached: Promise<VersionStatus> | undefined
  let expires = 0
  return (_req, res) => {
    if (cached && Date.now() >= expires) cached = undefined
    if (!cached) expires = Infinity  // set when the check settles
    cached ??= new Promise<VersionStatus>((resolve) => {
      execFile(resolvePythonBin(), [path.join(root, 'bin', 'samurai'), 'version', '--json'],
        { timeout: 8_000, killSignal: 'SIGKILL', maxBuffer: 64 * 1024, encoding: 'utf8' }, (error, stdout) => {
          if (error) { resolve(UNKNOWN); return }
          try {
            const value: unknown = JSON.parse(stdout)
            resolve(isVersionStatus(value) ? value : UNKNOWN)
          } catch { resolve(UNKNOWN) }
        })
    }).catch(() => UNKNOWN).then((status) => {
      expires = Date.now() + (status.latest_version === null ? UNKNOWN_TTL_MS : KNOWN_TTL_MS)
      return status
    })
    void cached.then((status) => res.json(status))
  }
}

function isVersionStatus(value: unknown): value is VersionStatus {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const status = value as Record<string, unknown>
  return Object.keys(status).length === 3
    && (typeof status.current_version === 'string' || status.current_version === null)
    && (typeof status.latest_version === 'string' || status.latest_version === null)
    && typeof status.update_available === 'boolean'
}
