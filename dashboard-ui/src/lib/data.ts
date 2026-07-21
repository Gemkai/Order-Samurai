import type { WIDPayload, Pillars, PillarKey, MetricEnvelope } from "@/types"
import type { RingData } from "@/components/charts/ring-context"
import type { PieData } from "@/components/charts/pie-context"

/** Stable model-tier -> color map. Keyed by tier (not positional) so a tier reads as the
 *  same color everywhere — hero pie, per-project mix bars, and legends stay consistent. */
export const TIER_COLORS: Record<string, string> = {
  PREMIUM:  "#f472b6",
  STANDARD: "#facc15",
  FAST:     "#38bdf8",
  LOCAL:    "#4ade80",
  FREE:     "#a78bfa",
  unknown:  "#71717a",
}
export const tierMixColor = (tier: string) => TIER_COLORS[tier] ?? "#71717a"

/** Maps model_tier keys to human-readable model version strings (per-platform representative models). */
export const TIER_MODEL_LABELS: Record<string, string> = {
  PREMIUM:  "claude-opus-4-8 · Gemini Pro",
  FAST:     "claude-haiku-4-5 · Gemini Flash",
  STANDARD: "claude-sonnet-4-6",
  LOCAL:    "Ollama (local)",
  FREE:     "Gemini Flash Free",
  unknown:  "Unknown",
}

export async function loadPayload(): Promise<WIDPayload> {
  const res = await fetch("/wid_payload.json", { cache: "no-store" })
  if (!res.ok) throw new Error(`failed to load payload: ${res.status}`)
  return res.json()
}

export interface FlatMetric {
  group: string
  key: string
  env: MetricEnvelope
}

export function flatten(pillars: Pillars, pk: PillarKey): FlatMetric[] {
  const groups = pillars[pk] ?? {}
  const out: FlatMetric[] = []
  for (const [group, metrics] of Object.entries(groups)) {
    for (const [key, env] of Object.entries(metrics)) out.push({ group, key, env })
  }
  return out
}

const num = (v: string): number => {
  const n = parseFloat(String(v).replace(/[^0-9.-]/g, ""))
  return Number.isFinite(n) ? n : 0
}

/** Percent / score metrics -> rings (value out of 100). */
export function percentRings(metrics: FlatMetric[], accent: string): RingData[] {
  return metrics
    .filter((m) => !m.env.is_simulated && (m.env.is_percent || /grade|pass_rate|density/i.test(m.key)))
    .slice(0, 4)
    .map((m) => ({
      label: metricLabel(m.key),
      value: Math.min(num(m.env.val), 100),
      maxValue: 100,
      color: accent,
    }))
}

/** "FAST:48% LOCAL:41% ..." -> pie slices. */
export function parseMix(val: string): PieData[] {
  return val
    .split(/\s+/)
    .map((tok) => tok.split(":"))
    .filter((p) => p.length === 2)
    .map(([label, pct]) => ({ label, value: num(pct), color: tierMixColor(label) }))
    .filter((s) => s.value > 0)
}

/** Count metrics -> bar series for a single <Bar dataKey="value">. */
export function countBars(metrics: FlatMetric[]): { name: string; value: number }[] {
  return metrics
    .filter((m) => !m.env.is_simulated && m.env.is_count)
    .slice(0, 6)
    .map((m) => ({ name: metricLabel(m.key), value: num(m.env.val) }))
}

export function sparkData(history: number[]): { i: number; value: number }[] {
  return history.map((value, i) => ({ i, value }))
}

export function liveSimCounts(pillars: Pillars): { live: number; sim: number } {
  let live = 0
  let sim = 0
  for (const groups of Object.values(pillars)) {
    for (const metrics of Object.values(groups)) {
      for (const env of Object.values(metrics)) { if (env.is_simulated) sim++; else live++ }
    }
  }
  return { live, sim }
}

