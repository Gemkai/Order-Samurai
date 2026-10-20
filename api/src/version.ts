import { execFile } from 'node:child_process'
import path from 'node:path'
import type { RequestHandler } from 'express'

interface VersionStatus {
  current_version: string | null
  latest_version: string | null
  update_available: boolean
}

const UNKNOWN: VersionStatus = { current_version: null, latest_version: null, update_available: false }

export function createVersionHandler(root: string): RequestHandler {
  // Cache success or failure for this server's lifetime, including concurrent requests.
  let cached: Promise<VersionStatus> | undefined
  return (_req, res) => {
    cached ??= new Promise<VersionStatus>((resolve) => {
      execFile('python3', [path.join(root, 'bin', 'samurai'), 'version', '--json'],
        { timeout: 8_000, killSignal: 'SIGKILL', maxBuffer: 64 * 1024, encoding: 'utf8' }, (error, stdout) => {
          if (error) { resolve(UNKNOWN); return }
          try {
            const value: unknown = JSON.parse(stdout)
            resolve(isVersionStatus(value) ? value : UNKNOWN)
          } catch { resolve(UNKNOWN) }
        })
    }).catch(() => UNKNOWN)
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
