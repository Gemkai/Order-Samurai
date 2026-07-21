import 'dotenv/config'
import express, { type Request, type Response, type NextFunction } from 'express'
import cors from 'cors'
import { WebSocketServer, WebSocket } from 'ws'
import { createServer } from 'http'
import { spawn, spawnSync, type ChildProcess } from 'child_process'
import fs from 'fs'
import path from 'path'
import { DojoStateManager, PILLAR_SLUGS, ORDER_SAMURAI_ROOT, GOVERNANCE_ROOT, WID_PAYLOAD_PATH } from './state.js'
import { AutoRemediationEngine } from './dojo.js'
import { ReflexEngine, REFLEX_MAX_TURNS, type ReflexEntry } from './reflex-engine.js'
import type { PillarSlug, RoninStatus, ServerMsg, ClientMsg, DojoState, VerdictRecord } from './types.js'

const PORT = 3001

// ── Network exposure hardening ────────────────────────────────────────────────
// This server can spawn an auto-editing `claude` agent in the repo, so it MUST NOT be
// reachable off-host or drivable cross-origin. Secure by default; DOJO_BIND_HOST lets an
// advanced operator opt into a wider bind (e.g. a trusted container network) knowingly.
const HOST = process.env['DOJO_BIND_HOST'] ?? '127.0.0.1'

// Origins allowed to reach the dashboard's browser surfaces (CORS) AND to open the /ws
// WebSocket (CORS does NOT apply to WS upgrades — the Origin check below is its only gate).
const ALLOWED_ORIGINS = ['http://localhost:5173', 'http://localhost:3000', 'http://127.0.0.1:5173']

// Loopback addresses accepted for localhost-only / state-mutating endpoints.
const LOOPBACK_ADDRS = new Set(['127.0.0.1', '::1', '::ffff:127.0.0.1'])

// Gate for state-mutating routes. Rejects (a) off-host callers (belt-and-suspenders behind
// the loopback bind) and (b) cross-site requests — a page the operator visits can fire a
// no-preflight POST at http://localhost:3001 (CORS blocks reading the reply, not the side
// effect), so a non-allow-listed Origin is refused. No Origin (curl / local tooling) is
// allowed because the loopback bind already fences it to this host.
function requireLocalTrusted(req: Request, res: Response, next: NextFunction): void {
  const remoteAddr = req.socket.remoteAddress ?? ''
  if (!LOOPBACK_ADDRS.has(remoteAddr)) {
    res.status(403).json({ error: 'forbidden: localhost-only endpoint' })
    return
  }
  const origin = req.headers.origin
  if (origin && !ALLOWED_ORIGINS.includes(origin)) {
    res.status(403).json({ error: 'forbidden: cross-origin request rejected' })
    return
  }
  next()
}

// Simple in-memory rate limiter for remediation runs (per-pillar, per-IP)
const runRateMap = new Map<string, number>()
const RUN_COOLDOWN_MS = 10_000

function isRateLimited(key: string): boolean {
  const last = runRateMap.get(key) ?? 0
  const now = Date.now()
  if (now - last < RUN_COOLDOWN_MS) return true
  runRateMap.set(key, now)
  return false
}

// ── Skill exec (headless claude --print) ─────────────────────────────────────
const EXEC_TIMEOUT_MS = 5 * 60 * 1000 // 5 minutes
let execChild: ChildProcess | null = null

/** Commands currently executing in spawnExec — used for cross-channel dedup with ReflexEngine (#11) */
// cmdKey → owning child. Map (not Set) so a stale child's close handler can't
// evict the entry a newer run of the same command just registered.
const spawnExecActive = new Map<string, ReturnType<typeof spawn>>()

