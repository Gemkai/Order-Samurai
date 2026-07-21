import { spawn } from 'child_process'
import { EventEmitter } from 'events'
import { execFileSync } from 'child_process'
import { RONIN_PILLAR_PATH, ORDER_SAMURAI_ROOT, PILLAR_SLUGS, type DojoStateManager } from './state.js'
import type { PillarSlug, RoninStatus, DojoState } from './types.js'

// Backstop kill for a spawned ronin cycle. MUST exceed the inner budget the ronin-pillar
// script enforces (timeout ${CYCLE_TIMEOUT}s claude …; dojo.env CYCLE_TIMEOUT=2400 → 40 min)
// so the script's own timeout governs and this is only a safety net. The old 5-min value
// guillotined every real cycle (which needs ~20–40 min) → the RUN button always errored.
// Override via RONIN_CHILD_TIMEOUT_MS if the cycle budget changes.
const CHILD_TIMEOUT_MS = Number(process.env.RONIN_CHILD_TIMEOUT_MS) || 45 * 60 * 1000 // 45 min default

/** Returns [cmd, args] appropriate for the current platform. */
function buildSpawnArgs(pillar: PillarSlug): [string, string[]] {
  if (process.platform === 'win32') {
    // On Windows, prefer bash (Git Bash / WSL) if available; fall back to PowerShell.
    let hasBash = false
    try {
      execFileSync('bash', ['--version'], { stdio: 'ignore' })
      hasBash = true
    } catch {
      hasBash = false
    }
    if (hasBash) {
      return ['bash', [RONIN_PILLAR_PATH, pillar]]
    }
    return ['powershell.exe', ['-Command', `& "${RONIN_PILLAR_PATH}" ${pillar}`]]
  }
  return ['bash', [RONIN_PILLAR_PATH, pillar]]
}

export class AutoRemediationEngine extends EventEmitter {
  private running = new Set<PillarSlug>()
  // Global queue: manual run cycle uses the shared git working tree (no worktrees),
  // so only ONE ronin-pillar process may run at a time. Subsequent requests queue
  // and execute automatically when the current one finishes.
  private queue: PillarSlug[] = []
  private readonly _onStateChange = (s: DojoState) => this.check(s)

  constructor(private stateManager: DojoStateManager) {
    super()
    stateManager.on('change', this._onStateChange)
  }

  destroy(): void {
    this.stateManager.off('change', this._onStateChange)
    this.removeAllListeners()
  }

  /** True if any pillar's manual cycle is currently running. */
  isAnyRunning(): boolean {
    return this.running.size > 0
  }

  check(s: DojoState): void {
    for (const slug of PILLAR_SLUGS) {
      const p = s.pillars[slug]
      if (p.ronin_mode !== 'ronin') continue
      if (p.live_current === null) continue
      if (p.live_current >= p.live_baseline) continue
      if (this.running.has(slug)) continue
      if (this.queue.includes(slug)) continue
      this.emit('auto_remediation', slug)
      this.enqueue(slug)
    }
  }

  /** Enqueue a pillar run. Runs immediately if nothing is in flight; queues otherwise. */
  enqueue(pillar: PillarSlug): void {
    if (!PILLAR_SLUGS.includes(pillar)) {
      this.emit('output', pillar, `Unknown pillar: ${pillar}`)
      return
    }
    if (this.running.has(pillar) || this.queue.includes(pillar)) return
    if (this.isAnyRunning()) {
      this.queue.push(pillar)
      this.emit('output', pillar, `⏳ queued — waiting for current cycle to finish`)
      this.emit('status', pillar, 'running' as RoninStatus) // show as pending in UI
      return
    }
    this._spawn(pillar)
  }

  /** Public alias kept for back-compat with server.ts call sites. */
  run(pillar: PillarSlug): void {
    this.enqueue(pillar)
  }

  private _drainQueue(): void {
    if (this.queue.length === 0) return
    const next = this.queue.shift()!
    this._spawn(next)
  }

  private _spawn(pillar: PillarSlug): void {
    this.running.add(pillar)
    this.emit('status', pillar, 'running' as RoninStatus)

    const [cmd, args] = buildSpawnArgs(pillar)
    const child = spawn(cmd, args, {
      cwd: ORDER_SAMURAI_ROOT,
      env: { ...process.env },
    })

    let settled = false

    const killTimer = setTimeout(() => {
      child.kill()
      this.running.delete(pillar)
      settled = true
      this.emit('status', pillar, 'error' as RoninStatus)
      this.emit('output', pillar, `Error: process exceeded backstop timeout (${Math.round(CHILD_TIMEOUT_MS / 60000)} min)`)
      this._drainQueue()
    }, CHILD_TIMEOUT_MS)

    const handleData = (data: Buffer) => {
      const lines = data.toString().split('\n').filter((l) => l.trim())
      for (const line of lines) {
        this.emit('output', pillar, line.slice(0, 200))
      }
    }

    child.stdout?.on('data', handleData)
    child.stderr?.on('data', handleData)
    child.on('close', (code) => {
      clearTimeout(killTimer)
      this.running.delete(pillar)
      if (settled) return
      settled = true
      if (code === null) {
        // Killed by signal (e.g. timeout); status already emitted by the timer handler.
      } else {
        this.emit('status', pillar, (code === 0 ? 'done' : 'error') as RoninStatus)
      }
      this._drainQueue()
    })
    child.on('error', (err) => {
      clearTimeout(killTimer)
      this.running.delete(pillar)
      if (settled) return
      settled = true
      this.emit('status', pillar, 'error' as RoninStatus)
      this.emit('output', pillar, `Error: ${err.message}`)
      this._drainQueue()
    })
  }

  isRunning(pillar: PillarSlug): boolean {
    return this.running.has(pillar)
  }
}
