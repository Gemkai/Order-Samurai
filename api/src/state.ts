import fs from 'fs'
import path from 'path'
import { fileURLToPath } from 'url'
import chokidar, { type FSWatcher } from 'chokidar'
import { EventEmitter } from 'events'
import type { DojoState, PillarSlug } from './types.js'

// GOVERNANCE_ROOT and WID_PAYLOAD_PATH - used by ReflexEngine
export const GOVERNANCE_ROOT: string = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)), '..', '..'
)

// Derived from the repo location, NOT a hardcoded machine path: the old
// Windows-era default (a hardcoded C:\Users\...\ path) silently broke every state endpoint
// on this Mac — health reported ONLINE while toggle/auto-remediation failed
// with "state write failed" because DOJO_STATE.json resolved to a dead path.
// This copy's layout is flat (api/, dashboard-ui/, bin/, state/ are siblings
// under GOVERNANCE_ROOT) — do NOT append 'Order Samurai' here, that lands on
// a phantom nested folder instead of the real state/ dir one level up.
export const ORDER_SAMURAI_ROOT: string =
  process.env['ORDER_SAMURAI_ROOT'] ?? GOVERNANCE_ROOT

export const DOJO_STATE_PATH = path.join(ORDER_SAMURAI_ROOT, 'state', 'DOJO_STATE.json')
export const RONIN_PILLAR_PATH = path.join(ORDER_SAMURAI_ROOT, 'bin', 'ronin-pillar')
export const WID_PAYLOAD_PATH: string =
  process.env['WID_PAYLOAD_PATH'] ??
  path.join(GOVERNANCE_ROOT, 'dashboard-ui', 'public', 'wid_payload.json')

// P4: versioned contract for the wid_payload envelope. The Python producer
// (agentica_core.aggregate.write_payload) validates the SAME file on write.
export const WID_PAYLOAD_SCHEMA_PATH: string =
  path.join(GOVERNANCE_ROOT, 'schema', 'wid_payload.schema.json')

export const PILLAR_SLUGS: PillarSlug[] = ['bow', 'sword', 'brush', 'arts']

function isValidDojoState(s: unknown): s is DojoState {
  return typeof s === 'object' && s !== null && typeof (s as DojoState).pillars === 'object'
}

export class DojoStateManager extends EventEmitter {
  private _state: DojoState | null = null
  private watcher: FSWatcher | null = null

  read(): DojoState | null {
    try {
      const parsed: unknown = JSON.parse(fs.readFileSync(DOJO_STATE_PATH, 'utf8'))
      if (!isValidDojoState(parsed)) return null
      this._state = parsed
      return this._state
    } catch {
      return null
    }
  }

  get current(): DojoState | null {
    return this._state
  }

  toggle(pillar: PillarSlug): DojoState | null {
    const s = this.read()
    if (!s) return null
    if (!s.pillars?.[pillar]) return null
    const existing = s.pillars[pillar].ronin_mode
    s.pillars[pillar].ronin_mode = existing === 'ronin' ? 'dormant' : 'ronin'
    // Atomic write: truncation mid-read by a concurrent observer can produce invalid JSON.
    const tmp = DOJO_STATE_PATH + '.tmp'
    fs.writeFileSync(tmp, JSON.stringify(s, null, 2), 'utf8')
    try {
      fs.renameSync(tmp, DOJO_STATE_PATH)
    } catch {
      // Windows: EPERM when chokidar holds the destination file open — fall back to copy.
      fs.copyFileSync(tmp, DOJO_STATE_PATH)
      fs.unlinkSync(tmp)
    }
    this._state = s
    this.emit('change', s)
    return s
  }

  watch(): void {
    this.watcher = chokidar.watch(DOJO_STATE_PATH, {
      ignoreInitial: true,
      awaitWriteFinish: { stabilityThreshold: 80, pollInterval: 30 },
    })
    this.watcher.on('change', () => {
      const s = this.read()
      if (s) this.emit('change', s)
    })
  }

  stop(): void {
    this.watcher?.close()
  }
}