// Resolve the full path to the claude binary once at server startup.
// We check %APPDATA%\npm\claude.cmd directly — the standard npm global install location on
// Windows — because the tsx process may have been started from a terminal with an incomplete
// PATH, making where.exe and PATH-based lookups unreliable. Using the absolute path bypasses
// PATH entirely and works regardless of how the server was launched.
const CLAUDE_BIN: string = (() => {
  if (process.platform !== 'win32') return 'claude'
  // Primary: APPDATA\npm\claude.cmd — standard npm global location, always set on Windows
  const appdata = process.env['APPDATA']
  if (appdata) {
    const p = path.join(appdata, 'npm', 'claude.cmd')
    if (fs.existsSync(p)) { console.log(`[dojo-api] claude found at ${p}`); return p }
  }
  // Secondary: where.exe — only works if npm global is already on server process PATH
  try {
    const r = spawnSync('where.exe', ['claude'], { encoding: 'utf8' })
    if (r.status === 0 && r.stdout) {
      const lines = String(r.stdout).trim().split(/\r?\n/).filter(Boolean)
      const found = lines.find(l => l.endsWith('.cmd')) ?? lines[0]
      if (found) { console.log(`[dojo-api] claude found via where.exe at ${found}`); return found }
    }
  } catch { /* ignore */ }
  console.warn('[dojo-api] claude binary not found — exec will fail on Windows')
  return 'claude'
})()

