// Canonical WIDPayload shape (produced by agentica_core.aggregate)
export interface MetricEnvelope {
  val: string
  delta: string
  trend: "up" | "down" | "neutral"
  history: number[]
  is_percent: boolean
  is_count: boolean
  is_simulated: boolean
  is_graded?: boolean
  tier: "AUTO" | "DERIVED" | "SIMULATED" | "SKILL"
  timestamp: string
  flagged?: boolean
  mitigation_skill?: string
  mitigation_command?: string
  calibrated?: boolean
  data_gap?: boolean
  /** Human-readable component breakdown for composite metrics (e.g. the Arts
   *  Craft_Improvements hero: "Vibe +6 · Doc-parity +3d · 2 promos · 1 arts item"). */
  detail?: string
  /** Effective (post-calibration) grading rule — present only on graded metrics. */
  rule?: { dir: "lower" | "higher"; warn: number; fail: number; weight: number; per?: string | null }
  /** Per-metric SLO status — "OK" | "WARN" | "FAIL" | "needs:human". Set server-side (insights.annotate)
   *  for graded live metrics only; absent on informational/simulated metrics. */
  status?: "OK" | "WARN" | "FAIL" | "needs:human"
}

export interface CategoryScore {
  // "score"/"grade" removed 2026-07-19: no weighted-mean pillar score exists anymore.
  // Status is `rollup` (worst tier + counts); per-metric letter grades live in `flags`.
  graded_count?: number
  total_gradeable?: number
  coverage_pct?: number | null
  flags: { name: string; val: string; grade: string }[]
  /** Pillar STATUS: worst metric tier wins — no averaging away of hard failures. */
  rollup?: { worst: "PASS" | "HIGH" | "CRITICAL"; passing: number; graded: number }
}

export type Group = Record<string, MetricEnvelope>
export type Pillars = Record<PillarKey, Record<string, Group>>
export type PillarKey = "bow" | "sword" | "brush" | "arts"

export interface TierMix {
  backing: string
  slices: string | null
}

export interface DojoStatePillar {
  ronin_mode: "ronin" | "dormant"
  live_current: number | null
  live_baseline: number
}

/** Per-category architecture decomposition (plan Phase 5). Emitted by
 *  agentica_core.aggregate.architecture_breakdown from the scorecard output artifact.
 *  The 0–100 score is demoted to a collapsed figure; the category statuses are the headline. */
export interface ArchitectureCategory {
  id: string
  label: string
  weight: number
  earned: number
  status: "pass" | "advisory_warn" | "advisory_gap" | "blocking" | string
  missing_verifiers: string[]
  warnings: { verifier?: string; status?: string; label?: string; detail?: string }[]
}

export interface ArchitectureBreakdown {
  score: number | null
  target_score: number | null
  merge_floor: number | null
  release_floor: number | null
  meets_merge_floor: boolean | null
  meets_release_floor: boolean | null
  enforcement_mode: string | null
  blocking_categories: string[]
  advisory_gaps: string[]
  categories: ArchitectureCategory[]
  generated_at: string | null
}

export interface WIDPayload {
  schema_version: string
  timestamp: string
  platforms: string[]
  record_counts: Record<string, number>
  window: { days: number; records: number }
  category_scores: Record<PillarKey, CategoryScore>
  category_scores_lifetime: Record<PillarKey, CategoryScore>
  summaries: Record<PillarKey, string>
  tier_mix: Record<PillarKey, TierMix>
  pillars: Pillars
  by_platform: Record<string, Pillars>
  by_platform_scores: Record<string, Record<PillarKey, CategoryScore>>
  radar_week: { week: string; records: Record<string, number> }
  by_project: Record<string, { platform: string; records: number; has_data: boolean; scores: Record<PillarKey, number>; metrics?: Record<string, number>; tier_mix?: Record<PillarKey, TierMix>; dojo_state?: Record<PillarKey, DojoStatePillar> }>
  by_tier?: Record<string, Pillars>
  by_tier_scores?: Record<string, Record<PillarKey, CategoryScore>>
  reflexes: Reflex[]
  remediation_efficacy: {
    applied: number; improved: number; regressed: number; flat: number
    /** Every exec_log run, whatever its outcome (incl. no_change/error/timeout) —
     *  "tried 49 times, improved nothing" must render as that, not as silence. */
    attempted?: number
    /** Attempted runs that finished status "done". */
    completed?: number
    success_rate: number | null
    by_skill: Record<string, { applied: number; improved: number; attempted?: number }>
    events: { metric: string; skill: string; command: string; before: number; after: number; outcome: string; used_at: string; actor?: "human" | "ronin" }[]
    note: string
    /** Skills that hit the loop-breaker — ran LOOP_BREAKER_LIMIT times with no improvement. */
    stuck_remediations?: StuckRemediation[]
  }
  top_usage?: {
    skills: { name: string; count: number }[]
    connections: { name: string; count: number }[]
    agents: { name: string; count: number }[]
  }
  architecture?: ArchitectureBreakdown | null
  needs_attention?: NeedsAttention
}