// Per-metric copy: `what` = what the number measures · `fix` = what running the remediation does.
// Descriptive display names. Metric KEYS stay stable (history, thresholds.json and
// reflex IDs are keyed on them) — only what the user sees changes. Fallback for
// unmapped keys is underscore→space.
export const METRIC_LABELS: Record<string, string> = {
  // Bow — operational
  Error_Rate: "Task Error Rate",
  Latency_P50: "Median Turn Latency",
  Latency_P95: "Tail Turn Latency (P95)",
  Complexity_Weighted_Throughput: "Complexity-Weighted Throughput",
  Tool_Calls: "Tool Invocations",
  Fallback_Recovery_Rate: "Model Fallback Recovery",
  Session_Count: "Work Sessions",
  Avg_Session_Turns: "Avg Turns per Session",
  Processes_Reaped: "Stale Processes Reaped",
  Agent_Autonomy_Ratio: "Autonomous Action Share",
  Agent_Process_Count: "Live Agent Processes",
  Mechanism_Orphans: "Dormant Mechanisms",
  Governance_Pass_Rate: "Verifier Pass Rate",
  Verifier_Failures: "Failing Verifier Checks",
  Estimated_Agent_Time_Saved: "Est. Agent Time Saved",
  MCP_Smoke_Fails: "MCP Server Failures",
  // Sword — security
  Vulnerability_MTTR: "Vulnerability Time-to-Patch",
  Boundary_Violations: "Workspace Boundary Violations",
  Secrets_Detected: "Hardcoded Secrets Found",
  Rule_Violations: "Principle Rule Violations",
  Rule_Violations_Lifetime: "Principle Rule Violations (Lifetime)",
  Canary_Failures: "Secret Canary Trips",
  Gate_Canary_Fault: "Security Gate Self-Test Fault",
  Security_Scorecard: "Security Posture Score",
  Skill_Safety_Findings: "Skill Supply-Chain Findings",
  Deprecated_Deps: "Outdated Dependencies",
  Governance_Review_Findings: "Governance Review Findings",
  Kill_Chains_Disrupted: "Attack Chains Disrupted",
  Kill_Chains_Detected: "Attack Chains Detected",
  Pending_Chain_Proposals: "Pending Chain Proposals",
  // Brush — architecture & efficiency
  Total_Cost: "Total Spend",
  Token_Spend: "Total Tokens Used",
  Cost_Per_Task: "Cost per Task",
  Token_Execution_Density: "Tokens per Completed Task",
  Local_Routing_Share: "Local Model Share",
  Estimated_Cost_Savings: "Cost Savings",
  Revision_Ratio: "Rewrite-vs-Edit Ratio",
  Subagent_Efficiency_Index: "Subagent ROI Index",
  Chain_Depth_Avg: "Orchestration Load (Median)",
  Hardcoded_Path_Incidents: "Hardcoded Path Leaks",
  Root_Hygiene_Issues: "Repo Root Clutter",
  Architecture_Scorecard_Grade: "Architecture Grade",
  // Arts — craft & UX
  Slop_Density: "AI Slop Density",
  Frustration_Signals: "User Frustration Signals",
  Frustration_Signals_Lifetime: "User Frustration Signals (Lifetime)",
  Rework_Loops: "Rework Loops",
  Rework_Loops_Lifetime: "Rework Loops (Lifetime)",
  Simplify_Runs: "Simplify Pass Count",
  Simplify_Age: "Days Since Simplify Pass",
  Doc_Parity_Issues: "Doc–Code Drift",
  Craft_Improvements: "Craft Improvements",
  Skill_Conflicts: "Overlapping Skills",
  Wiki_Health_Score: "Knowledge Vault Health",
  Wiki_Article_Count: "Vault Articles",
  Raw_Pending: "Unprocessed Captures",
  Wiki_Orphans: "Orphaned Vault Notes",
  // Meta
  Instrumentation_Coverage: "Metric Coverage",
}

export function metricLabel(key: string): string {
  return METRIC_LABELS[key] ?? key.replace(/_/g, " ")
}