function spawnExec(command: string, scope?: string): void {
  // Safety: only allow skill-style commands (start with /) or explicit claude invocations
  const isSkill = /^\/\S+/.test(command.trim())
  const isClaude = /^claude\s/.test(command.trim())
  if (!isSkill && !isClaude) {
    broadcast({ type: 'exec_status', status: 'error' as RoninStatus })
    broadcast({ type: 'exec_output', line: `Blocked: command must start with / or 'claude '` })
    return
  }

  // Cross-channel dedup (#11): skip if ReflexEngine is already running or has queued this command
  const cmdKey = command.trim()
  if (reflexEngine.isCommandActive(cmdKey)) {
    broadcast({ type: 'exec_status', status: 'done' as RoninStatus })
    broadcast({ type: 'exec_output', line: `[deduped] ${cmdKey} is already queued or running in reflex channel` })
    return
  }

  // Kill any already-running exec
  if (execChild) { try { execChild.kill() } catch { /* ignore */ } }

  broadcast({ type: 'exec_status', status: 'running' as RoninStatus })

  // Build args: skill commands become `claude --print -p "<command>"`. A `scope` (the worst-
  // contributing project, set on per-project metric reflexes by reflexes.py) is appended to the
  // prompt so a manual run concentrates on that project instead of the whole governance tree.
  const promptText = scope
    ? `${command.trim()} — concentrate this remediation on the single worst-contributing project this window: ${scope}`
    : command.trim()
  let cmd: string
  let args: string[]
  if (isSkill) {
    cmd = 'claude'
    args = ['--print', '-p', promptText, '--permission-mode', 'acceptEdits', '--max-turns', REFLEX_MAX_TURNS]
  } else {
    // Strip leading 'claude' and forward remaining args
    const parts = command.trim().split(/\s+/)
    cmd = parts[0]
    args = parts.slice(1)
    // P3-1: never forward privilege-escalating flags on a raw `claude` invocation, even though
    // this channel is already loopback+Origin fenced (P2-1). Defence-in-depth: a forwarded
    // --dangerously-skip-permissions would drop the acceptEdits sandbox entirely.
    const FORBIDDEN_FLAGS = ['--dangerously-skip-permissions', '--permission-mode']
    if (args.some(a => FORBIDDEN_FLAGS.some(f => a === f || a.startsWith(f + '=')))) {
      broadcast({ type: 'exec_status', status: 'error' as RoninStatus })
      broadcast({ type: 'exec_output', line: `Blocked: forwarded claude command may not set permission/skip flags` })
      return
    }
  }

  // On Windows use cmd.exe with the resolved absolute path to claude.cmd — bypasses PATH
  // entirely so it works regardless of how tsx was started.
  let spawnCmd = cmd
  let spawnArgs = args
  if (process.platform === 'win32' && cmd === 'claude') {
    spawnCmd = 'cmd'
    spawnArgs = ['/c', CLAUDE_BIN, ...args]
  }

  let settled = false
  const child = spawn(spawnCmd, spawnArgs, {
    cwd: ORDER_SAMURAI_ROOT,
    env: { ...process.env },
    stdio: ['ignore', 'pipe', 'pipe'],  // ignore stdin — prevents claude's "no stdin" 3s wait
  })
  execChild = child
  spawnExecActive.set(cmdKey, child)

  const killTimer = setTimeout(() => {
    try { child.kill() } catch { /* ignore */ }
    execChild = null
    settled = true
    broadcast({ type: 'exec_status', status: 'error' as RoninStatus })
    broadcast({ type: 'exec_output', line: 'Error: exec timed out after 5 minutes' })
  }, EXEC_TIMEOUT_MS)

  let outputBuffer = ''
  const handleData = (data: Buffer) => {
    const chunk = data.toString()
    outputBuffer += chunk
    chunk.split('\n')
      .filter(l => l.trim())
      .forEach(line => broadcast({ type: 'exec_output', line: line.slice(0, 500) }))
  }
  child.stdout?.on('data', handleData)
  child.stderr?.on('data', handleData)

  child.on('close', (code) => {
    clearTimeout(killTimer)
    if (spawnExecActive.get(cmdKey) === child) spawnExecActive.delete(cmdKey)
    if (execChild === child) execChild = null
    else return  // superseded by a newer run — don't stomp its status broadcasts
    if (settled) return
    settled = true

    const isLimit = code !== 0 && /5-hour limit|weekly limit|quota exceeded|limit reached|rate limit/i.test(outputBuffer)
    if (isLimit) {
      // No secondary model in the public build — report the limit as a terminal error.
      broadcast({ type: 'exec_output', line: '⚠️ Claude Code CLI limit reached — try again after the limit window resets.' })
      broadcast({ type: 'exec_status', status: 'error' as RoninStatus })
      return
    }

    const status = code === 0 ? 'done' : 'error'
    broadcast({ type: 'exec_status', status: status as RoninStatus })

    if (status === 'done') {
      // 1. Append to exec_log.jsonl so remediation.efficacy() can see this skill run
      try {
        const skillName = command.trim().startsWith('/')
          ? command.trim().slice(1).split(/\s+/)[0]
          : command.trim().split(/\s+/)[0]
        const entry = JSON.stringify({
          timestamp: new Date().toISOString(),
          command: command.trim(),
          skill: skillName,
          status: 'done',
        }) + '\n'
        fs.appendFileSync(path.join(ORDER_SAMURAI_ROOT, 'state', 'exec_log.jsonl'), entry, 'utf8')
      } catch { /* ignore write failures — exec_log is best-effort */ }

      // 2. Regenerate wid_payload.json so the dashboard picks up the new efficacy entry
      try {
        spawn(process.platform === 'win32' ? 'python' : 'python3',
          [path.join(GOVERNANCE_ROOT, 'refresh_dashboard.py')],
          { detached: true, stdio: 'ignore', cwd: GOVERNANCE_ROOT, env: { ...process.env } }
        ).unref()
      } catch { /* ignore spawn failures */ }
    }
  })
  child.on('error', (err) => {
    clearTimeout(killTimer)
    if (spawnExecActive.get(cmdKey) === child) spawnExecActive.delete(cmdKey)
    if (execChild === child) execChild = null
    else return  // superseded by a newer run
    if (settled) return
    settled = true
    broadcast({ type: 'exec_status', status: 'error' as RoninStatus })
    broadcast({ type: 'exec_output', line: `Error: ${err.message}` })
  })
}

const app = express()
app.use(cors({ origin: ALLOWED_ORIGINS }))
app.use(express.json())

