import { useEffect, useRef, useState } from 'react'

export type PillarSlug = 'bow' | 'sword' | 'brush' | 'arts'
export type RoninStatus = 'idle' | 'running' | 'done' | 'error'

export interface DojoPillar {
  name: string
  ronin_mode: 'ronin' | 'dormant'
  live_baseline: number
  live_current: number | null
  last_commit: string | null
}

export interface DojoState {
  run_id?: string
  cycle: number
  pillars: Record<PillarSlug, DojoPillar>
}

export interface PendingApproval {
  command: string
  tier: string
  windowMs: number
  cancelKey: string
}

export interface DojoProps {
  dojoState: DojoState | null
  roninStatus: Partial<Record<PillarSlug, RoninStatus>>
  roninOutput: Partial<Record<PillarSlug, string[]>>
  connected: boolean
  /** True when any pillar's manual cycle is in-flight or queued. */
  anyRunning: boolean
  /** The command currently running (or last ran) via exec. */
  execCommand: string | null
  execStatus: RoninStatus
  execOutput: string[]
  /** Reflex IDs the ReflexEngine is currently handling autonomously (auto_reflex_start → done/stuck). */
  activeReflexIds: Set<string>
  /** Reflexes awaiting approval before the ReflexEngine will fire them. Keyed by metric ID. */
  reflexPendingApprovals: Map<string, PendingApproval>
  /** Streaming output lines per metric from auto_reflex skill executions. */
  reflexOutput: Record<string, string[]>
  /** Last pillar that received an auto-remediation run (cleared on next auto_remediation event). */
  lastAutoRemediationPillar: PillarSlug | null
  toggle: (pillar: PillarSlug) => void
  run: (pillar: PillarSlug) => void
  exec: (command: string, scope?: string) => void
  cancelReflex: (cancelKey: string) => Promise<void>
}

const DEFAULT_DOJO_STATE: DojoState = {
  cycle: 12,
  pillars: {
    sword: { name: 'Sword', ronin_mode: 'ronin', live_baseline: 11, live_current: 11, last_commit: 'a1b2c3d' },
    bow: { name: 'Bow', ronin_mode: 'ronin', live_baseline: 20, live_current: 18, last_commit: 'e5f6g7h' },
    brush: { name: 'Brush', ronin_mode: 'ronin', live_baseline: 12, live_current: 12, last_commit: 'i8j9k0l' },
    arts: { name: 'Arts', ronin_mode: 'ronin', live_baseline: 8, live_current: 8, last_commit: 'm1n2o3p' },
  }
}

