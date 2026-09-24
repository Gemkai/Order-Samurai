import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import fs from 'fs'
import os from 'os'
import path from 'path'
import { EventEmitter } from 'events'

const TEST_ROOT = vi.hoisted(() => {
  const nodeFs = require('fs') as typeof import('fs')
  const nodeOs = require('os') as typeof import('os')
  const nodePath = require('path') as typeof import('path')
  const root = nodeFs.mkdtempSync(nodePath.join(nodeOs.tmpdir(), 'public-reflex-retired-'))
  process.env['ORDER_SAMURAI_ROOT'] = root
  process.env['WID_PAYLOAD_PATH'] = nodePath.join(root, 'wid_payload.json')
  process.env['REFLEX_AUTO_APPLY'] = 'true'
  return root
})

vi.mock('child_process', () => ({
  spawn: vi.fn(() => {
    const child = Object.assign(new EventEmitter(), {
      stdout: new EventEmitter(), stderr: new EventEmitter(), kill: vi.fn(), pid: 4242,
    })
    queueMicrotask(() => child.emit('close', 0, null))
    return child
  }),
  spawnSync: vi.fn(() => ({ status: 0, stdout: '', stderr: '', pid: 1, output: [], signal: null })),
}))

import { spawn, spawnSync } from 'child_process'
import { ReflexEngine } from './reflex-engine.js'
import type { ReflexEntry } from './reflex-engine.js'

interface Private {
  _isEligible: (entry: ReflexEntry) => boolean; _drainQueue: () => void; queue: ReflexEntry[]
  _execute: (entry: ReflexEntry) => Promise<void>; _runMechanism: (...args: unknown[]) => Promise<'done' | 'error' | 'timeout'>
  _afterRun: (...args: unknown[]) => void; _isSkillReadonly: (command: string) => boolean
  _saveState: () => void; _waitForApproval: (key: string, windowMs: number) => Promise<boolean>
}
const priv = (engine: ReflexEngine): Private => engine as unknown as Private
const fixture = (overrides: Partial<ReflexEntry> = {}): ReflexEntry => ({
  id: 'metric:arts:Doc_Parity_Issues', tier: 'CRITICAL', command: '/wiki', status: 'active',
  source: 'metric', maturity: 'APPLY', ...overrides,
})

describe('public runtime autonomous repair retirement', () => {
  let engine: ReflexEngine
  beforeEach(() => {
    fs.rmSync(path.join(TEST_ROOT, 'state'), { recursive: true, force: true })
    fs.mkdirSync(path.join(TEST_ROOT, 'state'), { recursive: true })
    vi.clearAllMocks(); engine = new ReflexEngine('claude')
  })
  afterEach(() => { engine?.destroy(); vi.useRealTimers() })

  it('refuses direct automatic LLM repair injection', () => {
    const p = priv(engine); p._isEligible = vi.fn(() => true); p._drainQueue = vi.fn()
    expect(engine.injectReflex(fixture())).toBe(false)
    expect(p.queue).toHaveLength(0)
    expect(spawn).not.toHaveBeenCalled()
  })

  it('runs a deterministic mechanism without falling back to an LLM skill', async () => {
    const p = priv(engine)
    p._runMechanism = vi.fn(async () => 'error' as const); p._afterRun = vi.fn(); p._isSkillReadonly = () => true
    await p._execute(fixture({ mechanism: { script: 'wiki_link.py', args: [], read_only: true, timeout_s: 30 } }))
    expect(spawn).not.toHaveBeenCalled()
    expect(vi.mocked(p._afterRun).mock.calls[0]?.slice(0, 4)).toEqual([
      expect.anything(), 'metric:arts:Doc_Parity_Issues::/wiki', 'error', 'mechanism',
    ])
  })

  it('treats approval-window expiry as denial', async () => {
    vi.useFakeTimers()
    const pending = priv(engine)._waitForApproval('metric:arts:Doc_Parity_Issues::/wiki', 1_000)
    await vi.advanceTimersByTimeAsync(1_001)
    await expect(pending).resolves.toBe(false)
  })

  it('keeps legacy REFLEX_AUTO_APPLY inert', () => {
    const p = priv(engine); const worktree = fs.mkdtempSync(path.join(os.tmpdir(), 'public-reflex-wt-'))
    p._saveState = vi.fn(); p._drainQueue = vi.fn()
    try {
      p._afterRun(fixture(), `${fixture().id}::${fixture().command}`, 'done', 'skill', undefined, worktree)
      expect(vi.mocked(spawnSync).mock.calls.some(([, args]) => args?.includes('apply') === true)).toBe(false)
      expect(fs.existsSync(path.join(TEST_ROOT, 'state', 'pending_remediation_metric_arts_Doc_Parity_Issues.patch'))).toBe(true)
    } finally { fs.rmSync(worktree, { recursive: true, force: true }) }
  })
})