const stateManager = new DojoStateManager()
const engine = new AutoRemediationEngine(stateManager)
// Pass spawnExecActive callback so ReflexEngine can skip commands already running in this channel
const reflexEngine = new ReflexEngine(
  CLAUDE_BIN,
  (cmd: string) => spawnExecActive.has(cmd.trim()),
)

// Boot side-effects (state watcher, auto-remediation, reflex watcher, periodic
// refresh) are deferred until the port is bound — see boot() below. A second
// instance must die on EADDRINUSE *before* it can fire any remediation, or it
// runs a shadow ReflexEngine that double-fires skill commands.
function boot(): void {
  stateManager.read()
  stateManager.watch()
  const initialState = stateManager.current
  if (initialState) engine.check(initialState)
  // Autonomous reflex watcher — fires skill commands when CRITICAL/HIGH reflexes detected.
  // Runs concurrently with AutoRemediationEngine (separate subprocess channel).
  reflexEngine.watch()

  // Periodic dashboard refresh, independent of reflex firing (#dashboard-freshness).
  // The engine otherwise only refreshes the payload in _afterRun (after a reflex
  // fires); when every reflex is idle or loop-breaker-stuck the dashboard would
  // freeze (see the 2026-06-14 outage). This keeps metrics + usage current regardless.
  const DASHBOARD_REFRESH_MS = Number(process.env['DASHBOARD_REFRESH_MS'] ?? 15 * 60 * 1000)
  if (DASHBOARD_REFRESH_MS > 0) {
    const periodicRefresh = setInterval(() => {
      try {
        spawn(process.platform === 'win32' ? 'python' : 'python3',
          [path.join(GOVERNANCE_ROOT, 'refresh_dashboard.py')],
          { detached: true, stdio: 'ignore', cwd: GOVERNANCE_ROOT, env: { ...process.env } }
        ).unref()
      } catch { /* non-fatal — a failed refresh must never crash the server */ }
    }, DASHBOARD_REFRESH_MS)
    periodicRefresh.unref()
  }
}

// ── Broadcast helpers ─────────────────────────────────────────────────────────
const server = createServer(app)
const wss = new WebSocketServer({
  server,
  path: '/ws',
  // The /ws channel accepts {type:'exec'} messages that spawn an auto-editing agent, and
  // browsers apply NO CORS/same-origin policy to outbound WebSocket connections — so a page
  // the operator visits could otherwise drive it. A browser always sends a truthful Origin
  // it cannot forge from JS; reject any not on the dashboard allow-list. No Origin (local
  // non-browser tooling) is allowed because the loopback bind already fences it to this host.
  verifyClient: ({ origin }: { origin?: string }, done: (ok: boolean, code?: number, msg?: string) => void) => {
    if (origin) return done(ALLOWED_ORIGINS.includes(origin), 403, 'forbidden origin')
    // No Origin (local non-browser tooling): trust it ONLY while fenced to loopback. If the
    // operator widened DOJO_BIND_HOST off-loopback, a raw network socket also omits Origin —
    // couple the exec gate to the bind so widening the bind can't silently open exec.
    if (LOOPBACK_ADDRS.has(HOST)) return done(true)
    done(false, 403, 'origin required on non-loopback bind')
  },
})

function broadcast(msg: ServerMsg): void {
  const str = JSON.stringify(msg)
  wss.clients.forEach((client) => {
    if (client.readyState === WebSocket.OPEN) client.send(str)
  })
}

// ── Forward engine events to all WS clients ───────────────────────────────────
engine.on('status', (pillar: PillarSlug, status: string) =>
  broadcast({ type: 'status', pillar, status: status as 'idle' | 'running' | 'done' | 'error' }))
engine.on('output', (pillar: PillarSlug, line: string) =>
  broadcast({ type: 'output', pillar, line }))
