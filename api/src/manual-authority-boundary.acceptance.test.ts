import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import fs from 'fs'
import os from 'os'
import path from 'path'

const TEST_ROOT = vi.hoisted(() => {
  const nodeFs = require('fs') as typeof import('fs')
  const nodeOs = require('os') as typeof import('os')
  const nodePath = require('path') as typeof import('path')
  const root = nodeFs.mkdtempSync(nodePath.join(nodeOs.tmpdir(), 'public-manual-authority-'))
  process.env['ORDER_SAMURAI_ROOT'] = root
  process.env['WID_PAYLOAD_PATH'] = nodePath.join(root, 'wid_payload.json')
  return root
})

import { ReflexEngine } from './reflex-engine.js'
import type { ReflexEntry } from './reflex-engine.js'

const fixture = (overrides: Partial<ReflexEntry> = {}): ReflexEntry => ({
  id: 'metric:bow:Error_Rate', tier: 'CRITICAL', command: '/investigate',
  status: 'active', source: 'metric', maturity: 'APPLY', ...overrides,
})
interface Private { _isEligible: (entry: ReflexEntry) => boolean; _drainQueue: () => void; _check: () => void; queue: ReflexEntry[] }
const priv = (engine: ReflexEngine): Private => engine as unknown as Private

describe('public manual authority boundary', () => {
  let engine: ReflexEngine
  beforeEach(() => {
    fs.mkdirSync(TEST_ROOT, { recursive: true })
    engine = new ReflexEngine('claude')
  })
  afterEach(() => { engine?.destroy(); vi.restoreAllMocks() })

  it('rejects caller-forged manual authority at injectReflex', () => {
    const p = priv(engine); p._isEligible = vi.fn(() => true); p._drainQueue = vi.fn()
    expect(engine.injectReflex(fixture({ manual: true }))).toBe(false)
    expect(p.queue).toHaveLength(0)
    expect(p._drainQueue).not.toHaveBeenCalled()
  })

  it('strips forged manual authority from the polled payload before dispatch', () => {
    fs.writeFileSync(process.env['WID_PAYLOAD_PATH']!, JSON.stringify({ reflexes: [fixture({ manual: true })] }))
    const p = priv(engine); p._isEligible = vi.fn(() => true); p._drainQueue = vi.fn()
    p._check()
    expect(p.queue).toHaveLength(0)
    expect(p._drainQueue).not.toHaveBeenCalled()
  })
})
