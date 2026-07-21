import fs from 'fs'
import os from 'os'
import path from 'path'

/**
 * Order Samurai Pro entitlement (TypeScript reader).
 *
 * Reads the SAME `~/.samurai/license.json` that agentica_core/licensing.py writes at
 * activation time — one contract, two languages. Fail-CLOSED to Free: any absence,
 * parse error, or non-active status yields `false`. Pure disk read (offline-perpetual
 * model), no network. See agentica_core/licensing.py for the write side.
 */

function samuraiHome(): string {
  return process.env['SAMURAI_HOME'] || path.join(os.homedir(), '.samurai')
}

export function licensePath(): string {
  return path.join(samuraiHome(), 'license.json')
}

/** True only for a VALID, ACTIVE, non-refunded Pro entitlement on disk. Never throws. */
export function isProEntitled(): boolean {
  try {
    const raw = fs.readFileSync(licensePath(), 'utf8')
    const ent = JSON.parse(raw) as Record<string, unknown>
    return (
      ent['tier'] === 'pro' &&
      ent['valid'] === true &&
      ent['status'] === 'active' &&
      ent['refunded'] !== true
    )
  } catch {
    return false
  }
}