engine.on('auto_remediation', (pillar: PillarSlug) => {
  broadcast({ type: 'auto_remediation', pillar })
  // Bridge: when a pillar score regresses into ronin mode, also inject its alarmed
  // per-metric reflexes (CRITICAL / HIGH) into ReflexEngine so the specific metrics
  // driving the regression are remediated alongside the pillar-level ronin script.
  try {
    const raw = fs.readFileSync(WID_PAYLOAD_PATH, 'utf8')
    const payload = JSON.parse(raw) as { reflexes?: ReflexEntry[] }
    const alarmed = (payload.reflexes ?? []).filter(r =>
      r.id.startsWith(`metric:${pillar}:`) &&
      (r.tier === 'CRITICAL' || r.tier === 'HIGH') &&
      r.status === 'active',
    )
    let injected = 0
    for (const entry of alarmed) {
      if (reflexEngine.injectReflex(entry)) injected++
    }
    if (alarmed.length > 0) {
      console.log(
        `[dojo-api] ronin:${pillar} — bridged ${injected}/${alarmed.length} alarmed reflexes to ReflexEngine`,
      )
    }
  } catch {
    // Non-fatal — wid_payload.json may not exist on first startup
  }
})
stateManager.on('change', (data: DojoState) =>
  broadcast({ type: 'state', data }))
reflexEngine.on('auto_reflex_start', (data) =>
  broadcast({ type: 'auto_reflex_start', ...data } as ServerMsg))
reflexEngine.on('auto_reflex_output', (data) =>
  broadcast({ type: 'auto_reflex_output', ...data } as ServerMsg))
reflexEngine.on('auto_reflex_done', (data) =>
  broadcast({ type: 'auto_reflex_done', ...data } as ServerMsg))
reflexEngine.on('auto_reflex_stuck', (data) =>
  broadcast({ type: 'auto_reflex_stuck', ...data } as ServerMsg))
reflexEngine.on('auto_reflex_pending', (data) =>
  broadcast({ type: 'auto_reflex_pending', ...data } as ServerMsg))
reflexEngine.on('auto_reflex_skipped', (data: { reflex_id: string; reason: string }) =>
  broadcast({ type: 'auto_reflex_skipped', ...data }))

// ── REST API ──────────────────────────────────────────────────────────────────
app.get('/api/health', (_req, res) => {
  res.json({
    ok: true,
    connected_clients: wss.clients.size,
    reflex_queue: reflexEngine.queueLength,
    reflex_running: reflexEngine.isRunning,
  })
})

// Allow operator to unstick a metric that the loop-breaker has halted
app.post('/api/reflex/unstick/:metric', requireLocalTrusted, (req, res) => {
  const metric = req.params['metric'] ?? ''
  reflexEngine.clearStuck(metric)
  res.json({ metric, unstuck: true })
})

// Recovery hatch: unstick EVERY reflex the loop-breaker has parked (e.g. after a systemic
// misclassification froze the whole engine). Returns how many were stuck before the reset.
app.post('/api/reflex/unstick-all', requireLocalTrusted, (_req, res) => {
  const cleared = reflexEngine.clearAllStuck()
  res.json({ unstuck: true, cleared })
})

// Cancel a pending skill fire during its approval window (#G1)
// Called when REFLEX_APPROVAL_WINDOW_MS > 0 and the operator wants to defer a reflex.
app.post('/api/reflex/cancel/:key', requireLocalTrusted, (req, res) => {
  const key = decodeURIComponent(req.params['key'] ?? '')
  const cancelled = reflexEngine.cancelPending(key)
  if (!cancelled) return res.status(409).json({ key, cancelled: false, error: 'no approval pending for this key' })
  res.json({ key, cancelled: true })
})