/** The ONE legitimate composite (plan Phase 2): metrics currently breaching their SLO.
 *  Decomposable by construction — `count === items.length`. Never drives a reflex or grade;
 *  rendered as a triage label beside an always-visible list, NOT a hero KPI number. */
export interface NeedsAttentionItem {
  metric: string
  status: "WARN" | "FAIL" | "needs:human"
  pillar: PillarKey
  severity: number   // 0 = FAIL, 1 = WARN (sort key)
  weight: number     // sort hint only — never a multiplier here
  val: string
}
export interface NeedsAttention {
  count: number
  items: NeedsAttentionItem[]
}

export interface StuckRemediation {
  reflex_id: string
  pillar: string
  metric: string
  skill: string
  command: string
  runs_attempted: number
  improved_count: number
  impact_rate: number | null
  last_run_at: string | null
  last_status: 'done' | 'error' | 'timeout' | null
  /** Why the skill isn't moving the metric — guides the correct human intervention. */
  failure_mode: 'audit_only' | 'accumulation' | 'behavioral' | 'auto_fixable'
  recommendation: string
  unstick_endpoint: string
}

export interface Reflex {
  id: string
  tier: "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | "INFO"
  category: string
  source: "metric" | "nudge"
  trigger: string
  target?: string
  message: string
  command: string | null
  last_fired: string | null
  status: "active" | "fired" | "armed"
  stuck?: boolean
  /** Worst-contributing project for a per-project behavioural metric (reflexes.py _PROJECT_SCOPABLE);
   *  threaded into the manual-run exec so a click targets the noisiest project. Absent on global metrics. */
  scope?: string
  /** Present (false) only for metrics marked auto_remediable:False — the dashboard shows these
   *  as advisory (no run button), since the routed skill is preventive/circular/wrong-domain. */
  auto_remediable?: boolean
}

export interface PillarMeta {
  key: PillarKey
  label: string
  category: string  // human-readable domain label used in page headers
  glyph: string
  accent: string // css var color
  headline?: string // metric key used as the page headline score
  headlineLabel?: string // human-readable name of the headline metric
  headlineDesc?: string  // hover description explaining the metric methodology
  /** Real measured metric shown as the hero WHILE the headline estimate is
   *  uncalibrated (env.calibrated === false). Calibration is the promotion gate:
   *  the estimate earns the hero slot the moment its 20-sample gate passes. */
  fallback?: { key: string; label: string; desc: string }
}

export const PILLARS: PillarMeta[] = [
  { key: "bow",   label: "Bow",   category: "Operations",   glyph: "🎯", accent: "var(--bow)",   headline: "Estimated_Agent_Time_Saved",
    headlineLabel: "Agent Hours Saved",
    headlineDesc: "Agent developer hours returned via automated workflows & unattended runs this week." },
  { key: "sword", label: "Sword", category: "Security",     glyph: "🗡️", accent: "var(--sword)", headline: "Kill_Chains_Disrupted",
    headlineLabel: "Kill Chains Disrupted",
    headlineDesc: "Distinct ATT&CK attack paths & prompt injection kill chains neutralized by fail-closed security hooks." },
  { key: "brush", label: "Brush", category: "Architecture", glyph: "🌸", accent: "var(--brush)", headline: "Estimated_Cost_Savings",
    headlineLabel: "Cost Savings",
    headlineDesc: "USD saved this week via token optimization, local model routing, and runaway spend prevention." },
  { key: "arts",  label: "Arts",  category: "Craft",        glyph: "👺", accent: "var(--arts)",  headline: "Human_Hours_Saved",
    headlineLabel: "Human Hours Saved",
    headlineDesc: "Developer review & QA hours saved by autonomous verifiers, auto-cleaning, and doc parity." },
]
