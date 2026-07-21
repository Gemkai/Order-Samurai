import { describe, it, expect, afterEach } from 'vitest'
import { ReflexEngine, parseBatchWindow, inBatchWindow } from './reflex-engine.js'
import type { ReflexEntry } from './reflex-engine.js'

// Coverage for the fire-path verify-gate (2a) + batch-defer routing (2b):
//   - parseBatchWindow / inBatchWindow pure helpers (window spec + wrap-past-midnight),
//   - _isEligible's batch-defer gate isolated via its 'batch_deferred' skip event
//     (emitted BEFORE the Bushido python call, so no subprocess is needed to observe it),
//   - _runVerifyGate fails OPEN on an unmeasurable metric,
//   - _suppressAsPhantom arms cooldown so a still-stale payload can't immediately re-fire.

function batchEntry(overrides: Partial<ReflexEntry> = {}): ReflexEntry {
  return {
    id: 'metric:arts:Doc_Parity_Issues',
    tier: 'CRITICAL',
    command: '/wiki',
    status: 'active',
    source: 'metric',
    ...overrides,
  }
}

describe('parseBatchWindow', () => {
  it('parses a normal window', () => {
    expect(parseBatchWindow('2-6')).toEqual({ start: 2, end: 6 })
    expect(parseBatchWindow(' 02-06 ')).toEqual({ start: 2, end: 6 })
  })

  it('parses a window that wraps past midnight', () => {
    expect(parseBatchWindow('22-4')).toEqual({ start: 22, end: 4 })
  })

  it('returns null for empty, malformed, out-of-range, or equal-bound specs', () => {
    expect(parseBatchWindow('')).toBeNull()
    expect(parseBatchWindow('abc')).toBeNull()
    expect(parseBatchWindow('5')).toBeNull()
    expect(parseBatchWindow('5-5')).toBeNull()
    expect(parseBatchWindow('24-1')).toBeNull()
    expect(parseBatchWindow('2-26')).toBeNull()
  })
})

describe('inBatchWindow', () => {
  const at = (hour: number) => new Date(2026, 6, 15, hour, 30, 0)

  it('includes hours inside a normal window and excludes the end bound', () => {
    const w = { start: 2, end: 6 }
    expect(inBatchWindow(w, at(2))).toBe(true)
    expect(inBatchWindow(w, at(5))).toBe(true)
    expect(inBatchWindow(w, at(6))).toBe(false)
    expect(inBatchWindow(w, at(12))).toBe(false)
  })

  it('handles a window that wraps past midnight', () => {
    const w = { start: 22, end: 4 }
    expect(inBatchWindow(w, at(23))).toBe(true)
    expect(inBatchWindow(w, at(1))).toBe(true)
    expect(inBatchWindow(w, at(4))).toBe(false)
    expect(inBatchWindow(w, at(12))).toBe(false)
  })
})

// Access to private internals for deterministic testing — cast THROUGH unknown to a
// standalone shape (never intersect ReflexEngine: redeclaring its private members
// collapses the intersection to `never` under tsc).
interface EnginePriv {
  _batchMetricsCache: { data: Set<string>; loadedAt: number } | null
  _nonRemediableCache: { data: Set<string>; loadedAt: number } | null
  cooldowns: Map<string, unknown>
  noImprovement: Map<string, unknown>
  _now: () => Date
  _isEligible: (e: ReflexEntry) => boolean
  _runVerifyGate: (e: ReflexEntry, m: string) => boolean
  _suppressAsPhantom: (e: ReflexEntry, k: string) => void
  _execute: (e: ReflexEntry) => Promise<void>
}
const priv = (e: ReflexEngine): EnginePriv => e as unknown as EnginePriv