// Accept rival verdicts from sensei-cycle — localhost-only, max 100 per request.
// REFUTED entries suppress ReflexEngine execution for 24h; absent = no change (fail-safe).
app.post('/api/reflex/verdicts', requireLocalTrusted, (req, res) => {
  // P3-3: use requireLocalTrusted for parity with the other mutating routes — it adds the
  // Origin check on top of the loopback gate (the inline check here was loopback-only).
  const body = req.body as unknown
  if (!Array.isArray(body)) {
    return res.status(400).json({ error: 'body must be an array of VerdictRecord' })
  }
  if (body.length > 100) {
    return res.status(400).json({ error: 'too many verdicts: max 100 per request' })
  }

  const VALID_VERDICTS = new Set(['CONFIRMED', 'REFUTED', 'SUSPECT'])
  const verdicts: VerdictRecord[] = []
  for (const v of body) {
    if (
      typeof v !== 'object' || v === null ||
      typeof (v as Record<string, unknown>)['reflex_id'] !== 'string' ||
      !(v as Record<string, unknown>)['reflex_id'] ||
      typeof (v as Record<string, unknown>)['verdict'] !== 'string' ||
      !VALID_VERDICTS.has(String((v as Record<string, unknown>)['verdict'])) ||
      typeof (v as Record<string, unknown>)['cycle_id'] !== 'string' ||
      typeof (v as Record<string, unknown>)['ts'] !== 'string'
    ) {
      return res.status(400).json({ error: 'invalid verdict record', received: v })
    }
    // Governance opt-in grant — reflex_ready is optional (no new *required* field
    // so existing 400 contract is preserved). If present it must be boolean.
    const reflexReady = (v as Record<string, unknown>)['reflex_ready']
    if (reflexReady !== undefined && typeof reflexReady !== 'boolean') {
      return res.status(400).json({ error: 'reflex_ready must be boolean when present', received: v })
    }
    verdicts.push(v as VerdictRecord)
  }

  reflexEngine.setVerdicts(verdicts)
  const refutedCount = verdicts.filter(v => v.verdict === 'REFUTED').length
  return res.json({ accepted: verdicts.length, refuted: refutedCount })
})

// Tail-read the sensei cycle ledger — byte-seek O(limit) read regardless of file size.
// Returns [] when ledger doesn't exist (sensei not yet started).
app.get('/api/sensei/ledger', (req, res) => {
  const ledgerPath = path.join(ORDER_SAMURAI_ROOT, 'state', 'SENSEI_LEDGER.jsonl')

  const limitRaw = parseInt(String(req.query['limit'] ?? '50'), 10)
  const limit = isNaN(limitRaw) || limitRaw < 1 ? 50 : Math.min(limitRaw, 500)
  const before = typeof req.query['before'] === 'string' ? req.query['before'] : null

  if (!fs.existsSync(ledgerPath)) return res.json([])

  try {
    const stat = fs.statSync(ledgerPath)
    const fileSize = stat.size
    const seekBytes = Math.max(0, fileSize - limit * 512)

    const fd = fs.openSync(ledgerPath, 'r')
    const buf = Buffer.alloc(fileSize - seekBytes)
    fs.readSync(fd, buf, 0, buf.length, seekBytes)
    fs.closeSync(fd)

    const lines = buf.toString('utf8').split('\n').filter(l => l.trim())
    const parsed: Record<string, unknown>[] = []
    for (const line of lines.slice(-limit)) {
      try { parsed.push(JSON.parse(line) as Record<string, unknown>) } catch { /* skip malformed */ }
    }

    const result = before
      ? parsed.filter(r => typeof r['ts'] === 'string' && r['ts'] < before)
      : parsed

    return res.json(result)
  } catch (err) {
    return res.status(500).json({ error: 'failed to read ledger', detail: String(err) })
  }
})

app.get('/api/dojo/state', (_req, res) => {
  const s = stateManager.read()
  if (!s) return res.status(503).json({ error: 'DOJO_STATE.json not found or unreadable' })
  res.json(s)
})