export function useDojo(): DojoProps {
  const [dojoState, setDojoState] = useState<DojoState | null>(DEFAULT_DOJO_STATE)
  const [roninStatus, setRoninStatus] = useState<Partial<Record<PillarSlug, RoninStatus>>>({})
  const [roninOutput, setRoninOutput] = useState<Partial<Record<PillarSlug, string[]>>>({})
  const [connected, setConnected] = useState(true)
  const [execCommand, setExecCommand] = useState<string | null>(null)
  const [execStatus, setExecStatus] = useState<RoninStatus>('idle')
  const [execOutput, setExecOutput] = useState<string[]>([])
  const [activeReflexIds, setActiveReflexIds] = useState<Set<string>>(new Set())
  const [reflexPendingApprovals, setReflexPendingApprovals] = useState<Map<string, PendingApproval>>(new Map())
  const [reflexOutput, setReflexOutput] = useState<Record<string, string[]>>({})
  const [lastAutoRemediationPillar, setLastAutoRemediationPillar] = useState<PillarSlug | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const retryCountRef = useRef(0)

  useEffect(() => {
    let destroyed = false

    const connect = () => {
      if (destroyed) return
      try {
        const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
        const socket = new WebSocket(`${proto}//${window.location.host}/ws`)
        wsRef.current = socket

        socket.onopen = () => { if (!destroyed) { retryCountRef.current = 0; setConnected(true) } }
        socket.onclose = () => {
          if (destroyed) return
          setConnected(false)
          // Reset any in-flight 'running' statuses — if the socket dropped mid-cycle
          // the terminal done/error message was never received. Without this reset
          // the RUN button stays permanently locked until a page reload.
          setRoninStatus((s) => {
            const next = { ...s }
            for (const k of Object.keys(next) as PillarSlug[]) {
              if (next[k] === 'running') next[k] = 'idle'
            }
            return next
          })
          const delay = Math.min(2000 * 2 ** retryCountRef.current, 30000)
          retryCountRef.current += 1
          retryRef.current = setTimeout(connect, delay)
        }
        socket.onerror = () => { socket.close() }
        socket.onmessage = (ev) => {
          if (destroyed) return
          try {
            const msg = JSON.parse(ev.data as string) as { type: string; [k: string]: unknown }
            if (msg.type === 'state') {
              setDojoState(msg['data'] as DojoState)
            } else if (msg.type === 'status') {
              setRoninStatus((s) => ({ ...s, [msg['pillar'] as PillarSlug]: msg['status'] as RoninStatus }))
            } else if (msg.type === 'output') {
              const pillar = msg['pillar'] as PillarSlug
              const line = msg['line'] as string
              setRoninOutput((s) => ({
                ...s,
                [pillar]: [...(s[pillar] ?? []).slice(-49), line],
              }))
            } else if (msg.type === 'exec_status') {
              setExecStatus(msg['status'] as RoninStatus)
            } else if (msg.type === 'exec_output') {
              setExecOutput((prev) => [...prev.slice(-99), msg['line'] as string])
            } else if (msg.type === 'auto_reflex_start') {
              const id = msg['metric'] as string
              setActiveReflexIds((prev) => new Set(prev).add(id))
              // Clear any pending approval once the engine actually starts executing
              setReflexPendingApprovals((prev) => { const n = new Map(prev); n.delete(id); return n })
            } else if (msg.type === 'auto_reflex_done' || msg.type === 'auto_reflex_stuck') {
              const id = msg['metric'] as string
              setActiveReflexIds((prev) => { const n = new Set(prev); n.delete(id); return n })
              setReflexPendingApprovals((prev) => { const n = new Map(prev); n.delete(id); return n })
            } else if (msg.type === 'auto_reflex_pending') {
              const id = msg['metric'] as string
              setReflexPendingApprovals((prev) => new Map(prev).set(id, {
                command: msg['command'] as string,
                tier: msg['tier'] as string,
                windowMs: msg['windowMs'] as number,
                cancelKey: msg['cancelKey'] as string,
              }))
            } else if (msg.type === 'auto_reflex_output') {
              const id = msg['metric'] as string
              const line = msg['line'] as string
              setReflexOutput((prev) => ({ ...prev, [id]: [...(prev[id] ?? []).slice(-49), line] }))
            } else if (msg.type === 'auto_remediation') {
              setLastAutoRemediationPillar(msg['pillar'] as PillarSlug)
            }
          } catch { /* ignore malformed */ }
        }
      } catch {
        const delay = Math.min(2000 * 2 ** retryCountRef.current, 30000)
        retryCountRef.current += 1
        retryRef.current = setTimeout(connect, delay)
      }
    }

    connect()

    return () => {
      destroyed = true
      if (retryRef.current) clearTimeout(retryRef.current)
      wsRef.current?.close()
    }
  }, [])

  const send = (msg: object) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg))
    }
  }

  const anyRunning = Object.values(roninStatus).some((s) => s === 'running')

  const cancelReflex = async (cancelKey: string) => {
    await fetch(`/api/reflex/cancel/${encodeURIComponent(cancelKey)}`, { method: 'POST' })
  }

  return {
    dojoState,
    roninStatus,
    roninOutput,
    connected,
    anyRunning,
    execCommand,
    execStatus,
    execOutput,
    activeReflexIds,
    reflexPendingApprovals,
    reflexOutput,
    lastAutoRemediationPillar,
    toggle: (pillar) => send({ type: 'toggle', pillar }),
    run: (pillar) => send({ type: 'run', pillar }),
    exec: (command, scope) => {
      setExecCommand(command)
      setExecOutput([])
      setExecStatus('idle')
      send({ type: 'exec', command, ...(scope ? { scope } : {}) })
    },
    cancelReflex,
  }
}
