import fs from 'fs'
import path from 'path'
import { spawn, spawnSync } from 'child_process'
import { EventEmitter } from 'events'
import chokidar, { type FSWatcher } from 'chokidar'
import Ajv from 'ajv'
import { WID_PAYLOAD_PATH, WID_PAYLOAD_SCHEMA_PATH, ORDER_SAMURAI_ROOT, GOVERNANCE_ROOT } from './state.js'
import type { VerdictRecord, VerdictMapEntry } from './types.js'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ReflexTier = 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW' | 'INFO'

interface MechanismBlock {
  script: string
  args: string[]
  read_only: boolean
  timeout_s: number
}

export interface ReflexEntry {
  id: string
  tier: ReflexTier
  command: string
  status: string
  source: string
  message?: string
  category?: string
  target?: string
  mechanism?: MechanismBlock
  // Governance opt-in grant — Phase 1 emits these on every reflex via the
  // Python aggregator; Phase 0 reads them for telemetry only; Phase 2 will
  // gate eligibility on them behind REFLEX_REQUIRE_GRANT (default-off).
  // Undefined preserves legacy behavior (treated as "ready" by the gate).
  maturity?: string
  reflex_ready?: boolean
  // Manual dashboard runs (not present in this pack's engine yet) — the fire-time
  // measurement code guards on it; undefined reads as "not manual", preserving behavior.
  manual?: boolean
}

/** Parse a noImprovement key ("metric:<pillar>:<Metric>::<cmd>") into its metric ref.
 *  Ported from the live repo's ledger-recompute (this pack has no such module). */
function metricFromKey(key: string): { pillar: string | null; metric: string } | null {
  const id = key.split('::', 1)[0]!
  const parts = id.split(':')
  if (parts.length < 3) return null
  if (parts[0] !== 'metric' && parts[0] !== 'trajectory') return null
  return { pillar: parts[1] || null, metric: parts.slice(2).join(':') }
}

interface WidPayload {
  reflexes?: ReflexEntry[]
  [key: string]: unknown
}

// Key: `${id}::${command}`
interface CooldownRecord {
  firedAt: number
}