app.post('/api/ronin/toggle/:pillar', requireLocalTrusted, (req, res) => {
  const pillar = req.params['pillar'] as PillarSlug
  if (!PILLAR_SLUGS.includes(pillar)) return res.status(400).json({ error: 'unknown pillar' })
  try {
    const s = stateManager.toggle(pillar)
    if (!s) return res.status(503).json({ error: 'state write failed' })
    res.json({ pillar, ronin_mode: s.pillars[pillar].ronin_mode })
  } catch {
    res.status(500).json({ error: 'internal error' })
  }
})

app.post('/api/dojo/run/:pillar', requireLocalTrusted, (req, res) => {
  const pillar = req.params['pillar'] as PillarSlug
  if (!PILLAR_SLUGS.includes(pillar)) return res.status(400).json({ error: 'unknown pillar' })
  if (engine.isRunning(pillar)) return res.status(409).json({ error: 'already running', pillar })
  const ip = req.ip ?? 'unknown'
  if (isRateLimited(`${ip}:${pillar}`)) return res.status(429).json({ error: 'rate limited', pillar })
  try {
    engine.run(pillar)
    res.json({ pillar, started: true })
  } catch {
    res.status(500).json({ error: 'internal error' })
  }
})

// ── WebSocket ─────────────────────────────────────────────────────────────────
wss.on('connection', (ws) => {
  // Push current state immediately on connect
  const s = stateManager.current
  if (s) ws.send(JSON.stringify({ type: 'state', data: s } satisfies ServerMsg))

  // If ReflexEngine is currently executing a skill, replay the start event so that
  // page refreshes and new tabs immediately see the pulse animation on the correct card.
  const active = reflexEngine.activeReflexEntry
  if (active) {
    ws.send(JSON.stringify({
      type: 'auto_reflex_start',
      metric: active.id,
      tier: active.tier,
      command: active.command,
    } satisfies ServerMsg))
  }

  ws.on('message', (raw) => {
    try {
      const msg = JSON.parse(raw.toString()) as ClientMsg
      if (msg.type === 'toggle' && PILLAR_SLUGS.includes(msg.pillar)) {
        const s = stateManager.toggle(msg.pillar)
        if (s) broadcast({ type: 'state', data: s })
      } else if (msg.type === 'run' && PILLAR_SLUGS.includes(msg.pillar)) {
        if (!engine.isRunning(msg.pillar)) engine.run(msg.pillar)
      } else if (msg.type === 'exec' && typeof msg.command === 'string' && msg.command.trim()) {
        spawnExec(msg.command, typeof msg.scope === 'string' && msg.scope.trim() ? msg.scope.trim() : undefined)
      }
      // 'ping' messages are silently ignored (keepalive only)
    } catch {
      // ignore malformed messages
    }
  })

  ws.on('error', () => { /* suppress unhandled ws errors */ })
})

// ── Start ─────────────────────────────────────────────────────────────────────
// Single-instance guard: engines start only after the port is bound, so a
// duplicate `npm run dev` exits loudly here instead of running a shadow
// ReflexEngine (or crash-looping under tsx watch, re-firing on every respawn).
// Attached to BOTH emitters: ws forwards http-server errors onto the
// WebSocketServer instance, and its forwarding listener runs first — an
// unhandled re-emit there throws before a server-only handler is reached.
function fatalListenError(err: NodeJS.ErrnoException): void {
  if (err.code === 'EADDRINUSE') {
    console.error(`[dojo-api] FATAL: port ${PORT} already in use — another dojo-api instance is running. Refusing to start a duplicate ReflexEngine.`)
  } else {
    console.error('[dojo-api] FATAL: server error before listen:', err)
  }
  process.exit(1)
}
server.on('error', fatalListenError)
wss.on('error', fatalListenError)

server.listen(PORT, HOST, () => {
  console.log(`[dojo-api] listening on ${HOST}:${PORT}`)
  console.log(`[dojo-api] WS at ws://${HOST}:${PORT}/ws`)
  console.log(`[dojo-api] REST at http://${HOST}:${PORT}/api`)
  boot()
})
