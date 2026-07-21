// SLO status for the status-first surfaces (plan Phase 1).
//
// The authoritative OK/WARN/FAIL comes from the server (insights.annotate sets env.status from
// the same session-normalized health curve the pillar rollup uses) so a badge can never disagree
// with the rollup or the needs-attention count. This module only *classifies* an envelope into a
// display state; it does NOT recompute thresholds client-side (that would drift from calibration).
import type { MetricEnvelope } from "@/types"

export type SloStatus =
  | "OK"        // meeting target
  | "WARN"      // at-risk (between warn and fail)
  | "FAIL"      // breached
  | "needs:human" // stuck reflex requiring human intervention
  | "INFO"      // informational / no-target (no threshold — never invent one)
  | "NODATA"    // simulated or no real value yet → render "—", not a false 0

export function metricStatus(env: MetricEnvelope): SloStatus {
  if (env.is_simulated) return "NODATA"
  if (env.status) return env.status        // server-set, authoritative
  if (!env.rule) return "INFO"             // no threshold → untargeted, neutral (not "passing")
  // Graded metric with a rule but no server status (defensive: missing/unparseable value).
  return "NODATA"
}

/** True for the WARN+FAIL+needs:human set that drives the needs-attention triage label. */
export function needsAttention(env: MetricEnvelope): boolean {
  const s = metricStatus(env)
  return s === "WARN" || s === "FAIL" || s === "needs:human"
}
