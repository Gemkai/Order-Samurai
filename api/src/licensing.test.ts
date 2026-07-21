import { describe, it, expect, afterEach, beforeEach } from 'vitest'
import fs from 'fs'
import os from 'os'
import path from 'path'
import { isProEntitled, licensePath } from './licensing.js'

// The TS reader must agree with agentica_core/licensing.py on the same
// ~/.samurai/license.json contract, and fail CLOSED to Free on anything ambiguous.
describe('isProEntitled', () => {
  let home: string
  const prev = process.env['SAMURAI_HOME']

  beforeEach(() => {
    home = fs.mkdtempSync(path.join(os.tmpdir(), 'samurai-lic-'))
    process.env['SAMURAI_HOME'] = home
  })
  afterEach(() => {
    if (prev === undefined) delete process.env['SAMURAI_HOME']
    else process.env['SAMURAI_HOME'] = prev
  })

  const write = (obj: unknown) => fs.writeFileSync(licensePath(), JSON.stringify(obj))

  it('is false when no license file exists (Free is the default)', () => {
    expect(isProEntitled()).toBe(false)
  })

  it('is true for a valid, active, non-refunded Pro entitlement', () => {
    write({ tier: 'pro', valid: true, status: 'active' })
    expect(isProEntitled()).toBe(true)
  })

  it('is false for a refunded license', () => {
    write({ tier: 'pro', valid: true, status: 'active', refunded: true })
    expect(isProEntitled()).toBe(false)
  })

  it('is false when status is not active', () => {
    write({ tier: 'pro', valid: true, status: 'inactive' })
    expect(isProEntitled()).toBe(false)
  })

  it('is false when tier is not pro', () => {
    write({ tier: 'free', valid: true, status: 'active' })
    expect(isProEntitled()).toBe(false)
  })

  it('is false on a malformed license file (fail closed, never throws)', () => {
    fs.writeFileSync(licensePath(), 'not json {')
    expect(isProEntitled()).toBe(false)
  })
})
