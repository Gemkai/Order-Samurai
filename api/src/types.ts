export type PillarSlug = 'bow' | 'sword' | 'brush' | 'arts'
export type RoninStatus = 'idle' | 'running' | 'done' | 'error'

export interface DojoPillar {
  name: string
  ronin_mode: 'ronin' | 'dormant'
  charter_path: string
  live_baseline: number
  live_current: number | null
  last_commit: string | null
}

export interface DojoBacklogItem {
  id: string
  pillar: PillarSlug
  title: string
  value: number
  effort: number
  status: string
}

export interface DojoState {
  run_id: string
  cycle: number
  pillars: Record<PillarSlug, DojoPillar>
  backlog: DojoBacklogItem[]
}

export interface VerdictRecord {
  reflex_id: string
  command?: string
  verdict: 'CONFIRMED' | 'REFUTED' | 'SUSPECT'
  reasoning: string
  evidence: string
  cycle_id: string
  ts: string
  // Governance opt-in grant — sensei-cycle may set true on a CONFIRMED verdict
  // for a mechanism-backed metric (Phase 4). Optional: pre-grant callers and
  // legacy verdict posters omit it without changing semantics.
  reflex_ready?: boolean
}

export interface VerdictMapEntry extends VerdictRecord {
  expiresAt: number  // Date.now() + 24 * 60 * 60 * 1000
}

// WebSocket message types — Server → Client
export type ServerMsg =
  | { type: 'state'; data: DojoState }
  | { type: 'status'; pillar: PillarSlug; status: RoninStatus }
  | { type: 'output'; pillar: PillarSlug; line: string }
  | { type: 'auto_remediation'; pillar: PillarSlug }
  | { type: 'exec_status'; status: RoninStatus }
  | { type: 'exec_output'; line: string }
  | { type: 'auto_reflex_start'; metric: string; tier: string; command: string; category?: string; message?: string }
  | { type: 'auto_reflex_output'; metric: string; line: string }
  | { type: 'auto_reflex_done'; metric: string; command: string; status: 'done' | 'error' | 'timeout'; improved: boolean; stuck: boolean }
  | { type: 'auto_reflex_stuck'; metric: string; command: string; consecutiveNoImprovement: number }
  | { type: 'auto_reflex_pending'; metric: string; command: string; tier: string; windowMs: number; cancelKey: string }
  | { type: 'auto_reflex_skipped'; reflex_id: string; reason: string }

// WebSocket message types — Client → Server
export type ClientMsg =
  | { type: 'toggle'; pillar: PillarSlug }
  | { type: 'run'; pillar: PillarSlug }
  | { type: 'exec'; command: string; scope?: string }
  | { type: 'ping' }