interface NoImprovementRecord {
  consecutive: number
  stuck: boolean
  /** Consecutive runs that exited without finishing (turn-cap / wall-clock timeout). Tracked
   *  separately from `consecutive` so a slow-but-working skill isn't parked as if it failed —
   *  still bounded by INCOMPLETE_LIMIT to prevent endless non-finishing retries. */
  incompleteConsecutive?: number
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const COOLDOWN_MS = 30 * 60 * 1000          // 30 minutes
const EXEC_TIMEOUT_MS = 5 * 60 * 1000       // 5 minutes
const LOOP_BREAKER_LIMIT = 2
/** Parks a reflex after this many consecutive incomplete (turn-cap / wall-clock) runs.
 *  Higher than LOOP_BREAKER_LIMIT because "didn't finish" is weaker evidence of a broken
 *  skill than "ran and errored / didn't move the metric". */
const INCOMPLETE_LIMIT = 4
/** Per-skill turn budget for autonomous remediation. 30 was too low for heavy orchestration
 *  skills (e.g. sensei-cycle), which exceeded it, exited non-zero ("Reached max turns"), and
 *  were misclassified as errors → loop-breaker parked them. Override with REFLEX_MAX_TURNS on
 *  the host (no rebuild). Kept as a string since it is spliced straight into the spawn args. */
export const REFLEX_MAX_TURNS: string = String(
  Number(process.env['REFLEX_MAX_TURNS']) > 0 ? Number(process.env['REFLEX_MAX_TURNS']) : 50,
)

/** Optional approval window before a skill fires autonomously (#G1).
 *  0 = fully autonomous (default). Set REFLEX_APPROVAL_WINDOW_MS env var to enable.
 *  During the window, operator can cancel via POST /api/reflex/cancel/:key. */
const APPROVAL_WINDOW_MS = parseInt(process.env['REFLEX_APPROVAL_WINDOW_MS'] ?? '0', 10)

/** Per-type gate for code-modifying skills only (#blast-radius).
 *  0 = disabled (default). Set REFLEX_CODE_APPROVAL_MS to gate code-modifying skills
 *  while keeping readonly/diagnostic skills fully autonomous.
 *  REFLEX_APPROVAL_WINDOW_MS takes precedence (it overrides all skills uniformly). */
const REFLEX_CODE_APPROVAL_MS = parseInt(process.env['REFLEX_CODE_APPROVAL_MS'] ?? '0', 10)

/** Governance opt-in grant (Phase 2). When true, _isEligible() requires the
 *  reflex to carry maturity="APPLY" (or undefined for legacy) or reflex_ready=true.
 *  Default false → byte-identical behavior to pre-grant. Flipped → only mechanism-
 *  backed canary metrics with non-APPLY maturity are gated; everything else still
 *  fires (Phase 1 seeds APPLY on every METRIC_CONFIG entry).
 *  Runtime-read on process start; rollback = unset/false + restart, no rebuild. */
const REFLEX_REQUIRE_GRANT = (process.env['REFLEX_REQUIRE_GRANT'] ?? 'false').toLowerCase() === 'true'

/** Bushido fail-open policy (R8). When 'true' (default — Phase 2 rollout safety),
 *  a Python error from bushido_check.py allows execution. When 'false' (recommended
 *  for production once stable), a Python error for non-readonly skills blocks
 *  execution. Readonly skills are always allowed even when fail-closed. */
const BUSHIDO_FAIL_OPEN = (process.env['BUSHIDO_FAIL_OPEN'] ?? 'true').toLowerCase() !== 'false'

/** Cache TTL for Bushido decisions — applied ONLY to `auto` and `hard_stop`
 *  decisions. `queue` / `hitl` are NEVER cached (R3) so a human approval in
 *  hitl_queue.json propagates within a single check cycle, not minutes. */
const BUSHIDO_CACHE_MS = 60 * 1000

/** Verify-gate (2a) kill switch. Default ON: before spawning an expensive code-modifying
 *  skill for a batch metric, re-measure the breach live (bin/remeasure_gate.py) and suppress
 *  the spawn if it recovered/was a stale-snapshot phantom. Fail-open on any gate error. Set
 *  REFLEX_VERIFY_GATE=false to disable (rollback path, no rebuild). */
const VERIFY_GATE_ENABLED = (process.env['REFLEX_VERIFY_GATE'] ?? 'true').toLowerCase() !== 'false'

/** Autonomous patch-apply kill switch. DEFAULT OFF for the public pack: a code-modifying
 *  remediation that passes the maker-checker audit + pytest gate is saved to
 *  state/pending_remediation_*.patch for human review instead of being git-applied to the
 *  live repo — so a freshly-cloned install never rewrites a stranger's working tree
 *  unattended. Set REFLEX_AUTO_APPLY=true to restore fully autonomous apply (the audit +
 *  pytest gate run identically either way). Rollback = unset/false + restart, no rebuild. */
const AUTO_APPLY_ENABLED = (process.env['REFLEX_AUTO_APPLY'] ?? 'false').toLowerCase() === 'true'

/** Parse REFLEX_BATCH_WINDOW = "startHour-endHour" (local 24h, e.g. "2-6" = 02:00–06:00).
 *  Empty / malformed → null (feature disabled = today's real-time behavior). start===end is
 *  rejected (an empty or all-day window is never the intent). Exported for tests. */
export function parseBatchWindow(spec: string): { start: number; end: number } | null {
  const m = /^(\d{1,2})-(\d{1,2})$/.exec(spec.trim())
  if (!m) return null
  const start = Number(m[1]), end = Number(m[2])
  if (!Number.isInteger(start) || !Number.isInteger(end)) return null
  if (start < 0 || start > 23 || end < 0 || end > 23 || start === end) return null
  return { start, end }
}

/** True when `date`'s LOCAL hour falls inside the window, handling a window that wraps
 *  past midnight (e.g. 22-4 covers 22:00–03:59). Exported for tests. */
export function inBatchWindow(win: { start: number; end: number }, date: Date): boolean {
  const h = date.getHours()
  return win.start < win.end
    ? (h >= win.start && h < win.end)
    : (h >= win.start || h < win.end)
}

/** Execution priority — lower number fires first */
const TIER_ORDER: Record<ReflexTier, number> = {
  CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3, INFO: 4,
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function readWid(): ReflexEntry[] {
  try {
    const raw = fs.readFileSync(WID_PAYLOAD_PATH, 'utf8')
    const parsed = JSON.parse(raw) as WidPayload
    return Array.isArray(parsed.reflexes) ? parsed.reflexes : []
  } catch {
    return []
  }
}

/** Full payload object (not just reflexes) — the fire-time measurement path reads
 *  metric leaves out of pillars. Ported from the live repo's payload reader. */
function readWidFull(): WidPayload | null {
  try {
    return JSON.parse(fs.readFileSync(WID_PAYLOAD_PATH, 'utf8')) as WidPayload
  } catch {
    return null
  }
}

type MetricLeaf = { val?: unknown; is_simulated?: boolean }

/** Find a graded metric leaf by name in the pillars tree. Tries the id's own
 *  pillar first, then scans all pillars (the pillar segment in a reflex id can
 *  drift from where the metric is actually placed). Ported from ledger-recompute. */
function findMetricLeaf(payload: unknown, pillar: string | null, metric: string): MetricLeaf | undefined {
  const pillars = (payload as { pillars?: Record<string, unknown> })?.pillars
  if (!pillars || typeof pillars !== 'object') return undefined
  const scan = (pobj: unknown): MetricLeaf | undefined => {
    for (const group of Object.values((pobj as object) ?? {})) {
      if (group && typeof group === 'object' && metric in (group as object)) {
        return (group as Record<string, MetricLeaf>)[metric]
      }
    }
    return undefined
  }
  if (pillar && pillars[pillar]) {
    const hit = scan(pillars[pillar])
    if (hit) return hit
  }
  for (const pobj of Object.values(pillars)) {
    const hit = scan(pobj)
    if (hit) return hit
  }
  return undefined
}

/** P4: validate the persisted envelope against the shared JSON Schema at startup.
 *  The Python producer validates the same schema on write, so both ends of the
 *  Python⇄TS seam enforce one contract. Throws (fail fast) if the file EXISTS but
 *  violates the schema. A missing payload is not a contract breach — the engine
 *  already tolerates it (readWid -> []) and the watcher picks it up when it
 *  appears — so it only warns, to avoid blocking boot on a fresh checkout. */
function validateWidPayloadAtStartup(): void {
  let raw: string
  try {
    raw = fs.readFileSync(WID_PAYLOAD_PATH, 'utf8')
  } catch {
    console.warn(
      `[reflex-engine] wid_payload.json not found at ${WID_PAYLOAD_PATH}; skipping startup schema check`,
    )
    return
  }
  const schema = JSON.parse(fs.readFileSync(WID_PAYLOAD_SCHEMA_PATH, 'utf8')) as object
  const ajv = new Ajv({ allErrors: true })
  const validate = ajv.compile(schema)
  const payload: unknown = JSON.parse(raw)
  if (!validate(payload)) {
    throw new Error(
      `[reflex-engine] wid_payload.json failed schema validation: ${ajv.errorsText(validate.errors)}`,
    )
  }
}

function buildSkillSpawnArgs(claudeBin: string, command: string): [string, string[]] {
  const args = [
    '--print',
    '-p', command.trim(),
    '--permission-mode', 'acceptEdits',
    '--max-turns', REFLEX_MAX_TURNS,
  ]
  if (process.platform === 'win32') {
    return ['cmd', ['/c', claudeBin, ...args]]
  }
  return [claudeBin, args]
}

function reflexKey(id: string, command: string): string {
  return `${id}::${command}`
}

// ---------------------------------------------------------------------------
// ReflexEngine
// ---------------------------------------------------------------------------

export class ReflexEngine extends EventEmitter {
  private watcher: FSWatcher | null = null
  private _pollTimer: ReturnType<typeof setInterval> | null = null
  private _isRunning = false

  /** Priority-ordered queue of reflex entries waiting to execute (CRITICAL first) */
  private queue: ReflexEntry[] = []

  /** Currently executing entry — null when idle */
  private activeEntry: ReflexEntry | null = null

  /** Cooldown registry: key → timestamp when skill was fired */
  private cooldowns = new Map<string, CooldownRecord>()

  /** Loop-breaker registry: key → no-improvement tracking */
  private noImprovement = new Map<string, NoImprovementRecord>()

  private readonly claudeBin: string

  /** Path for persisting cooldown + stuck state across server restarts */
  private readonly _statePath = path.join(ORDER_SAMURAI_ROOT, 'state', 'reflex_engine_state.json')

  /** Debounce timer for state writes — prevents I/O thrash on rapid mutations */
  private _saveTimer: ReturnType<typeof setTimeout> | null = null

  /** Reflex IDs present in wid_payload.json immediately before a skill spawns */
  private _preRunReflexIds: Set<string> | null = null

  /** Git HEAD commit captured before a skill spawns — for diff auditability */
  private _beforeCommit: string | null = null

  /** Metric value captured at fire time (verify-gate live re-measure when it ran,
   *  else the triggering wid_payload snapshot). Written to exec_log as metric_before
   *  so remediation efficacy is computed from real per-run values instead of the
   *  sparse metrics_history snapshots (2026-07-19 metric surface review §A1). */
  private _metricBefore: number | null = null

  /** Keys cancelled during their approval window (#G1) */
  private _pendingCancels = new Set<string>()

  /** The key currently awaiting approval — cancels for any other key are rejected,
   *  otherwise a stray cancel would persist and silently kill that reflex's NEXT fire. */
  private _awaitingApprovalKey: string | null = null

  /** Cached skill efficacy data for dynamic cooldown multiplier (#G2).
   *  Refreshed from disk at most once per 60 seconds. */
  private _efficacyCache: { data: Record<string, { cooldown_multiplier: number }>; loadedAt: number } | null = null

  /** Cached readonly skill set from skill_metadata.json (#blast-radius).
   *  Refreshed from disk at most once per 60 seconds. */
  private _readonlySkillsCache: { data: Set<string>; loadedAt: number } | null = null

  /** Cached set of metric names that cannot be auto-remediated (auto_remediable=False in
   *  METRIC_CONFIG). Sourced from non_remediable_metrics.json written by refresh_dashboard.py.
   *  Refreshed from disk at most once per 60 seconds. */
  private _nonRemediableCache: { data: Set<string>; loadedAt: number } | null = null

  /** Cached set of batch-deferred metric names (code-modifying agent remediation, no
   *  deterministic mechanism, not urgent). Sourced from state/batch_metrics.json written by
   *  refresh_dashboard.py (insights.batch_deferred_metrics). Drives the verify-gate and the
   *  batch-defer gate. Refreshed from disk at most once per 60 seconds. */
  private _batchMetricsCache: { data: Set<string>; loadedAt: number } | null = null

  /** Verdicts from rival — keyed by reflex_id, TTL 24h.
   *  REFUTED entries suppress execution in _isEligible(); absent = unchanged behavior (fail-safe). */
  private _verdicts = new Map<string, VerdictMapEntry>()
  private readonly _verdictsPath = path.join(ORDER_SAMURAI_ROOT, 'state', 'reflex_verdicts.json')

  /** Bushido tier decisions cached per `skill:pillar` for 60 s. Only `auto` and
   *  `hard_stop` are stored — `queue`/`hitl` would block consume-on-check (R3). */
  private _bushidoCache = new Map<string, { tier: string; cachedAt: number }>()

  /** Phase 3.4: queue_id Bushido returned (either enqueued OR consumed from an approval),
   *  keyed by `${id}::${command}`. _afterRun() reads this to drive `--complete`. */
  private _bushidoQueueIds = new Map<string, string>()

  constructor(
    claudeBin: string,
    /** Optional callback from server.ts: returns true if this command is already
     *  running in the spawnExec channel (cross-channel dedup). */
    private readonly _isChannelActive?: (cmd: string) => boolean,
  ) {
    super()
    this.claudeBin = claudeBin
    this._loadState()
  }

  // -------------------------------------------------------------------------
  // Public surface
  // -------------------------------------------------------------------------

  get isRunning(): boolean {
    return this._isRunning
  }

  get queueLength(): number {
    return this.queue.length
  }

  /**
   * The currently executing reflex entry — null when idle.
   * Exposed so server.ts can replay active state to freshly connected WS clients.
   */
  get activeReflexEntry(): ReflexEntry | null {
    return this.activeEntry
  }

  /**
   * Returns true if the given skill command is currently running or queued.
   * Called by server.ts to prevent duplicate execution across channels.
   */
  isCommandActive(cmd: string): boolean {
    const normalized = cmd.trim()
    if (this.activeEntry?.command.trim() === normalized) return true
    return this.queue.some(q => q.command.trim() === normalized)
  }

  /** Start watching wid_payload.json for changes and run an initial check.
   *
   * Two triggers:
   *  1. chokidar: fires _check() immediately whenever wid_payload.json changes
   *  2. 5-minute poll: ensures cooldown expiry is noticed even if the file hasn't
   *     changed (e.g. refresh_dashboard runs infrequently or not at all)
   */
  watch(): void {
    // P4: fail fast if the persisted envelope violates the typed contract.
    validateWidPayloadAtStartup()
    // Initial pass
    this._check()

    this.watcher = chokidar.watch(WID_PAYLOAD_PATH, {
      ignoreInitial: true,
      awaitWriteFinish: { stabilityThreshold: 100, pollInterval: 40 },
    })

    this.watcher.on('change', () => {
      this._check()
    })

    // Fallback poll — catches cooldowns that expired between file writes
    this._pollTimer = setInterval(() => {
      if (!this._isRunning) this._check()
    }, 5 * 60 * 1000)  // every 5 minutes
  }

  /**
   * Cancel a pending approval for the given reflex key (#G1).
   * Called by the POST /api/reflex/cancel/:key REST endpoint in server.ts.
   * Returns false (no-op) unless that key is currently awaiting approval.
   */
  cancelPending(key: string): boolean {
    if (this._awaitingApprovalKey !== key) return false
    this._pendingCancels.add(key)
    return true
  }

  /**
   * Inject a reflex entry directly into the execution queue.
   *
   * Called by server.ts when AutoRemediationEngine detects a pillar score regression —
   * bridges the pillar-level ronin system and the per-metric reflex system so that
   * alarmed metrics contributing to the regression are also remediated.
   *
   * All existing eligibility checks (cooldown, loop-breaker, approval gate, cross-channel
   * dedup) apply — callers do not need to pre-filter.
   *
   * Returns true if the entry was queued, false if ineligible or already present.
   */
  injectReflex(entry: ReflexEntry): boolean {
    if (!this._isEligible(entry)) return false
    const alreadyQueued = this.queue.some(q => q.id === entry.id && q.command === entry.command)
    const isActive = this.activeEntry?.id === entry.id && this.activeEntry?.command === entry.command
    if (alreadyQueued || isActive) return false
    this._insertWithPriority(entry)
    if (!this._isRunning) this._drainQueue()
    return true
  }

  /**
   * Reset stuck state for all reflex keys whose id starts with the given
   * metric prefix. This lets external callers unblock a reflex after a human
   * manually improves the underlying metric.
   */
  clearStuck(metric: string): void {
    for (const [key, record] of this.noImprovement.entries()) {
      // key format: `${id}::${command}`
      const id = key.split('::')[0] ?? ''
      if (id.startsWith(metric)) {
        record.consecutive = 0
        record.incompleteConsecutive = 0
        record.stuck = false
      }
    }
    this._saveState()
  }

  /**
   * Reset stuck state for EVERY reflex key. Recovery hatch for when the loop-breaker has
   * parked the whole engine (e.g. after a systemic misclassification froze every reflex).
   * Returns the number of keys that were stuck before the reset.
   */
  clearAllStuck(): number {
    let cleared = 0
    for (const record of this.noImprovement.values()) {
      if (record.stuck) cleared++
      record.consecutive = 0
      record.incompleteConsecutive = 0
      record.stuck = false
    }
    this._saveState()
    return cleared
  }

  /**
   * Accept a batch of rival verdicts from sensei-cycle.
   * REFUTED entries suppress execution in _isEligible() for 24h.
   * CONFIRMED / SUSPECT / absent = unchanged engine behavior (fail-safe invariant).
   */
  setVerdicts(verdicts: VerdictRecord[]): void {
    const expiresAt = Date.now() + 24 * 60 * 60 * 1000
    for (const v of verdicts) {
      this._verdicts.set(v.reflex_id, { ...v, expiresAt })
    }
    this._saveState()
  }

  destroy(): void {
    if (this._saveTimer) {
      clearTimeout(this._saveTimer)
      this._saveTimer = null
    }
    if (this._pollTimer) {
      clearInterval(this._pollTimer)
      this._pollTimer = null
    }
    this.watcher?.close()
    this.watcher = null
    this.removeAllListeners()
  }

  // -------------------------------------------------------------------------
  // Internal — state persistence (#27)
  // -------------------------------------------------------------------------

  private _loadState(): void {
    try {
      const raw = fs.readFileSync(this._statePath, 'utf8')
      const s = JSON.parse(raw) as {
        version: number
        cooldowns: Record<string, CooldownRecord>
        noImprovement: Record<string, NoImprovementRecord>
      }
      if (s.version === 1) {
        for (const [k, v] of Object.entries(s.cooldowns)) this.cooldowns.set(k, v)
        for (const [k, v] of Object.entries(s.noImprovement)) {
          this.noImprovement.set(k, { incompleteConsecutive: 0, ...v })
        }
      }
    } catch {
      // File absent on first run or corrupted — start fresh
    }
    // Load verdicts — drop already-expired entries on load
    try {
      const raw = fs.readFileSync(this._verdictsPath, 'utf8')
      const entries = JSON.parse(raw) as Record<string, VerdictMapEntry>
      const now = Date.now()
      for (const [k, v] of Object.entries(entries)) {
        if (v.expiresAt > now) this._verdicts.set(k, v)
      }
    } catch {
      // File absent on first run — start with empty verdict map
    }
  }

  private _saveState(): void {
    if (this._saveTimer) clearTimeout(this._saveTimer)
    this._saveTimer = setTimeout(() => {
      const s = {
        version: 1,
        timestamp: new Date().toISOString(),
        cooldowns: Object.fromEntries(this.cooldowns),
        noImprovement: Object.fromEntries(this.noImprovement),
      }
      this._atomicWrite(this._statePath, JSON.stringify(s, null, 2))
      this._atomicWrite(this._verdictsPath, JSON.stringify(Object.fromEntries(this._verdicts), null, 2))
    }, 500)
  }

  /** Atomic write: tmp + rename so a concurrent reader (skill_no_impact.py,
   *  reflex_eureka.py) never observes a torn/half-written JSON file. Falls back
   *  to copy+unlink on Windows EPERM (a watcher holding the destination open). */
  private _atomicWrite(dest: string, data: string): void {
    const tmp = `${dest}.tmp`
    try {
      fs.writeFileSync(tmp, data, 'utf8')
      try {
        fs.renameSync(tmp, dest)
      } catch {
        fs.copyFileSync(tmp, dest)
        fs.unlinkSync(tmp)
      }
    } catch {
      // Non-fatal — state dir may not exist yet
    }
  }

  // -------------------------------------------------------------------------
  // Internal — eligibility check
  // -------------------------------------------------------------------------

  private _check(): void {
    const reflexes = readWid()

    // Drop queued entries whose reflex was removed or deactivated while waiting.
    // Prevents executing stale work if the payload changes mid-queue.
    const liveIds = new Set(
      reflexes.filter((r) => r.status === 'active').map((r) => r.id),
    )
    this.queue = this.queue.filter((q) => liveIds.has(q.id))

    for (const entry of reflexes) {
      if (!this._isEligible(entry)) continue

      // Not already queued or running?
      const alreadyQueued = this.queue.some(
        (q) => q.id === entry.id && q.command === entry.command,
      )
      const isActive =
        this.activeEntry?.id === entry.id &&
        this.activeEntry?.command === entry.command

      if (alreadyQueued || isActive) continue

      // Insert in priority order (#10: CRITICAL before HIGH)
      this._insertWithPriority(entry)
    }

    // Kick off execution if idle
    if (!this._isRunning) {
      this._drainQueue()
    }
  }

  private _isEligible(entry: ReflexEntry): boolean {
    // 1. Tier must be CRITICAL or HIGH
    if (entry.tier !== 'CRITICAL' && entry.tier !== 'HIGH') return false

    // 2. Status must be active
    if (entry.status !== 'active') return false

    // 3. Command must start with "/" and match safe pattern.
    // Guards against cmd.exe metacharacter injection (&, |, >, ^) — startsWith('/')
    // alone doesn't constrain the rest of the string under cmd /c.
    const SAFE_COMMAND_RE = /^\/[a-z0-9:_-]+(\s+[\w./:-]+)*$/i
    if (!entry.command || !SAFE_COMMAND_RE.test(entry.command.trim())) return false

    // 4. Not in cooldown — multiplied by skill efficacy (#G2: failing skills cool longer).
    //    H3: emit structured skip-reason so the freeze set is fully inspectable.
    const key = reflexKey(entry.id, entry.command)
    const cooldown = this.cooldowns.get(key)
    if (cooldown) {
      const multiplier = this._getCooldownMultiplier(entry.command)
      if (Date.now() - cooldown.firedAt < COOLDOWN_MS * multiplier) {
        this.emit('auto_reflex_skipped', { reflex_id: entry.id, reason: 'cooldown' })
        return false
      }
    }

    // 5. Not stuck (loop-breaker) — H3 structured skip-reason.
    const noImp = this.noImprovement.get(key)
    if (noImp?.stuck) {
      this.emit('auto_reflex_skipped', { reflex_id: entry.id, reason: 'loop_breaker_stuck' })
      return false
    }

    // 6. Not already running in another channel (#11: cross-channel dedup) — H3.
    if (this._isChannelActive?.(entry.command)) {
      this.emit('auto_reflex_skipped', { reflex_id: entry.id, reason: 'channel_active' })
      return false
    }

    // 7. Not a non-auto-remediable metric (behavioral/structural — requires human action,
    //    no skill can change the underlying signal autonomously) — H3.
    const metricName = entry.id.split(':')[2] ?? ''
    if (metricName && this._isNonRemediable(metricName)) {
      this.emit('auto_reflex_skipped', { reflex_id: entry.id, reason: 'non_remediable' })
      return false
    }

    // 7a. Batch-defer (2b): a non-urgent, code-modifying agent remediation is held for the
    //     overnight batch window instead of spending a live skill spawn now — verify
    //     real-time (the mechanism/verify-gate paths still run), improve overnight. Active
    //     ONLY when REFLEX_BATCH_WINDOW is set; unset = disabled = today's real-time behavior.
    //     The metric stays breached in the payload, so the 5-minute poll re-fires it the
    //     moment the window opens.
    const batchWindow = this._batchWindow()
    if (batchWindow && metricName && this._isBatchMetric(metricName)) {
      if (!inBatchWindow(batchWindow, this._now())) {
        this.emit('auto_reflex_skipped', { reflex_id: entry.id, reason: 'batch_deferred' })
        return false
      }
    }

    // 7b. Not REFUTED by rival within the last 24h.
    //     Absent verdict = existing behavior exactly (fail-safe: sensei downtime never silences autonomy).
    const verdict = this._verdicts.get(entry.id)
    if (verdict && Date.now() < verdict.expiresAt && verdict.verdict === 'REFUTED') {
      this.emit('auto_reflex_skipped', { reflex_id: entry.id, reason: 'rival_refuted' })
      return false
    }

    // Phase 0/2 — Governance opt-in grant. Computes the grant decision (Phase 0
    // telemetry) and, when REFLEX_REQUIRE_GRANT=true, enforces it (Phase 2 gate,
    // step 7c). Inserted AFTER cooldown/loop-breaker so the gate can only subtract
    // eligibility — never reset `${id}::${command}` state. Default-off; flipping
    // the env var + restart is the rollback path (no rebuild).
    const grantedToRemediate = (
      entry.maturity === undefined ||
      entry.maturity === 'APPLY' ||
      entry.reflex_ready === true
    )
    // A read-only mechanism in DRY-RUN-GRADED may RUN to earn its grade — mechanism
    // only, never the skill fallback (enforced in _execute, Part B). Without this a
    // frozen DRY-RUN-GRADED reflex can never accumulate the runs needed to promote.
    const mayRunForGrading =
      entry.maturity === 'DRY-RUN-GRADED' && entry.mechanism?.read_only === true
    this.emit('auto_reflex_grant_eval', {
      reflex_id: entry.id,
      would_pass_grant: grantedToRemediate,
      grading_run: !grantedToRemediate && mayRunForGrading,
      maturity: entry.maturity,
      skip_reason: grantedToRemediate ? null : (mayRunForGrading ? 'grading_run' : 'no_grant'),
    })
    if (REFLEX_REQUIRE_GRANT && !grantedToRemediate && !mayRunForGrading) {
      this.emit('auto_reflex_skipped', { reflex_id: entry.id, reason: 'no_grant' })
      return false
    }

    // 8. Bushido — unified tier gate (Phase 2). LAST gate, so all prior signals win
    //    first and we don't spawn Python for already-skipped entries. Queue/hitl
    //    decisions are NEVER cached (R3) — they need a fresh hitl_queue.json read
    //    so consume-on-check fires the moment a human approves the item.
    if (this._isBushidoBlocked(entry)) {
      return false
    }

    return true
  }

  // -------------------------------------------------------------------------
  // Internal — Bushido tier gate (Phase 2)
  // -------------------------------------------------------------------------

  /**
   * Ask the unified Bushido decision module (Python) whether this reflex may
   * auto-fire. Returns true (= blocked) for tier queue/hitl/hard_stop; false
   * (= allowed) for tier auto.
   *
   * - Cache (60 s) covers only auto / hard_stop. Queue/hitl never cached so a
   *   human approval in state/hitl_queue.json takes effect on the next check.
   * - On Python error: honors BUSHIDO_FAIL_OPEN env var (R8). Default true
   *   (Phase 2 rollout): allow execution. Set to false in production to block
   *   non-readonly skills when Bushido is unhealthy.
   * - Stores any returned queue_id keyed by `${id}::${command}` so _afterRun()
   *   can call `--complete` after a consumed-approval skill finishes.
   */
  private _isBushidoBlocked(entry: ReflexEntry): boolean {
    const skillName = entry.command.trim().replace(/^\//, '').split(/\s+/)[0] ?? ''
    if (!skillName) return false
    const pillar = entry.id.split(':')[1] ?? ''
    const cacheKey = `${skillName}:${pillar}`
    const key = reflexKey(entry.id, entry.command)

    const cached = this._bushidoCache.get(cacheKey)
    if (cached && Date.now() - cached.cachedAt < BUSHIDO_CACHE_MS) {
      if (cached.tier === 'auto') return false
      if (cached.tier === 'hard_stop') {
        this.emit('auto_reflex_skipped', { reflex_id: entry.id, reason: 'bushido_blocked', tier: 'hard_stop', cached: true })
        return true
      }
      // Unexpected cached value — fall through and re-query.
    }

    const pythonBin = process.platform === 'win32' ? 'python' : 'python3'
    const script = path.join(ORDER_SAMURAI_ROOT, 'bin', 'bushido_check.py')
    const args = [script, '--skill', skillName, '--source', 'reflex', '--metric', entry.id]
    if (pillar) args.push('--pillar', pillar)

    let result: ReturnType<typeof spawnSync>
    try {
      result = spawnSync(pythonBin, args, {
        cwd: ORDER_SAMURAI_ROOT,
        encoding: 'utf8',
        timeout: 10_000,
        // PYTHONIOENCODING avoids the Windows cp1252 UnicodeEncodeError trap
        // documented in CLAUDE.md anti-pattern #13.
        env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
      })
    } catch (err) {
      return this._bushidoFailOpen(entry, skillName, `spawnSync threw: ${String(err)}`)
    }

    if (result.error) {
      return this._bushidoFailOpen(entry, skillName, `spawn error: ${result.error.message}`)
    }
    if (result.status === null) {
      return this._bushidoFailOpen(entry, skillName, `python timeout or signal (stderr: ${(result.stderr || '').slice(0, 200)})`)
    }
    if (result.status === 3) {
      return this._bushidoFailOpen(entry, skillName, `bushido error exit 3: ${(result.stderr || '').slice(0, 200)}`)
    }

    let tier = 'auto'
    let queueId: string | null = null
    const stdout = typeof result.stdout === 'string' ? result.stdout : String(result.stdout ?? '')
    try {
      const parsed = JSON.parse(stdout || '{}') as { tier?: string; queue_id?: string | null }
      tier = parsed.tier ?? 'auto'
      queueId = parsed.queue_id ?? null
    } catch {
      return this._bushidoFailOpen(entry, skillName, `bad JSON on stdout: ${stdout.slice(0, 200)}`)
    }

    if (tier === 'auto' || tier === 'hard_stop') {
      this._bushidoCache.set(cacheKey, { tier, cachedAt: Date.now() })
    }

    // Remember queue_id (may be set on AUTO from consume-on-check) so _afterRun
    // can mark the corresponding hitl item done/failed.
    if (queueId) this._bushidoQueueIds.set(key, queueId)
    else this._bushidoQueueIds.delete(key)

    if (tier === 'queue' || tier === 'hitl' || tier === 'hard_stop') {
      this.emit('auto_reflex_skipped', { reflex_id: entry.id, reason: 'bushido_blocked', tier })
      return true
    }
    return false
  }

  /** Apply BUSHIDO_FAIL_OPEN policy on Python errors. Always logs the reason. */
  private _bushidoFailOpen(entry: ReflexEntry, skillName: string, reason: string): boolean {
    this.emit('auto_reflex_output', { metric: entry.id, line: `[bushido] ${reason}` })
    if (BUSHIDO_FAIL_OPEN) return false  // permissive — preserves pre-Bushido behavior
    // Strict (production): block non-readonly skills. Readonly is always safe.
    if (this._isSkillReadonly(`/${skillName}`)) return false
    this.emit('auto_reflex_skipped', { reflex_id: entry.id, reason: 'bushido_blocked_fail_closed' })
    return true
  }

  // -------------------------------------------------------------------------
  // Internal — priority queue (#10)
  // -------------------------------------------------------------------------

  /** Insert entry so CRITICAL runs before HIGH, HIGH before MEDIUM, etc. */
  private _insertWithPriority(entry: ReflexEntry): void {
    const pri = TIER_ORDER[entry.tier]
    const idx = this.queue.findIndex(q => TIER_ORDER[q.tier] > pri)
    if (idx === -1) this.queue.push(entry)
    else this.queue.splice(idx, 0, entry)
  }

  // -------------------------------------------------------------------------
  // Internal — queue drain
  // -------------------------------------------------------------------------

  private _drainQueue(): void {
    if (this._isRunning) return
    if (this.queue.length === 0) return

    const entry = this.queue.shift()!
    this._execute(entry)
  }

  // -------------------------------------------------------------------------
  // Internal — spawn skill
  // -------------------------------------------------------------------------

  private async _execute(entry: ReflexEntry): Promise<void> {
    this._isRunning = true
    this.activeEntry = entry

    const key = reflexKey(entry.id, entry.command)

    // Tiered approval gate (#G1 + #blast-radius): pause before firing code-modifying
    // skills if REFLEX_CODE_APPROVAL_MS is set, or all skills if REFLEX_APPROVAL_WINDOW_MS
    // is set. Readonly/diagnostic skills are always autonomous.
    const approvalWindowMs = this._getApprovalWindowMs(entry.command)
    if (approvalWindowMs > 0) {
      this.emit('auto_reflex_pending', {
        metric: entry.id,
        command: entry.command,
        tier: entry.tier,
        windowMs: approvalWindowMs,
        cancelKey: key,
      })
      this._awaitingApprovalKey = key
      const approved = await this._waitForApproval(key, approvalWindowMs)
      this._awaitingApprovalKey = null
      this._pendingCancels.delete(key)
      if (!approved) {
        // Operator cancelled — set a half-cooldown so it doesn't immediately re-trigger
        this.cooldowns.set(key, { firedAt: Date.now() - Math.floor(COOLDOWN_MS / 2) })
        this._saveState()
        this.emit('auto_reflex_done', {
          metric: entry.id,
          command: entry.command,
          status: 'error' as const,
          improved: false,
          stuck: false,
        })
        this._isRunning = false
        this.activeEntry = null
        this._drainQueue()
        return
      }
    }

    // Verify-gate (2a): for a batch metric (code-modifying skill, no deterministic
    // mechanism), re-measure the breach LIVE before spending an expensive skill spawn.
    // Runs against the live tree BEFORE any staging worktree exists — cheap, read-only,
    // and short-circuits a stale-snapshot phantom without touching git. Fail-open. Kill
    // switch: REFLEX_VERIFY_GATE=false.
    this._metricBefore = null
    if (VERIFY_GATE_ENABLED) {
      const gateMetric = entry.id.split(':')[2] ?? ''
      if (gateMetric && this._isBatchMetric(gateMetric) && !this._runVerifyGate(entry, gateMetric)) {
        this.emit('auto_reflex_output', {
          metric: entry.id,
          line: `[verify-gate] ${gateMetric} recovered on live re-measure — suppressing skill spawn (no_change).`,
        })
        this._suppressAsPhantom(entry, key)
        return
      }
    }

    // Fire-time before-value (§A1): when the verify-gate didn't run (non-batch metric,
    // gate disabled) or couldn't read a value, fall back to the wid_payload snapshot —
    // the very evidence that fired this reflex. Manual runs carry no metric id.
    if (!entry.manual && this._metricBefore === null) {
      const metricRef = metricFromKey(key)
      if (metricRef) {
        this._metricBefore = this._metricValueFromPayload(metricRef.pillar, metricRef.metric)
      }
    }

    // Snapshot pre-run state for real improvement detection (#16) and audit (#31)
    this._preRunReflexIds = new Set(readWid().map(r => r.id))
    try {
      const gitHead = spawnSync('git', ['-C', ORDER_SAMURAI_ROOT, 'rev-parse', 'HEAD'], { encoding: 'utf8' })
      this._beforeCommit = gitHead.stdout?.trim() || null
    } catch {
      this._beforeCommit = null
    }

    // Cooldown is set in _afterRun (on completion), NOT here. Setting it at spawn-start
    // would lock out a CRITICAL reflex for 30 min on a transient spawn error.

    this.emit('auto_reflex_start', {
      metric: entry.id,
      tier: entry.tier,
      command: entry.command,
      category: entry.category,
      message: entry.message,
    })

    // Track whether a mechanism attempt preceded this skill run (for exec_log kind tagging).
    const mechFallback: 'mechanism' | undefined = entry.mechanism ? 'mechanism' : undefined

    // Grading-only run (Part B): under the grant flag, an ungranted read-only mechanism
    // runs ONLY to earn its grade — it records the run and returns, never escalating to
    // the skill fallback (which may modify code). Mirrors _isEligible's mayRunForGrading.
    const grantedToRemediate =
      entry.maturity === undefined || entry.maturity === 'APPLY' || entry.reflex_ready === true
    const gradingOnly =
      REFLEX_REQUIRE_GRANT && !grantedToRemediate && entry.mechanism?.read_only === true

    // Create temporary git worktree for staging if command is code-modifying
    let worktreeDir: string | undefined = undefined
    const isCodeModifying = !this._isSkillReadonly(entry.command) || (entry.mechanism && !entry.mechanism.read_only)
    if (isCodeModifying) {
      worktreeDir = path.join(ORDER_SAMURAI_ROOT, '.tmp', 'worktrees', `remediation_${Date.now()}`)
      try {
        fs.mkdirSync(path.dirname(worktreeDir), { recursive: true })
        const wtAdd = spawnSync('git', ['-C', ORDER_SAMURAI_ROOT, 'worktree', 'add', '--detach', worktreeDir], { encoding: 'utf8' })
        if (wtAdd.status !== 0) {
          this.emit('auto_reflex_output', {
            metric: entry.id,
            line: `[Staging] Failed to create git worktree: ${wtAdd.stderr || wtAdd.error?.message}`,
          })
          this._afterRun(entry, key, 'error', 'skill', mechFallback, undefined)
          return
        }

        // Copy untracked files
        const untrackedResult = spawnSync('git', ['-C', ORDER_SAMURAI_ROOT, 'ls-files', '--others', '--exclude-standard'], { encoding: 'utf8' })
        if (untrackedResult.status === 0) {
          const files = untrackedResult.stdout.split('\n').map(f => f.trim()).filter(Boolean)
          for (const file of files) {
            const src = path.join(ORDER_SAMURAI_ROOT, file)
            const dest = path.join(worktreeDir, file)
            if (fs.existsSync(src)) {
              fs.mkdirSync(path.dirname(dest), { recursive: true })
              fs.copyFileSync(src, dest)
            }
          }
        }
        if (fs.existsSync(path.join(ORDER_SAMURAI_ROOT, 'state'))) {
          fs.cpSync(path.join(ORDER_SAMURAI_ROOT, 'state'), path.join(worktreeDir, 'state'), { recursive: true })
        }
        if (fs.existsSync(path.join(ORDER_SAMURAI_ROOT, '.claude'))) {
          fs.cpSync(path.join(ORDER_SAMURAI_ROOT, '.claude'), path.join(worktreeDir, '.claude'), { recursive: true })
        }
      } catch (err) {
        this.emit('auto_reflex_output', {
          metric: entry.id,
          line: `[Staging] Warning: error copying workspace state to worktree: ${String(err)}`,
        })
      }
    }

    // If a mechanism is declared, attempt it first (explicit timeout, no LLM call).
    // On success, skip the skill spawn. On failure, fall through to the skill path.
    if (entry.mechanism) {
      const mechResult = await this._runMechanism(entry, worktreeDir)
      if (gradingOnly) {
        this._afterRun(entry, key, mechResult === 'done' ? 'done' : 'error', 'mechanism', undefined, worktreeDir)
        return  // grade only — never escalate to the skill
      }
      if (mechResult === 'done') {
        this._afterRun(entry, key, 'done', 'mechanism', undefined, worktreeDir)
        return
      }
      this.emit('auto_reflex_output', {
        metric: entry.id,
        line: `mechanism exited with ${mechResult} — falling through to skill`,
      })
    }

    const [cmd, args] = buildSkillSpawnArgs(this.claudeBin, entry.command)

    // Wrap spawn to catch synchronous throws (EACCES, ERR_INVALID_ARG, EMFILE) that
    // would bypass the 'error' event handler and leave _isRunning permanently true.
    let child: ReturnType<typeof spawn>
    try {
      child = spawn(cmd, args, {
        cwd: worktreeDir || ORDER_SAMURAI_ROOT,
        stdio: ['ignore', 'pipe', 'pipe'],
        env: { ...process.env },
      })
    } catch (err) {
      this.emit('auto_reflex_output', { metric: entry.id, line: `spawn failed: ${String(err)}` })
      this._afterRun(entry, key, 'error', 'skill', mechFallback, worktreeDir)
      return
    }

    let settled = false
    let timedOut = false
    let escalateTimer: ReturnType<typeof setTimeout> | null = null
    let forceTimer: ReturnType<typeof setTimeout> | null = null

    const killTimer = setTimeout(() => {
      timedOut = true
      try { child.kill() } catch { /* SIGTERM — ignore if already gone */ }

      // Escalation: if SIGTERM is ignored (e.g. cmd.exe wrapper doesn't propagate the
      // signal to the claude subprocess), SIGKILL / taskkill the process tree after 5 s.
      // Then force-settle 2 s later so _isRunning never gets permanently stuck.
      escalateTimer = setTimeout(() => {
        if (settled) return
        if (process.platform === 'win32' && child.pid != null) {
          try {
            spawn('taskkill', ['/F', '/T', '/PID', String(child.pid)], { stdio: 'ignore' })
          } catch { /* ignore */ }
        } else {
          try { child.kill('SIGKILL') } catch { /* ignore */ }
        }
        forceTimer = setTimeout(() => {
          if (settled) return
          settled = true
          this.emit('auto_reflex_output', {
            metric: entry.id,
            line: '[force-killed: process tree did not exit after SIGKILL]',
          })
          this._afterRun(entry, key, 'timeout', 'skill', mechFallback, worktreeDir)
        }, 2000)
        forceTimer.unref()
      }, 5000)
      escalateTimer.unref()
    }, EXEC_TIMEOUT_MS)

    let outputBuffer = ''
    const handleData = (data: Buffer) => {
      const chunk = data.toString()
      outputBuffer += chunk
      const lines = chunk.split('\n').filter((l) => l.trim())
      for (const line of lines) {
        this.emit('auto_reflex_output', { metric: entry.id, line: line.slice(0, 400) })
      }
    }

    child.stdout?.on('data', handleData)
    child.stderr?.on('data', handleData)

    child.on('close', (code) => {
      if (settled) return
      settled = true
      clearTimeout(killTimer)
      if (escalateTimer) clearTimeout(escalateTimer)
      if (forceTimer) clearTimeout(forceTimer)

      const isLimit = code !== 0 && /5-hour limit|weekly limit|quota exceeded|limit reached|rate limit/i.test(outputBuffer)
      if (isLimit) {
        this.emit('auto_reflex_output', {
          metric: entry.id,
          line: '⚠️ Claude Code CLI limit reached — deferring this remediation until the limit window resets.',
        })
        // No secondary model in the public build: treat a quota limit as an incomplete run
        // ('timeout') so the lenient incomplete budget retries it later instead of parking it
        // as a hard failure.
        this._afterRun(entry, key, 'timeout', 'skill', mechFallback, worktreeDir)
        return
      }

      // Turn-budget exhaustion ("Error: Reached max turns (N)") exits non-zero, but the skill
      // did NOT fail — it just didn't finish. Classify as 'timeout' (incomplete), not 'error',
      // so the loop-breaker doesn't park a slow-but-working skill after two runs.
      const hitTurnCap = code !== 0 && /reached max turns/i.test(outputBuffer)

      let runStatus: 'done' | 'error' | 'timeout'
      if (timedOut || hitTurnCap) {
        runStatus = 'timeout'
      } else if (code === 0) {
        runStatus = 'done'
      } else {
        runStatus = 'error'
      }

      this._afterRun(entry, key, runStatus, 'skill', mechFallback, worktreeDir)
    })

    child.on('error', (err) => {
      if (settled) return
      settled = true
      clearTimeout(killTimer)
      if (escalateTimer) clearTimeout(escalateTimer)
      if (forceTimer) clearTimeout(forceTimer)
      this.emit('auto_reflex_output', { metric: entry.id, line: `spawn error: ${err.message}` })
      this._afterRun(entry, key, 'error', 'skill', mechFallback, worktreeDir)
    })
  }

  // -------------------------------------------------------------------------
  // Internal — post-run logic (loop-breaker + exec log)
  // -------------------------------------------------------------------------

  private _afterRun(
    entry: ReflexEntry,
    key: string,
    runStatus: 'done' | 'error' | 'timeout',
    kind: 'skill' | 'mechanism' = 'skill',
    fallbackFrom?: 'mechanism',
    worktreeDir?: string,
  ): void {
    // Arm cooldown at completion — moving it here (instead of at spawn-start) means a
    // transient error doesn't lock out a CRITICAL reflex for the full 30-minute window.
    this.cooldowns.set(key, { firedAt: Date.now() })

    let finalStatus = runStatus

    // Staging / Maker-Checker / pytest validation for code-modifying reflexes
    if (worktreeDir && runStatus === 'done') {
      this.emit('auto_reflex_output', {
        metric: entry.id,
        line: `[Staging] Staging run completed. Starting Maker-Checker audit and pytest verification...`,
      })

      // 1. Generate patch
      spawnSync('git', ['-C', worktreeDir, 'add', '-N', '.'], { encoding: 'utf8' })
      const diffResult = spawnSync('git', ['-C', worktreeDir, 'diff'], { encoding: 'utf8', maxBuffer: 10 * 1024 * 1024 })
      const patchContent = diffResult.stdout || ''
      const patchFile = path.join(worktreeDir, 'remediation.patch')
      fs.writeFileSync(patchFile, patchContent, 'utf8')

      // 2. Maker-Checker Audit
      const pythonBin = process.platform === 'win32' ? 'python' : 'python3'
      const auditScript = path.join(ORDER_SAMURAI_ROOT, 'execution', 'audit_remediation_patch.py')
      
      this.emit('auto_reflex_output', {
        metric: entry.id,
        line: `[Staging] Running security audit on patch (size: ${patchContent.length} bytes)...`,
      })

      const auditResult = spawnSync(pythonBin, [auditScript, '--patch', patchFile], {
        cwd: ORDER_SAMURAI_ROOT,
        encoding: 'utf8',
        env: { ...process.env, GOVERNANCE_ROOT }
      })
      const auditApproved = auditResult.status === 0

      if (auditResult.stdout) {
        this.emit('auto_reflex_output', { metric: entry.id, line: `[Staging] Audit output: ${auditResult.stdout.trim()}` })
      }
      if (auditResult.stderr) {
        this.emit('auto_reflex_output', { metric: entry.id, line: `[Staging] Audit stderr: ${auditResult.stderr.trim()}` })
      }

      // 3. Verification Loop (pytest)
      let pytestPassed = false
      if (auditApproved) {
        this.emit('auto_reflex_output', {
          metric: entry.id,
          line: `[Staging] Security audit approved. Running pytest verification suite in worktree...`,
        })

        const pytestResult = spawnSync(pythonBin, ['-m', 'pytest', 'tests/', '-q'], {
          cwd: worktreeDir,
          encoding: 'utf8',
          timeout: 120_000,
        })
        pytestPassed = pytestResult.status === 0

        if (pytestResult.stdout) {
          this.emit('auto_reflex_output', { metric: entry.id, line: `[Staging] Pytest output: ${pytestResult.stdout.trim()}` })
        }
        if (pytestResult.stderr) {
          this.emit('auto_reflex_output', { metric: entry.id, line: `[Staging] Pytest stderr: ${pytestResult.stderr.trim()}` })
        }
      } else {
        this.emit('auto_reflex_output', {
          metric: entry.id,
          line: `[Staging] Security audit REJECTED. Skipping pytest verification.`,
        })
      }

      if (auditApproved && pytestPassed && !AUTO_APPLY_ENABLED) {
        // Review-only mode (public-safe default): the patch passed the audit + pytest gate but
        // auto-apply is disabled, so the live repo is left UNTOUCHED and the validated patch is
        // saved for a human to review/apply. finalStatus stays 'done' (the skill genuinely
        // produced a valid patch); the real-improvement check sees the metric still breached, so
        // `improved` is false and the loop-breaker parks the reflex after a couple of proposals
        // instead of regenerating a patch on every poll. No error-metric pollution.
        try {
          const pendingPatchPath = path.join(ORDER_SAMURAI_ROOT, 'state', `pending_remediation_${entry.id.replace(/[^A-Za-z0-9_-]/g, '_')}.patch`)
          fs.mkdirSync(path.dirname(pendingPatchPath), { recursive: true })
          fs.writeFileSync(pendingPatchPath, patchContent, 'utf8')
          this.emit('auto_reflex_output', {
            metric: entry.id,
            line: `[Staging] Validation succeeded, but auto-apply is disabled (REFLEX_AUTO_APPLY=false). Validated patch saved for review at ${pendingPatchPath} — live repo left unchanged.`,
          })
        } catch (err) {
          this.emit('auto_reflex_output', {
            metric: entry.id,
            line: `[Staging] Validation succeeded but saving the pending patch failed: ${String(err)}`,
          })
        }
      } else if (auditApproved && pytestPassed) {
        this.emit('auto_reflex_output', {
          metric: entry.id,
          line: `[Staging] Validation succeeded. Applying patch to main repository...`,
        })

        // Apply patch to main repo
        const applyResult = spawnSync('git', ['-C', ORDER_SAMURAI_ROOT, 'apply', patchFile], { encoding: 'utf8' })
        if (applyResult.status === 0) {
          // Copy untracked files
          const wtUntracked = spawnSync('git', ['-C', worktreeDir, 'ls-files', '--others', '--exclude-standard'], { encoding: 'utf8' })
          if (wtUntracked.status === 0) {
            const files = wtUntracked.stdout.split('\n').map(f => f.trim()).filter(Boolean)
            for (const file of files) {
              const src = path.join(worktreeDir, file)
              const dest = path.join(ORDER_SAMURAI_ROOT, file)
              if (fs.existsSync(src)) {
                fs.mkdirSync(path.dirname(dest), { recursive: true })
                fs.copyFileSync(src, dest)
              }
            }
          }
          // Copy state folder
          if (fs.existsSync(path.join(worktreeDir, 'state'))) {
            fs.cpSync(path.join(worktreeDir, 'state'), path.join(ORDER_SAMURAI_ROOT, 'state'), { recursive: true })
          }
          this.emit('auto_reflex_output', {
            metric: entry.id,
            line: `[Staging] Patch applied and files copied successfully.`,
          })
          // Delete stale failed patch if exists
          try {
            const failedPatchPath = path.join(ORDER_SAMURAI_ROOT, 'state', `failed_remediation_${entry.id.replace(/[^A-Za-z0-9_-]/g, '_')}.patch`)
            if (fs.existsSync(failedPatchPath)) {
              fs.unlinkSync(failedPatchPath)
            }
          } catch (err) {}
        } else {
          this.emit('auto_reflex_output', {
            metric: entry.id,
            line: `[Staging] Failed to apply git patch to main repo: ${applyResult.stderr || applyResult.error?.message}`,
          })
          finalStatus = 'error'
          // Save failed patch to persistent state folder for backlog tickets
          try {
            const failedPatchPath = path.join(ORDER_SAMURAI_ROOT, 'state', `failed_remediation_${entry.id.replace(/[^A-Za-z0-9_-]/g, '_')}.patch`)
            fs.mkdirSync(path.dirname(failedPatchPath), { recursive: true })
            fs.writeFileSync(failedPatchPath, patchContent, 'utf8')
          } catch (err) {}
        }
      } else {
        this.emit('auto_reflex_output', {
          metric: entry.id,
          line: `[Staging] Remediation validation failed. Patch discarded.`,
        })
        finalStatus = 'error'
        // Save failed patch to persistent state folder for backlog tickets
        try {
          const failedPatchPath = path.join(ORDER_SAMURAI_ROOT, 'state', `failed_remediation_${entry.id.replace(/[^A-Za-z0-9_-]/g, '_')}.patch`)
          fs.mkdirSync(path.dirname(failedPatchPath), { recursive: true })
          fs.writeFileSync(failedPatchPath, patchContent, 'utf8')
        } catch (err) {}
      }
    }

    // Clean up git worktree if created
    if (worktreeDir) {
      try {
        spawnSync('git', ['-C', ORDER_SAMURAI_ROOT, 'worktree', 'remove', '--force', worktreeDir], { encoding: 'utf8' })
      } catch (err) {
        this.emit('auto_reflex_output', {
          metric: entry.id,
          line: `[Staging] Failed to remove git worktree: ${String(err)}`,
        })
      }
    }

    // After a successful run, synchronously regenerate wid_payload.json so that the
    // reflex-presence check below reflects fresh metric data. spawnSync blocks until
    // refresh completes (≤60 s), which is acceptable because _isRunning=true already
    // serializes all skill execution during this window.
    if (finalStatus === 'done') {
      const pythonBin = process.platform === 'win32' ? 'python' : 'python3'
      try {
        spawnSync(pythonBin, [path.join(GOVERNANCE_ROOT, 'refresh_dashboard.py')], {
          cwd: GOVERNANCE_ROOT,
          timeout: 60_000,
          env: { ...process.env },
        })
      } catch (err) {
        this.emit('auto_reflex_output', {
          metric: entry.id,
          line: `[refresh_dashboard spawn failed: ${String(err)}]`,
        })
      }

      // On a successful MECHANISM run, emit a routing-efficient mechanism_run event so the
      // hero metrics credit the deterministic path (Agent-Time-Saved benchmarks by `kind`;
      // routing efficiency is surfaced as a count). Fire-and-forget — a failed emit must
      // never fail the reflex, so we never inspect its exit status.
      if (kind === 'mechanism' && entry.mechanism) {
        const pillar = entry.id.split(':')[1] ?? ''
        const emitArgs = [
          path.join(ORDER_SAMURAI_ROOT, 'bin', 'emit_event.py'),
          'mechanism_run',
          '--detail', `${entry.id} via ${entry.mechanism.script}`,
          '--routing-efficient',
          '--kind', 'mechanism',
        ]
        if (['bow', 'sword', 'brush', 'arts'].includes(pillar)) {
          emitArgs.push('--pillar', pillar)
        }
        try {
          spawnSync(pythonBin, emitArgs, {
            cwd: ORDER_SAMURAI_ROOT,
            timeout: 15_000,
            env: { ...process.env },
          })
        } catch (err) {
          this.emit('auto_reflex_output', {
            metric: entry.id,
            line: `[emit_event mechanism_run failed: ${String(err)}]`,
          })
        }
      }
    }

    // Post-run after-value (§A1): pairs with the fire-time before-value so the
    // exec_log row carries a real per-run measurement. 'done' just refreshed the
    // payload — read it; every other outcome re-measures live (the payload may
    // predate the run), falling back to the snapshot if the gate can't measure.
    let metricAfter: number | null = null
    if (!entry.manual && this._metricBefore !== null) {
      const metricRef = metricFromKey(key)
      if (metricRef) {
        metricAfter = finalStatus === 'done'
          ? this._metricValueFromPayload(metricRef.pillar, metricRef.metric)
          : this._runRemeasure(entry, metricRef.metric).value
        if (metricAfter === null) {
          metricAfter = this._metricValueFromPayload(metricRef.pillar, metricRef.metric)
        }
      }
    }

    // Real improvement detection (#16): check whether this reflex was resolved
    // (removed from wid_payload) after the synchronous refresh above.
    // Fallback to exit-code proxy if pre-run snapshot is unavailable.
    let improved: boolean
    if (this._preRunReflexIds !== null) {
      if (finalStatus === 'done') {
        const postRunIds = new Set(readWid().map(r => r.id))
        // Improved = reflex existed before AND is gone now
        improved = this._preRunReflexIds.has(entry.id) && !postRunIds.has(entry.id)
      } else {
        improved = false
      }
    } else {
      improved = finalStatus === 'done'
    }
    this._preRunReflexIds = null

    // Update loop-breaker state
    if (!this.noImprovement.has(key)) {
      this.noImprovement.set(key, { consecutive: 0, incompleteConsecutive: 0, stuck: false })
    }
    const noImp = this.noImprovement.get(key)!
    if (noImp.incompleteConsecutive === undefined) noImp.incompleteConsecutive = 0

    // Loop-breaker progress: a read-only detect mechanism never moves its own metric
    // (improved is always false), so judging it by `improved` would freeze it after
    // LOOP_BREAKER_LIMIT clean runs — before it can earn the graded runs needed to
    // promote. A clean read-only mechanism run (exit 0) IS its success, so it counts
    // as progress here. `improved` stays honest for exec_log/efficacy (metric unmoved).
    const cleanReadonlyMech =
      kind === 'mechanism' && finalStatus === 'done' && entry.mechanism?.read_only === true
    const loopBreakerProgress = improved || cleanReadonlyMech

    // An incomplete run (turn-cap / wall-clock 'timeout') means the skill was working but did
    // not finish. Count it against a separate, more lenient budget (INCOMPLETE_LIMIT) so a
    // slow-but-working skill isn't parked as if it errored — while still bounding endless
    // non-finishing retries. Genuine failures ('error') and no-op successes use the hard counter.
    const incomplete = finalStatus === 'timeout' && !loopBreakerProgress

    if (loopBreakerProgress) {
      noImp.consecutive = 0
      noImp.incompleteConsecutive = 0
    } else if (incomplete) {
      noImp.incompleteConsecutive += 1
    } else {
      noImp.consecutive += 1
    }

    const stuckReason =
      noImp.consecutive >= LOOP_BREAKER_LIMIT ? 'loop_breaker'
      : (noImp.incompleteConsecutive ?? 0) >= INCOMPLETE_LIMIT ? 'loop_breaker_incomplete'
      : null
    if (stuckReason && !noImp.stuck) {
      noImp.stuck = true
      // Prune from queue
      this.queue = this.queue.filter(q => reflexKey(q.id, q.command) !== key)

      this.emit('auto_reflex_stuck', {
        metric: entry.id,
        command: entry.command,
        consecutiveNoImprovement: noImp.consecutive,
        reason: stuckReason,
      })
      // Escalate to external webhook if configured (#G4) — non-fatal, dashboard-independent
      const webhookUrl = process.env['ESCALATION_WEBHOOK_URL']
      if (webhookUrl) {
        this._sendEscalation(webhookUrl, {
          metric: entry.id,
          command: entry.command,
          consecutiveNoImprovement: noImp.consecutive,
          timestamp: new Date().toISOString(),
        }).catch(() => { /* non-fatal — webhook failure must never crash the engine */ })
      }
    }

    // Persist updated cooldown + stuck state (#27)
    this._saveState()

    this.emit('auto_reflex_done', {
      metric: entry.id,
      command: entry.command,
      status: finalStatus,
      improved,
      stuck: noImp.stuck,
    })

    // Append to exec log (includes git diff if available) — must run before nulling _beforeCommit
    this._appendExecLog(entry, finalStatus, improved, kind, fallbackFrom,
                        this._metricBefore, metricAfter)

    // Phase 3.4: if Bushido tracked a queue_id for this reflex (consumed approval
    // or enqueued-then-AUTO), mark it done/failed so the HITL queue stays clean.
    const bushidoId = this._bushidoQueueIds.get(key)
    if (bushidoId) {
      this._bushidoQueueIds.delete(key)
      const failed = finalStatus !== 'done'
      try {
        const pythonBin = process.platform === 'win32' ? 'python' : 'python3'
        const script = path.join(ORDER_SAMURAI_ROOT, 'bin', 'bushido_check.py')
        const args = [script, '--complete', bushidoId]
        if (failed) args.push('--failed')
        spawnSync(pythonBin, args, {
          cwd: ORDER_SAMURAI_ROOT,
          encoding: 'utf8',
          timeout: 10_000,
          env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
        })
      } catch (err) {
        this.emit('auto_reflex_output', {
          metric: entry.id,
          line: `[bushido] --complete ${bushidoId} failed: ${String(err)}`,
        })
      }
    }

    // Reset active state and drain
    this._isRunning = false
    this.activeEntry = null
    this._beforeCommit = null
    this._metricBefore = null
    this._drainQueue()
  }

  private _appendExecLog(
    entry: ReflexEntry,
    status: 'done' | 'error' | 'timeout',
    improved: boolean,
    kind: 'skill' | 'mechanism' = 'skill',
    fallbackFrom?: 'mechanism',
    metricBefore?: number | null,
    metricAfter?: number | null,
  ): void {
    const logPath = path.join(ORDER_SAMURAI_ROOT, 'state', 'exec_log.jsonl')
    const skillWord = entry.command.trim().replace(/^\//, '').split(/\s+/)[0] ?? ''

    // Capture git diff summary for auditability (#31) — non-fatal if git unavailable
    let gitDiffSummary: string | null = null
    let filesChanged: string[] = []
    if (this._beforeCommit) {
      try {
        const diff = spawnSync(
          'git', ['-C', ORDER_SAMURAI_ROOT, 'diff', '--shortstat', this._beforeCommit],
          { encoding: 'utf8', timeout: 10_000 },
        )
        gitDiffSummary = diff.stdout?.trim() || null

        const names = spawnSync(
          'git', ['-C', ORDER_SAMURAI_ROOT, 'diff', '--name-only', this._beforeCommit],
          { encoding: 'utf8', timeout: 10_000 },
        )
        filesChanged = names.stdout?.trim().split('\n').filter(Boolean) ?? []
      } catch {
        // Non-fatal — not a git repo or git not on PATH
      }
    }

    const record: Record<string, unknown> = {
      timestamp: new Date().toISOString(),
      command: entry.command,
      skill: skillWord,
      status,
      improved,  // true = reflex was resolved (metric moved past threshold); enables learning-loop analytics
      code_modifying: !this._isSkillReadonly(entry.command),  // true when skill has edit capabilities (used by rival post-auditor)
      kind,
      source: 'reflex_engine',
      reflex_id: entry.id,
      ...(kind === 'mechanism' && { read_only: entry.mechanism?.read_only }),
      // Fire-time before/after metric values (§A1). remediation.py builds efficacy
      // events directly from these instead of correlating against the sparse
      // metrics_history snapshots. Omitted when unmeasurable (non-metric reflex,
      // unreadable payload) — consumers treat absence as "no measurement".
      ...(metricBefore !== undefined && metricBefore !== null && { metric_before: metricBefore }),
      ...(metricAfter !== undefined && metricAfter !== null && { metric_after: metricAfter }),
      ...(fallbackFrom !== undefined && { fallback_from: fallbackFrom }),
      ...(this._beforeCommit !== null && { git_before_commit: this._beforeCommit }),
      ...(gitDiffSummary !== null && { git_diff_summary: gitDiffSummary }),
      ...(filesChanged.length > 0 && { files_changed: filesChanged }),
    }

    try {
      fs.appendFileSync(logPath, JSON.stringify(record) + '\n', 'utf8')
    } catch {
      // Non-fatal — log dir may not exist yet
    }
  }

  // -------------------------------------------------------------------------
  // Internal — mechanism spawn (deterministic path, no LLM)
  // -------------------------------------------------------------------------

  /** Spawn a deterministic bin/ script and resolve with its outcome.
   *  Mirrors the skill-spawn kill-ladder shape (SIGTERM → SIGKILL → force) on a deterministic bin/ script.
   *  Caller is responsible for falling through to the skill path on non-'done' result. */
  private _runMechanism(entry: ReflexEntry, runCwd?: string): Promise<'done' | 'error' | 'timeout'> {
    return new Promise((resolve) => {
      const mech = entry.mechanism!
      const pythonBin = process.platform === 'win32' ? 'python' : 'python3'
      const targetCwd = runCwd || ORDER_SAMURAI_ROOT
      const scriptPath = path.join(targetCwd, 'bin', mech.script)
      const MECH_TIMEOUT_MS = (mech.timeout_s ?? 120) * 1_000

      let mechChild: ReturnType<typeof spawn>
      try {
        mechChild = spawn(pythonBin, [scriptPath, ...mech.args], {
          cwd: targetCwd,
          stdio: ['ignore', 'pipe', 'pipe'],
          // PYTHONIOENCODING forces UTF-8 stdio so a mechanism printing non-cp1252
          // Unicode (e.g. ↔) can't crash with UnicodeEncodeError on Windows and
          // wrongly read as a failed run (see subagent_audit.py, 2026-06-18).
          env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
        })
      } catch (err) {
        this.emit('auto_reflex_output', { metric: entry.id, line: `mechanism spawn failed: ${String(err)}` })
        resolve('error')
        return
      }

      let settled = false
      let timedOut = false
      let escalateTimer: ReturnType<typeof setTimeout> | null = null
      let forceTimer: ReturnType<typeof setTimeout> | null = null

      const killTimer = setTimeout(() => {
        timedOut = true
        try { mechChild.kill() } catch { /* SIGTERM */ }
        escalateTimer = setTimeout(() => {
          if (settled) return
          if (process.platform === 'win32' && mechChild.pid != null) {
            try { spawn('taskkill', ['/F', '/T', '/PID', String(mechChild.pid)], { stdio: 'ignore' }) } catch { /* ignore */ }
          } else {
            try { mechChild.kill('SIGKILL') } catch { /* ignore */ }
          }
          forceTimer = setTimeout(() => {
            if (settled) return
            settled = true
            this.emit('auto_reflex_output', { metric: entry.id, line: '[mechanism force-killed after SIGKILL]' })
            resolve('timeout')
          }, 2000)
          forceTimer.unref()
        }, 5000)
        escalateTimer.unref()
      }, MECH_TIMEOUT_MS)

      const handleData = (data: Buffer) => {
        const lines = data.toString().split('\n').filter((l) => l.trim())
        for (const line of lines) {
          this.emit('auto_reflex_output', { metric: entry.id, line: line.slice(0, 400) })
        }
      }
      mechChild.stdout?.on('data', handleData)
      mechChild.stderr?.on('data', handleData)

      mechChild.on('close', (code) => {
        if (settled) return
        settled = true
        clearTimeout(killTimer)
        if (escalateTimer) clearTimeout(escalateTimer)
        if (forceTimer) clearTimeout(forceTimer)
        resolve(timedOut ? 'timeout' : code === 0 ? 'done' : 'error')
      })

      mechChild.on('error', (err) => {
        if (settled) return
        settled = true
        clearTimeout(killTimer)
        if (escalateTimer) clearTimeout(escalateTimer)
        if (forceTimer) clearTimeout(forceTimer)
        this.emit('auto_reflex_output', { metric: entry.id, line: `mechanism error: ${err.message}` })
        resolve('error')
      })
    })
  }

  // -------------------------------------------------------------------------
  // Internal — approval window (#G1)
  // -------------------------------------------------------------------------

  /** Resolve to true when the approval window expires (auto-approve) or false when cancelled. */
  private _waitForApproval(key: string, windowMs: number): Promise<boolean> {
    return new Promise((resolve) => {
      const deadline = Date.now() + windowMs
      const tick = setInterval(() => {
        if (this._pendingCancels.has(key)) {
          clearInterval(tick)
          resolve(false)  // operator cancelled
        } else if (Date.now() >= deadline) {
          clearInterval(tick)
          resolve(true)   // window expired — auto-approve
        }
      }, 1000)
    })
  }

  // -------------------------------------------------------------------------
  // Internal — skill efficacy dynamic cooldown (#G2)
  // -------------------------------------------------------------------------

  /** Return the cooldown multiplier for a given skill command.
   *  Reads skill_efficacy.json (written by refresh_dashboard.py), cached 60 s.
   *
   *  Tiers (mirrors skill_efficacy.py constants):
   *    no history (not in JSON) → 0.25  → 7.5 min  (optimistic first attempt)
   *    < 3 runs (warmup)        → 0.25  → 7.5 min  (insufficient data)
   *    ≥ 3 runs, ≥30% success   → 1.0   → 30 min   (proven baseline)
   *    ≥ 3 runs, <30% success   → 3.0   → 90 min   (consistently failing)
   */
  private _getCooldownMultiplier(command: string): number {
    const now = Date.now()
    if (!this._efficacyCache || now - this._efficacyCache.loadedAt > 60_000) {
      try {
        const raw = fs.readFileSync(
          path.join(ORDER_SAMURAI_ROOT, 'state', 'skill_efficacy.json'), 'utf8')
        this._efficacyCache = { data: JSON.parse(raw) as Record<string, { cooldown_multiplier: number }>, loadedAt: now }
      } catch {
        // File absent (first run) or corrupt — treat as no history
        this._efficacyCache = { data: {}, loadedAt: now }
      }
    }
    const skillName = command.trim().replace(/^\//, '').split(/\s+/)[0] ?? ''
    // Prefer the mechanism's own record ("<skill>::mechanism") when present: a
    // determinized read-only mechanism grades on its exit-0 reliability, not the
    // retired LLM skill's improved-based failures, so its cooldown must not inherit
    // the skill's penalty. Its presence means the mechanism has run.
    const mechRec = this._efficacyCache.data[`${skillName}::mechanism`]
    if (mechRec) return mechRec.cooldown_multiplier
    // No entry = skill has never run = no history → warmup multiplier (0.25)
    return this._efficacyCache.data[skillName]?.cooldown_multiplier ?? 0.25
  }

  // -------------------------------------------------------------------------
  // Internal — skill metadata & tiered autonomy (#blast-radius)
  // -------------------------------------------------------------------------

  /** Return true if the skill command is classified as readonly (diagnostic/reporting only).
   *  Readonly skills are always autonomous regardless of REFLEX_CODE_APPROVAL_MS.
   *  Classification sourced from skill_metadata.json written by refresh_dashboard.py.
   *  Cached for 60 seconds to avoid per-check I/O. */
  private _isSkillReadonly(command: string): boolean {
    const now = Date.now()
    if (!this._readonlySkillsCache || now - this._readonlySkillsCache.loadedAt > 60_000) {
      try {
        const raw = fs.readFileSync(
          path.join(ORDER_SAMURAI_ROOT, 'state', 'skill_metadata.json'), 'utf8')
        const parsed = JSON.parse(raw) as { readonly?: string[] }
        this._readonlySkillsCache = {
          data: new Set(parsed.readonly ?? []),
          loadedAt: now,
        }
      } catch {
        // File absent on first run (before refresh_dashboard.py writes it) — default to
        // code-modifying (safer: over-gating is better than under-gating)
        this._readonlySkillsCache = { data: new Set(), loadedAt: now }
      }
    }
    const skillName = command.trim().replace(/^\//, '').split(/\s+/)[0] ?? ''
    return this._readonlySkillsCache.data.has(skillName)
  }

  /** Return true if this metric name is marked auto_remediable=False in METRIC_CONFIG.
   *  Such metrics show alert cards and allow manual skill execution, but the ReflexEngine
   *  never queues them autonomously — they require a human action to resolve.
   *  Sourced from non_remediable_metrics.json written by refresh_dashboard.py.
   *  Cached for 60 seconds. */
  private _isNonRemediable(metricName: string): boolean {
    const now = Date.now()
    if (!this._nonRemediableCache || now - this._nonRemediableCache.loadedAt > 60_000) {
      try {
        const raw = fs.readFileSync(
          path.join(ORDER_SAMURAI_ROOT, 'state', 'non_remediable_metrics.json'), 'utf8')
        this._nonRemediableCache = {
          data: new Set(JSON.parse(raw) as string[]),
          loadedAt: now,
        }
      } catch {
        // File absent before first refresh_dashboard.py run — treat all as remediable
        this._nonRemediableCache = { data: new Set(), loadedAt: now }
      }
    }
    return this._nonRemediableCache.data.has(metricName)
  }

  /** True when this metric is in the batch-deferred set (code-modifying agent remediation,
   *  no deterministic mechanism, not urgent). Sourced from batch_metrics.json, cached 60 s.
   *  Absent file (before first refresh) → empty set → no metric is batched (safe: today's
   *  behavior). Drives the verify-gate (_execute) and the batch-defer gate (_isEligible). */
  private _isBatchMetric(metricName: string): boolean {
    const now = Date.now()
    if (!this._batchMetricsCache || now - this._batchMetricsCache.loadedAt > 60_000) {
      try {
        const raw = fs.readFileSync(
          path.join(ORDER_SAMURAI_ROOT, 'state', 'batch_metrics.json'), 'utf8')
        this._batchMetricsCache = { data: new Set(JSON.parse(raw) as string[]), loadedAt: now }
      } catch {
        this._batchMetricsCache = { data: new Set(), loadedAt: now }
      }
    }
    return this._batchMetricsCache.data.has(metricName)
  }

  /** Current time. Indirected so tests can pin the clock for batch-window checks. */
  protected _now(): Date {
    return new Date()
  }

  /** Active batch window (REFLEX_BATCH_WINDOW), or null when disabled. Read per-call so a
   *  runtime env change / test override takes effect without reconstruction. */
  private _batchWindow(): { start: number; end: number } | null {
    return parseBatchWindow(process.env['REFLEX_BATCH_WINDOW'] ?? '')
  }

  /** Synchronously re-measure a batch metric's breach LIVE before spawning its skill.
   *  Runs bin/remeasure_gate.py against the live tree (read-only, ~1–2 s; ≤90 s cap).
   *  Returns TRUE = proceed to the skill, FALSE = suppress (phantom / recovered).
   *
   *  Exit-code contract (from remeasure_gate.py): 0 = phantom → suppress; 1 = still
   *  breaching → proceed; 2 = could-not-measure → proceed. Every non-zero outcome —
   *  real breach, aggregate error, timeout, spawn failure — FAILS OPEN (proceeds), so a
   *  broken gate can never silence autonomy; it can only ADD a suppression on a positive
   *  within-threshold signal. */
  private _runVerifyGate(entry: ReflexEntry, metricName: string): boolean {
    const { exit, value, stderr } = this._runRemeasure(entry, metricName)
    // The gate's live re-measure IS the fire-time before-value — record it so the
    // exec_log row carries a real measurement rather than the (possibly stale) snapshot.
    if (value !== null) this._metricBefore = value
    if (exit === 0) return false  // phantom → suppress the skill spawn
    if (exit !== 1) {
      // 2 / null (timeout|signal|spawn-throw) / anything unexpected — fail-open, but say so.
      this.emit('auto_reflex_output', {
        metric: entry.id,
        line: `[verify-gate] status ${exit ?? 'null'} — proceeding (fail-open)${stderr ? ': ' + stderr.slice(0, 200) : ''}`,
      })
    }
    return true
  }

  /** Spawn bin/remeasure_gate.py --json for one metric and parse its report.
   *  Returns the exit code (null on spawn failure/timeout), the live metric value
   *  when the gate could read one, and trimmed stderr. Shared by the verify-gate
   *  (pre-spawn) and the post-run metric_after measurement in _afterRun. */
  private _runRemeasure(
    entry: ReflexEntry,
    metricName: string,
  ): { exit: number | null; value: number | null; stderr: string } {
    const pythonBin = process.platform === 'win32' ? 'python' : 'python3'
    const script = path.join(ORDER_SAMURAI_ROOT, 'bin', 'remeasure_gate.py')
    let result: ReturnType<typeof spawnSync>
    try {
      result = spawnSync(pythonBin, [script, '--metric', metricName, '--json'], {
        cwd: ORDER_SAMURAI_ROOT,
        encoding: 'utf8',
        timeout: 90_000,
        env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
      })
    } catch (err) {
      this.emit('auto_reflex_output', { metric: entry.id, line: `[verify-gate] spawn threw: ${String(err)} — proceeding (fail-open)` })
      return { exit: null, value: null, stderr: '' }
    }
    let value: number | null = null
    const stdout = result.stdout ? String(result.stdout).trim() : ''
    if (stdout) {
      try {
        const report = JSON.parse(stdout) as { verdict?: unknown; detail?: unknown; value?: unknown }
        if (typeof report.value === 'number' && Number.isFinite(report.value)) value = report.value
        this.emit('auto_reflex_output', {
          metric: entry.id,
          line: `[verify-gate] ${metricName}: ${String(report.verdict ?? 'unknown')}${value !== null ? ` (value ${value})` : ''} — ${String(report.detail ?? '').slice(0, 300)}`,
        })
      } catch {
        // Non-JSON output (older gate build) — surface it raw; no value available.
        this.emit('auto_reflex_output', { metric: entry.id, line: `[verify-gate] ${stdout.slice(0, 400)}` })
      }
    }
    return {
      exit: result.status,
      value,
      stderr: result.stderr ? String(result.stderr).trim() : '',
    }
  }

  /** Numeric value of a graded metric read from the CURRENT wid_payload.json
   *  (pillars.<pillar>.<group>.<Metric>.val). Cheap disk read, no aggregation —
   *  used for the fire-time before-value (the snapshot that triggered the fire)
   *  and the post-'done' after-value (the payload was just refreshed). */
  private _metricValueFromPayload(pillar: string | null, metric: string): number | null {
    const payload = readWidFull()
    if (!payload) return null
    const leaf = findMetricLeaf(payload, pillar, metric)
    if (!leaf) return null
    const v = typeof leaf.val === 'number' ? leaf.val : parseFloat(String(leaf.val).replace(/,/g, ''))
    return Number.isFinite(v) ? v : null
  }

  /** Terminal path when the verify-gate proves a breach is a stale-snapshot phantom.
   *  Arms cooldown (so the still-stale payload can't immediately re-fire it before the
   *  next refresh clears the recovered reflex), counts as loop-breaker progress (nothing
   *  was broken), logs a no_change exec row, and drains — WITHOUT spawning the skill or a
   *  staging worktree. Mirrors the operator-cancel terminal path's bookkeeping. */
  private _suppressAsPhantom(entry: ReflexEntry, key: string): void {
    this.cooldowns.set(key, { firedAt: Date.now() })
    const noImp = this.noImprovement.get(key)
    if (noImp) {
      noImp.consecutive = 0
      noImp.incompleteConsecutive = 0
    }
    this._saveState()
    // NOTE: deliberately NOT written to exec_log. No skill ran — a suppression is a
    // pre-flight decision, so a 'skill'/'no_change' row would wrongly count as a run in
    // skill_efficacy.py and be scanned by rival's post-audit. The auto_reflex_done event +
    // the '[verify-gate] …' reflex_output line are the durable record of the suppression.
    this.emit('auto_reflex_done', {
      metric: entry.id,
      command: entry.command,
      status: 'no_change' as const,
      improved: false,
      stuck: false,
    })
    this._isRunning = false
    this.activeEntry = null
    this._metricBefore = null
    this._drainQueue()
  }

  /** Return the approval window duration for a given skill command.
   *
   *  Priority (first match wins):
   *   1. REFLEX_APPROVAL_WINDOW_MS > 0 → global gate, all skills equally
   *   2. REFLEX_CODE_APPROVAL_MS > 0 AND skill is code-modifying → per-type gate
   *   3. All other cases (readonly skill, both gates disabled) → 0 (fully autonomous)
   */
  private _getApprovalWindowMs(command: string): number {
    if (APPROVAL_WINDOW_MS > 0) return APPROVAL_WINDOW_MS
    if (REFLEX_CODE_APPROVAL_MS > 0 && !this._isSkillReadonly(command)) {
      return REFLEX_CODE_APPROVAL_MS
    }
    return 0
  }

  // -------------------------------------------------------------------------
  // Internal — human escalation (#G4)
  // -------------------------------------------------------------------------

  /** POST a stuck-reflex alert to ESCALATION_WEBHOOK_URL (Slack, Pushover, n8n, etc.).
   *  Uses Node's built-in http/https — no extra dependencies.  Always non-fatal. */
  private async _sendEscalation(url: string, payload: {
    metric: string; command: string; consecutiveNoImprovement: number; timestamp: string
  }): Promise<void> {
    const body = JSON.stringify({
      text: `🚨 Order Samurai stuck: *${payload.metric}* — \`${payload.command}\` failed ${payload.consecutiveNoImprovement}× consecutively`,
      ...payload,
    })
    const mod = url.startsWith('https') ? await import('https') : await import('http')
    await new Promise<void>((resolve, reject) => {
      const parsed = new URL(url)
      const req = mod.request({
        hostname: parsed.hostname,
        port: parsed.port || (url.startsWith('https') ? 443 : 80),
        path: parsed.pathname + parsed.search,
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(body),
        },
        timeout: 10_000,
      }, (res) => {
        res.resume()  // drain the response so the socket closes
        resolve()
      })
      req.on('error', reject)
      req.on('timeout', () => { req.destroy(); reject(new Error('escalation webhook timed out')) })
      req.write(body)
      req.end()
    })
  }
}