export const METRIC_DOCS: Record<string, { what: string; fix: string }> = {
  // Bow — operational
  Error_Rate: { what: "Share of agent tasks that ended in error vs success.", fix: "/investigate root-causes the failing tasks before they compound." },
  Latency_P50: { what: "Median turn latency (ms) across tasks.", fix: "/investigate finds the slow path driving median latency." },
  Latency_P95: { what: "95th-percentile turn latency (ms) — the slow tail.", fix: "/investigate isolates the worst-case slow turns." },
  Complexity_Weighted_Throughput: { what: "Sum of task complexity scores across successful runs.", fix: "/context-optimization optimizes task workflows to reduce complexity." },
  Tool_Calls: { what: "Total tool invocations across tasks.", fix: "/context-optimization cuts redundant tool calls per turn." },
  Fallback_Recovery_Rate: { what: "Percent of model failures gracefully recovered by cascading fallback paths.", fix: "/model-selector optimizes the provider chains and routing strategy." },
  Session_Count: { what: "Distinct work sessions in the window.", fix: "/context-optimization keeps each session focused." },
  Avg_Session_Turns: { what: "Average turns per session — how long it takes to resolve work. High means thrash.", fix: "/plan up front cuts back-and-forth by fixing the approach before execution." },
  Processes_Reaped: { what: "Stale/duplicate background processes the reaper killed.", fix: "/guard checks why orphan processes are spawning." },
  Agent_Autonomy_Ratio: { what: "Percent of agent execution steps initiated autonomously by reflexes vs user prompts.", fix: "Advisory only — auto-firing the autonomy loop to raise this number would be circular (its own runs are the numerator). Raise real autonomy by wiring more verified reflexes." },
  Agent_Process_Count: { what: "Live python/node process footprint right now.", fix: "Advisory only — not auto-reaped (self-heal is too blunt and would target the governance stack itself). Investigate the spawner if the count climbs." },
  Mechanism_Orphans: { what: "Registered mechanisms (hooks/scripts) that are wired but dormant.", fix: "/audit-mechanisms verifies each mechanism runs and is consumed." },
  Governance_Pass_Rate: { what: "Percent of governance verifier checks that passed.", fix: "/audit-mechanisms surfaces and helps fix the failing checks." },
  Verifier_Failures: { what: "Count of FAILing governance verifier checks.", fix: "Run the doctor to see and resolve each failure." },
  // Sword — security
  Vulnerability_MTTR: { what: "Vulnerability Mean Time to Resolution — average days to patch a threat after detection.", fix: "/pip-safe-upgrade executes the patch safely (dry-run, CVE priority, ML-constraint aware)." },
  Boundary_Violations: { what: "Attempts to cross declared layer/archive boundaries.", fix: "Advisory only — guard is preventive, not a remediator (it can't fix an existing crossing). Review the violation in the doctor's boundary verifier and quarantine the offending path; a deterministic boundary-restore bin is planned." },
  Secrets_Detected: { what: "Hardcoded secrets found in the control-plane scan.", fix: "/security-audit scrubs the leaked credential and hardens the path." },
  Rule_Violations: { what: "Logged violations of CLAUDE.md principle rules.", fix: "/policy-enforcement-audit triages and closes the recurring violations." },
  Canary_Failures: { what: "Failed secret-canary tripwires — a fail can mean exfil or a broken detector.", fix: "/security-audit investigates the regression and treats it as a live exposure." },
  Gate_Canary_Fault: { what: "Security-gate self-test: 1 if the gate failed its last canary or the canary is stale (a stale all-clear isn't an all-clear).", fix: "/guard re-runs the gate canary and repairs the gate if it stopped blocking." },
  Security_Scorecard: { what: "Weighted security posture score (0–100): scanning, supply chain, vetting, PII.", fix: "/security-audit raises the lowest-scoring security category." },
  Skill_Safety_Findings: { what: "Critical+warning findings from the red-team safety scan of installed skills.", fix: "Advisory only — no deterministic auto-fix (supply-chain-risk-auditor audits dependency packages, not installed skills). Manually review the flagged skills for injection/secret/subprocess risk." },
  Deprecated_Deps: { what: "Outdated dependency packages (supply-chain drift). The actionable core is the CVE subset — the raw count is dominated by the pinned ML stack.", fix: "/pip-safe-upgrade dry-runs and applies safe upgrades — CVEs first, ML-constraint aware." },
  // Brush — architecture & efficiency
  Total_Cost: { what: "Total $ spent across recorded tasks.", fix: "/context-optimization cuts token waste to lower spend." },
  Token_Spend: { what: "Total tokens (prompt+completion) consumed.", fix: "/context-optimization reduces prompt bloat and rework." },
  Cost_Per_Task: { what: "Mean $ cost per task.", fix: "/context-optimization lowers the per-task token footprint." },
  Token_Execution_Density: { what: "Tokens consumed per successful operation — efficiency.", fix: "/context-optimization improves output-per-token (incl. cavecrew-compressed subagent results, ~60% smaller)." },
  Local_Routing_Share: { what: "Percent of tasks kept on the LOCAL model tier (Ollama) — cheaper, private, offline-capable. Higher is better.", fix: "/context-optimization routes more sensitive/bulk work to the local LLM per policy." },
  Revision_Ratio: { what: "Share of edits that were full rewrites (CLOBBER) vs surgical.", fix: "/simplify favors small, surgical edits over rewrites." },
  Simplify_Age: { what: "Days since /simplify last ran — recency of the second-draft quality gate. Lower is better.", fix: "/simplify now to refresh the gate before quality drifts." },
  Subagent_Efficiency_Index: { what: "Ratio of successful sessions to subagents spawned. Higher means cleaner inline execution.", fix: "/simplify refactors tasks to run inline, reducing subagent overhead." },
  Hardcoded_Path_Incidents: { what: "Hardcoded machine/repo paths vs canonical-truth paths.", fix: "/simplify routes paths through the canonical resolver." },
  Root_Hygiene_Issues: { what: "Top-level entries that breach the root-hygiene policy.", fix: "/doctor checks workspace health and restores the declared root structure." },
  Architecture_Scorecard_Grade: { what: "Weighted architecture grade (0–100) from the scorecard rubric.", fix: "/architecture reviews and raises the lowest-scoring category." },
  // Arts — craft & UX
  Slop_Density: { what: "AI-slop filler phrases per 1k words of output.", fix: "/humanizer rewrites slop-heavy output into clean prose." },
  Frustration_Signals: { what: "User turns expressing dissatisfaction (friction).", fix: "/investigate finds what triggered the friction." },
  Rework_Loops: { what: "User turns asking for correction/redo.", fix: "/insights diagnoses the friction patterns behind the redo requests." },
  Simplify_Runs: { what: "Times the mandated /simplify gate ran.", fix: "Run /simplify before presenting code — it's a required gate." },
  Doc_Parity_Issues: { what: "Broken/undocumented refs (docs out of sync with runtime).", fix: "/wiki reconciles docs with the live runtime." },
  Skill_Conflicts: { what: "Groups of overlapping/conflicting skills.", fix: "/skill-consolidator merges the redundant skills." },
  MCP_Smoke_Fails: { what: "MCP servers failing the connectivity smoke test.", fix: "/mcp-setup repairs the failing MCP server config." },
  // Sword — governance ops
  Governance_Review_Findings: { what: "Total CRITICAL + HIGH issues found in the latest governance code review pass.", fix: "/governance-review opens the adversarial review and addresses the flagged findings." },
  Kill_Chains_Disrupted: { what: "Distinct attack kill-chains actually remediated this week — at least one block/patch/quarantine/revert event. Detection alone does not count.", fix: "/guard runs the kill-chain scanner and surfaces active chains awaiting closure." },
  Kill_Chains_Detected: { what: "Distinct attack kill-chains with any observed event this week, including log-only detections not yet remediated. Detected minus disrupted = open exposure.", fix: "/guard reviews detected chains and drives them to disruption (block/patch/quarantine)." },
  Pending_Chain_Proposals: { what: "Kill-chain proposals queued for operator approval — chains the autonomic layer identified but has not yet acted on.", fix: "/guard reviews and approves (or rejects) the pending proposals." },
  // Bow — autonomic
  Estimated_Agent_Time_Saved: { what: "Approximate hours saved this week by autonomous reflex execution instead of manual intervention (calibrated from dojo task benchmarks).", fix: "/audit-mechanisms ensures the dojo and reflex engine are calibrated and operational." },
  // Brush — token efficiency
  Estimated_Cost_Savings: { what: "Dollars saved this week: cost-per-task improvement vs prior week at this week's task volume — real spend telemetry, measured (not estimated). The former routing-efficiency component ($0.05/run, no sample source) was removed.", fix: "/cost-breakdown-audit attributes spend across model tiers and tasks to find the highest-ROI routing change." },
  Chain_Depth_Avg: { what: "Median Agent/Task tool-call count per session (orchestration load). High values signal sessions that lean heavily on subagents instead of inline work — correlates with latency and cost overhead.", fix: "/subagent-audit identifies sessions that over-orchestrate and proposes inline alternatives." },
  // Arts — craft
  Craft_Improvements: { what: "Real count of discrete craft wins this week — skill promotions plus completed arts backlog items. Replaced the synthetic 'Est. Human Time Saved' (real deltas × hours coefficients with no sample source). Vibe-alignment and doc-parity deltas are tracked as their own metrics and shown in the hero breakdown.", fix: "/improve-system accelerates the learning loop that drives this metric higher." },
  // Arts — Knowledge vault (cross-component: Knowledge → Governance)
  Wiki_Health_Score: { what: "0–100 composite health of the Knowledge vault: penalties for pending raw notes, orphaned articles, stale articles, and empty topic domains.", fix: "/wiki compiles pending raw notes, fixes orphaned articles, and updates stale content." },
  Wiki_Article_Count: { what: "Total curated wiki articles across all topic domains in the Knowledge vault. Informational — not a graded metric.", fix: "/wiki runs the compile step to process raw notes into curated articles." },
  Raw_Pending: { what: "Raw notes in the Knowledge vault inbox not yet compiled into wiki articles. Accumulation means the learning loop is stalled.", fix: "/wiki compiles the raw inbox into structured wiki articles." },
  Wiki_Orphans: { what: "Wiki articles with no incoming wikilinks from anywhere in the vault. Orphaned articles are hard to discover and dilute graph coverage.", fix: "/wiki cross-links orphaned articles to relevant existing content." },
  // Meta
  Instrumentation_Coverage: { what: "Percent of this pillar's gradeable metrics that are live (not simulated). Low coverage means the pillar score is based on thin data — the number is less trustworthy.", fix: "/audit-mechanisms surfaces un-wired scouts and missing telemetry fields that would bring more metrics live." },
}
