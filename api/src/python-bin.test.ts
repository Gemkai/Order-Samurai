import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import fs from 'fs'
import os from 'os'
import path from 'path'
import { resolvePythonBin } from './python-bin.js'

// On PEP 668 "externally managed" Pythons (Homebrew, Debian/Ubuntu) bin/install.sh puts
// jsonschema/requests into ${SAMURAI_HOME:-~/.samurai}/venv, so the API must run its
// Python helpers with that interpreter or refresh_dashboard.py cannot import them.
describe('resolvePythonBin', () => {
  let home: string

  beforeEach(() => {
    home = fs.mkdtempSync(path.join(os.tmpdir(), 'samurai-py-'))
  })
  afterEach(() => {
    fs.rmSync(home, { recursive: true, force: true })
  })

  // A stand-in interpreter: a shell script that exits with the given status.
  const writeVenvPython = (samuraiHome: string, exitCode: number): string => {
    const bin = path.join(samuraiHome, 'venv', 'bin')
    fs.mkdirSync(bin, { recursive: true })
    const py = path.join(bin, 'python')
    fs.writeFileSync(py, `#!/bin/sh\nexit ${exitCode}\n`, { mode: 0o755 })
    return py
  }

  it.skipIf(process.platform === 'win32')('prefers the private venv python when it runs', () => {
    const py = writeVenvPython(home, 0)
    expect(resolvePythonBin({ SAMURAI_HOME: home }, 'darwin')).toBe(py)
  })

  it.skipIf(process.platform === 'win32')('falls back to python3 when the venv python does not run', () => {
    writeVenvPython(home, 1)
    expect(resolvePythonBin({ SAMURAI_HOME: home }, 'linux')).toBe('python3')
  })

  it('falls back to python3 when there is no venv', () => {
    expect(resolvePythonBin({ SAMURAI_HOME: home }, 'darwin')).toBe('python3')
  })

  it('falls back to python on win32', () => {
    expect(resolvePythonBin({ SAMURAI_HOME: home }, 'win32')).toBe('python')
  })

  it.skipIf(process.platform === 'win32')('defaults SAMURAI_HOME to $HOME/.samurai, treating empty as unset like install.sh', () => {
    const py = writeVenvPython(path.join(home, '.samurai'), 0)
    expect(resolvePythonBin({ HOME: home }, 'darwin')).toBe(py)
    expect(resolvePythonBin({ HOME: home, SAMURAI_HOME: '' }, 'darwin')).toBe(py)
  })
})

// Every API call site that runs a repo Python script must go through the resolver;
// a hard-coded 'python3' silently loses the venv's dependencies on PEP 668 machines.
describe('python call sites', () => {
  it.each(['server.ts', 'reflex-engine.ts'])('%s does not hard-code a python interpreter', (file) => {
    const src = fs.readFileSync(new URL(file, import.meta.url), 'utf8')
    expect(src).not.toMatch(/['"]python3?['"]/)
    expect(src).toContain('resolvePythonBin(')
  })
})