describe('ReflexEngine batch-defer gate (2b)', () => {
  let engine: ReflexEngine
  const prevWindow = process.env['REFLEX_BATCH_WINDOW']

  // Deterministic membership + clock, independent of on-disk batch_metrics.json / wall clock.
  function harness(hour: number, batch: string[] = ['Doc_Parity_Issues']): ReflexEngine {
    const e = new ReflexEngine('claude')
    // Isolate from any on-disk state _loadState() may have hydrated (a stuck/cooldown entry
    // for this key would short-circuit _isEligible before the batch-defer gate under test).
    priv(e).cooldowns.clear()
    priv(e).noImprovement.clear()
    priv(e)._batchMetricsCache = { data: new Set(batch), loadedAt: Date.now() }
    priv(e)._nonRemediableCache = { data: new Set<string>(), loadedAt: Date.now() }
    priv(e)._now = () => new Date(2026, 6, 15, hour, 30, 0)
    return e
  }

  function skipReasons(e: ReflexEngine, entry: ReflexEntry): string[] {
    const reasons: string[] = []
    e.on('auto_reflex_skipped', (d: { reason: string }) => reasons.push(d.reason))
    priv(e)._isEligible(entry)
    return reasons
  }

  afterEach(() => {
    engine?.destroy()
    if (prevWindow === undefined) delete process.env['REFLEX_BATCH_WINDOW']
    else process.env['REFLEX_BATCH_WINDOW'] = prevWindow
  })

  it('defers a batch metric outside the window', () => {
    process.env['REFLEX_BATCH_WINDOW'] = '2-6'
    engine = harness(12)
    expect(skipReasons(engine, batchEntry())).toContain('batch_deferred')
  })

  it('does not defer inside the window', () => {
    process.env['REFLEX_BATCH_WINDOW'] = '2-6'
    engine = harness(3)
    expect(skipReasons(engine, batchEntry())).not.toContain('batch_deferred')
  })

  it('does not defer when REFLEX_BATCH_WINDOW is unset (feature off = real-time)', () => {
    delete process.env['REFLEX_BATCH_WINDOW']
    engine = harness(12)
    expect(skipReasons(engine, batchEntry())).not.toContain('batch_deferred')
  })

  it('never defers a non-batch metric', () => {
    process.env['REFLEX_BATCH_WINDOW'] = '2-6'
    engine = harness(12, ['Doc_Parity_Issues'])
    const entry = batchEntry({ id: 'metric:bow:Error_Rate', command: '/investigate' })
    expect(skipReasons(engine, entry)).not.toContain('batch_deferred')
  })
})

describe('ReflexEngine verify-gate (2a)', () => {
  let engine: ReflexEngine
  afterEach(() => engine?.destroy())

  it('fails open (proceeds) when the metric cannot be measured', () => {
    engine = new ReflexEngine('claude')
    expect(priv(engine)._runVerifyGate(batchEntry(), 'Not_A_Real_Metric')).toBe(true)
  })

  it('arms cooldown when suppressing a phantom so a stale payload cannot re-fire it', () => {
    engine = new ReflexEngine('claude')
    const entry = batchEntry()
    priv(engine)._suppressAsPhantom(entry, `${entry.id}::${entry.command}`)
    // Immediately re-checking eligibility now short-circuits on cooldown.
    const reasons: string[] = []
    engine.on('auto_reflex_skipped', (d: { reason: string }) => reasons.push(d.reason))
    priv(engine)._isEligible(entry)
    expect(reasons).toContain('cooldown')
  })

  // Integration of the _execute WIRING: gate returns false → _execute must short-circuit at
  // the verify-gate (before the pre-run snapshot / auto_reflex_start / any spawn) and settle
  // via _suppressAsPhantom. The gate itself is stubbed here so we test the composition, not
  // remeasure_gate.py (covered separately, function + live). No claude subprocess is reached
  // because the suppress branch returns first.
  it('_execute short-circuits at the verify-gate for a phantom without starting a run', async () => {
    engine = new ReflexEngine('claude')
    const p = priv(engine)
    p._batchMetricsCache = { data: new Set(['Doc_Parity_Issues']), loadedAt: Date.now() }
    p._runVerifyGate = () => false  // force phantom
    const events: string[] = []
    let doneStatus: string | undefined
    engine.on('auto_reflex_start', () => events.push('start'))
    engine.on('auto_reflex_done', (d: { status: string }) => { events.push('done'); doneStatus = d.status })

    await p._execute(batchEntry())

    expect(events).not.toContain('start')  // never reached the snapshot/spawn path
    expect(events).toContain('done')
    expect(doneStatus).toBe('no_change')
    expect(engine.isRunning).toBe(false)
    expect(engine.activeReflexEntry).toBeNull()
  })
  // The proceed branch (gate true → fall through to the real skill spawn) is intentionally
  // NOT driven through _execute here: it would spawn a real `claude` subprocess. It is covered
  // indirectly — the gate's true-return is exercised by the fail-open test, and the branch is
  // a single early-return.
})
