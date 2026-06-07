# Order Samurai Metric Catalog (canonical)

The single registry the unified aggregator consumes. **Supersedes the Jarvis-inherited catalog** —
Jarvis measured a single Antigravity task flow (generic software metrics: web vitals, CVEs, bundle size).
Order Samurai is a *skill-orchestration agentic OS* (Master Controller → 4 Pillar Orchestrators → Child
Skills), so it measures the **agent's operational surface**, not just the code it writes.

Two metric classes per pillar:
- **Autonomic Governance** — from `autonomous_ronins.md`; measures self-* effectiveness (drift, heal, patch, sprawl).
- **Agent Operation** — measures how the agent actually works (tools, skills, orchestrators, tokens, sessions, knowledge). This is what makes it an *agentic-OS* dashboard.

### Status legend (honesty: a metric is never shown as live if it isn't)
- **LIVE** — computable now from the canonical telemetry schema or our verifiers (AUTO/DERIVED)
- **+FIELD** — needs a new field on the canonical telemetry record
- **+STREAM** — needs a separate event stream (`Data/telemetry/autonomic_events.jsonl`)
- **+SCOUT** — needs a platform-specific collector
- **+SKILL** — needs LLM/manual judgment

---

## ✅ CURRENT LIVE REGISTRY (synced 2026-06-05) — 47 metrics, 0 SIMULATED

Source of truth = `agentica_core/aggregate.py` (REGISTRY + scouts/insights injection). This doc tracks it;
the **design catalog below is the roadmap** (untapped candidate rows + `+FIELD`/`+SCOUT` items not yet wired).
Tier: **AUTO** = verifier/log-derived · **DERIVED** = computed from canonical telemetry. All real.

**🏹 Bow (17)** — Activity: Error_Rate, Latency_P50, Latency_P95, Throughput, Tool_Calls, Tool_Diversity, Session_Count, Avg_Session_Turns, MCP_Smoke_Fails · Autonomic: Processes_Reaped, Config_Drift_Rate, Agent_Process_Count, Mechanism_Orphans · Governance: Governance_Pass_Rate, Verifier_Failures · Failure: Hook_Failure_Rate, Zombie_Process_Count

**⚔️ Sword (12)** — Vulnerability: Open_CVEs · Code Security: Boundary_Violations, Secrets_Detected, Gate_Fires, Secret_Scrubs · Governance: Rule_Violations · Audit Trail: Canary_Failures, Gate_Canary_Fault · Reliability: Loop_Breaker_Fires · Posture: Security_Scorecard · Supply Chain: Skill_Safety_Findings, Deprecated_Deps

**🖌️ Brush (12)** — Token Efficiency: Total_Cost, Token_Spend, Cost_Per_Task, Token_Execution_Density, Model_Tier_Mix, Local_Routing_Share, MCP_vs_CLI_Ratio · Code Health: Revision_Ratio, Hardcoded_Path_Incidents, Root_Hygiene_Issues · Orchestration: Subagent_Spawns · Architecture: Architecture_Scorecard_Grade

**🎭 Arts (8)** — Output Quality: Slop_Density · Interaction: Frustration_Signals, Rework_Loops · Process: Simplify_Runs · Docs: Doc_Parity_Issues · Craft: Skills_Optimized, Skill_Promotions, Skill_Conflicts

> **Wired, populating:** `Simplify_Age` (Arts/Process) — emitter now harvests slash-command skills (`<command-name>` parse), so it flips from SIMULATED to live DERIVED on the first `/simplify` record. `Local_Routing_Share` (Brush) is live from `model_tier==LOCAL`.

**Live sources:** verify_path_authority/root_hygiene/archive_boundaries/runtime_contract + verify_secrets + scouts.security_signals reading `~/.claude/data` (`principle_violations`, `security_gate_log`, `dependency_audit`, `security_scorecard`, `skill_safety_scan`, `skill_*_log`, `skill_conflicts`, `secret_scrubber`, `mcp_smoke_test`, `canary_status`, `security_gate_canary`, `loop_breaker_state`, `mechanism_audit`, `doc_parity`, `mcp_reaper`) + canonical telemetry (transcript-derived; incl. `model_tier`→Local_Routing_Share) + insights (scorecard grade, history snapshots).

**Untapped roadmap rows** (real-source-pending or need emitter fields): Guardrail Blocks · Permission Denials · MCP Attack Surface (`mcp_security_audit` absent) · Nudge Conversion · Eureka Quality (pipeline broken) · Review Findings · all `+FIELD` agent-op metrics (orchestrator/chain_depth/knowledge_refs/phase — need emitter to populate). Note: `mcp_or_cli` field and reducer are now LIVE in `agentica_core/`; emitter not yet writing the field so ratio shows 0.0 until populated.

---

## 🏹 Bow — Operational Status & Agent Activity

### Autonomic Governance
| Metric | Measures | Source | Status |
|--------|----------|--------|--------|
| Config Drift Rate | divergences/day from `anti_drift_policy.json` | verifier results logged over time | +STREAM |
| Mean Time to Heal (MTTH) | seconds to auto-resolve a degradation | autonomic events | +STREAM |
| Zombie Process Count | orphaned/hung background processes (→ 0) | state/autonomic_events.jsonl | LIVE |
| Daemon Restart Count | autonomic daemons killed/restarted | autonomic events | +STREAM |
| Telemetry Ping Success Rate | % health checks returning OK unaided | doctor runs over time | LIVE |

### Agent Operation
| Metric | Measures | Source | Status |
|--------|----------|--------|--------|
| Session Length (turns) | records grouped by `session_id` | telemetry.session_id | LIVE |
| Session Count | distinct sessions/day | telemetry.session_id | LIVE |
| Tool Call Volume | total / avg tool calls per task | telemetry.tool_calls | LIVE |
| Tool Diversity | distinct tools invoked | telemetry.tool_calls_list | LIVE |
| Tool Failure Rate | % tool calls that errored | telemetry.tool_latencies + outcome | +FIELD |
| Error Rate / Latency P50·P95·P99 / Throughput | task health | telemetry | LIVE |
| Knowledge Prompted | # knowledge/lessons/context docs surfaced per task | — | +FIELD (`knowledge_refs`) |
| Rediscovery Rate | re-solving the same problem (repeated task_name) | telemetry.task_name | LIVE (approx) |
| Lesson Graduation Rate | lessons added to the ledger / time | lessons ledger count | +SCOUT |

### Failure & Mechanism Health (cluster D)
| Metric | Measures | Source | Status |
|--------|----------|--------|--------|
| Loop-Breaker Fires | times the 3×-same-error breaker tripped | `loop_breaker_state.json` emissions | LIVE (Sword) |
| Self-Correction Rate | errors fixed autonomously vs escalated to human | telemetry.status + events | +FIELD/+STREAM |
| Mechanism Liveness | registered mechanisms that ran AND had output consumed (3-step Mechanism Rule) | mechanism audit | +STREAM |
| Stale Scheduled Tasks | never_run / failed / stale (automation_scout taxonomy) | scheduled-task scout | +SCOUT |
| Hook Failure Rate | hooks that errored | state/autonomic_events.jsonl | LIVE |

## ⚔️ Sword — Security Integrity

### Autonomic Governance
| Metric | Measures | Source | Status |
|--------|----------|--------|--------|
| Secret Interception Count | secrets caught & scrubbed before commit | verify_secrets / security_gate_log | LIVE |
| Vulnerability Window (Patch Latency) | flagged-vulnerable → autonomous patch | dep-audit scout + events | +SCOUT/+STREAM |
| Boundary Violations Blocked | archive/root violations vs `root_hygiene_policy.json` | verify_archive_boundaries / verify_root_hygiene | LIVE |

### Agent Operation (security-relevant behavior)
| Metric | Measures | Source | Status |
|--------|----------|--------|--------|
| Dangerous Tool Invocations | shell w/ raw input, secret-in-log (CLAUDE.md grep checks) | telemetry tool risk tag | +FIELD |
| Push Bypasses | `--no-verify` / hook bypasses | git scout | +SCOUT |
| Permission Escalations | elevated-access requests | autonomic events | +STREAM |

## 🖌️ Brush — Architecture Optimization & Token Efficiency

### Autonomic Governance
| Metric | Measures | Source | Status |
|--------|----------|--------|--------|
| Architecture Scorecard Grade | aggregate from `architecture_scorecard.json` (complexity, modularity, clean-code) | scorecard eval run | +SCOUT |
| Root Sprawl Index | top-level entries actual vs permitted by `anti_sprawl_policy.json` | verify_root_hygiene | LIVE |
| Hardcoded Path Incidents | hardcoded vs canonical-truth paths | verify_path_authority | LIVE |
| Token Execution Density | tokens consumed ÷ successful operations | telemetry.tokens + status | LIVE |

### Agent Operation (the token-optimization goal lives here)
| Metric | Measures | Source | Status |
|--------|----------|--------|--------|
| **Total Cost / Token Spend** | total $ and tokens | telemetry.total_cost / tokens_* | LIVE |
| **Cost per Task / per Project** | mean spend, with project contributions | telemetry.total_cost, project | LIVE |
| Context Utilization | prompt tokens vs model window | telemetry.tokens_prompt | LIVE |
| Cache Hit Rate | prompt-cache reuse | — | +FIELD (`cache_read_tokens`) |
| Model Tier Mix | distribution across model tiers | telemetry.model_tier | LIVE |
| Revision Ratio | CLOBBER vs SURGICAL edits (rework signal) | telemetry.mod_type | LIVE |
| **Orchestrator Chain Depth & Fan-out** | Master→Orchestrator→Child depth; subagents spawned | — | +FIELD (`orchestrator`, `chain_depth`, `subagent_spawns`) |
| **Subagent Cost Multiplier** | subagent token cost vs inline (CLAUDE.md: 7–10×) | — | +FIELD (`parent_task` attribution) |
| **Skill Utility / Dead Skills** | which skills used; orchestrators & sub-skills never invoked | telemetry.skill_hits ⨯ skill inventory | LIVE (approx) |

### Skill & Orchestration efficiency (cluster A)
| Metric | Measures | Source | Status |
|--------|----------|--------|--------|
| Skill Selection Efficiency | used the lowest-pattern skill that covers the need (priority matrix) | telemetry.skill_tier vs task | +FIELD (`skill_tier`) |
| Skill Escalation Rate | light skill → heavier skill escalations | telemetry chain | +FIELD |
| Orchestrator Routing Accuracy | Master routed to the correct pillar orchestrator | telemetry.orchestrator + outcome | +FIELD |
| Handoff Integrity | % subagent calls with complete context (rule #6) | result-envelope completeness | +FIELD/+STREAM |

### Token-routing discipline (cluster B — sharpest token-optimization metrics)
| Metric | Measures | Source | Status |
|--------|----------|--------|--------|
| MCP-vs-CLI Ratio | MCP calls that should have been CLI (MCP ≈ 35× tokens) | telemetry.mcp_or_cli | LIVE |
| Model Selection Adherence | Opus usage % (target < 20%); Sonnet/Haiku mix | telemetry.model | +FIELD (`model`) |
| Context Cliff Events | sessions crossing ~70% context window | telemetry.tokens_prompt vs window | LIVE (approx) |
| Compaction Events | # /compact + pre-compact extraction compliance | autonomic events (compaction) | +STREAM |
| Cost per Outcome | $ per merged PR / resolved task (not just per task) | telemetry.total_cost + outcome link | +FIELD |

## 🧭 Workflow & Rule Governance (cluster C — cross-pillar; "Self-Governing" under Brush)
| Metric | Measures | Source | Status |
|--------|----------|--------|--------|
| Phase-Gate Compliance | % tasks through Discovery→Plan→Implement with approval | telemetry.phase + approved | +FIELD (`phase`, `approved`) |
| Plan-First Adherence | % 3+ step tasks with a plan artifact before code | telemetry.phase | +FIELD |
| Scope-Drift Incidents | scope changed mid-phase without re-approval | autonomic events (scope_change) | +STREAM |
| Rule Firing Rate | which CLAUDE.md rules fire (and how often) | rule telemetry | +STREAM |
| Rule Violation Rate | principle violations | `principle_violations.jsonl` | +STREAM |
| Dead-Rule Detection | rules untriggered in 90 days (retirement candidates) | derived from rule firing | DERIVED |

## 🎭 Arts — Cultural Arts (UX, Docs, Vibe)

### Autonomic Governance
| Metric | Measures | Source | Status |
|--------|----------|--------|--------|
| Documentation Parity Latency | code change → doc update gap | doc/runtime diff scout | +SCOUT |
| Vibe Alignment (Anti-Slop) Score | output vs vibe pack; AI-slop drift | linter/LLM | +SKILL |
| Visual Regression Delta | pixel-mismatch % in headless UI checks | headless browser scout | +SCOUT |

### Agent Operation
| Metric | Measures | Source | Status |
|--------|----------|--------|--------|
| Skill Documentation Coverage | orchestrators/sub-skills with a valid SKILL.md | skill inventory | LIVE (approx) |
| Output Acceptance Rate | accepted vs reverted/regenerated outputs | — | +FIELD |

---

## Instrumentation gaps — what must be added to make these live

The canonical telemetry schema (`agentica_core/telemetry.py`) was harvested from Jarvis and is
**task-level**. To see the agent-operation metrics above, it needs to grow to **orchestration-level**:

**New optional canonical-record fields:**
- `orchestrator` — which orchestrator/skill drove the task (Master/Bow/Sword/Brush/Arts/none)
- `chain_depth` — Master→Orchestrator→Child depth
- `subagent_spawns` + `parent_task` — subagent fan-out and cost attribution (the 7–10× multiplier)
- `knowledge_refs` — count/ids of knowledge/lessons/context surfaced
- `cache_read_tokens` — prompt-cache reuse
- per-tool outcome in `tool_latencies` (add `ok: bool`)
- `model` — concrete model id (for Model Selection Adherence / Opus<20%)
- `skill_tier` — skill priority tier used (tool-wrapper/reviewer/generator/pipeline) for Skill Selection Efficiency
- `mcp_or_cli` — whether a tool call went via MCP or CLI (MCP-vs-CLI Ratio)
- `phase` + `approved` — 7-phase workflow stage and whether a gate approval was recorded
- `outcome_ref` — link a task to its outcome (merged PR / resolved task) for Cost per Outcome

**New event stream — `Data/telemetry/autonomic_events.jsonl`:**
For things that aren't task records: `{timestamp, event, pillar, detail, duration_ms}` covering
`zombie_killed`, `daemon_restart`, `heal`, `drift_corrected`, `boundary_blocked`, `permission_escalation`,
`loop_breaker_fire`, `hook_failure`, `scope_change`, `compaction`, `mechanism_run`, `rule_violation`.
This is the source for the autonomic + failure + governance metrics (Zombie/Daemon counts, MTTH,
Config Drift Rate, Boundary Violations, Loop-Breaker Fires, Rule Violations, Scope Drift, Mechanism Liveness).

## Harness-derived expansion (2026-06-01) — the harness as a sensor array

The agent harness already generates security/quality signals via its hooks, gates, scrubbers, and
quality skills. These flesh out the thin Sword/Arts pillars. Source scripts are real (`~/.claude/scripts`).

### ⚔️ Sword — capture the harness's security decisions
| Metric | Signal source | Status |
|--------|---------------|--------|
| Guardrail Blocks | `guardrails.py` + `guardrail_patterns.json` (PreToolUse) | +STREAM (hook → autonomic_events) |
| Protected-Shell / Asset Blocks | `protected_shell_gate.py`, `protected_asset_gate.py` | +STREAM |
| Real-time Secret Scrubs | `secret_scrubber_realtime.py` (interception count) | +STREAM |
| Dep-Audit Blocks | `dep_audit_gate.py` | +STREAM |
| **Permission Denials** | auto-mode classifier refusals (self-mod/persistence blocked) | +STREAM (novel) |
| Sandbox-Disable Events | `dangerouslyDisableSandbox` usage | +FIELD |
| Unaudited Skills (supply chain) | `skill_install_gate.py` / `skill_security_audit.py` / skill-install-reconcile | +SCOUT |
| MCP Attack Surface | `mcp_security_audit.py` (enabled + broad-scope count) | +SCOUT |
| Security Score | `score_security.py` | +SCOUT (read existing output) |
| Principle Violations | `principle_audit.py` → `principle_violations.jsonl` | +STREAM |
| Canary Health | `security_gate_canary.py` / `behavioral_canary.py` alive? | +SCOUT |

### 🎭 Arts — mine the conversation + quality skills
| Metric | Signal source | Status |
|--------|---------------|--------|
| **Slop_Density** | transcript: AI-slop markers + em-dashes / 1k words (`humanizer`/`ai-slop-cleaner`) | **LIVE** |
| **Frustration_Signals** | transcript: user dissatisfaction turns | **LIVE** |
| **Rework_Loops** | transcript: user correction/redo turns | **LIVE** |
| **Simplify_Runs** | transcript: `skills_used` contains `simplify` (mandated gate adherence) | **LIVE** |
| Simplify_Reduction | `code-simplifier.mjs` lines/complexity removed | +FIELD |
| Review Findings | `ce-code-review` / `gsd-code-review` findings by severity | +SCOUT |
| Nudge Conversion Rate | `nudge_conversion_tracker.py` / `nudge_score.py` | +SCOUT |
| Design Fidelity / A11y | `visual-verdict` / `design-review` | +SCOUT/+SKILL |
| Anti-Slop (LLM) | `humanizer` LLM judgment (vs the heuristic Slop_Density) | +SKILL |
| Lesson/Eureka Quality | `eureka_score.py` | +SCOUT |

Also fixed: **Tool_Diversity** is now LIVE (the emitter emits `tool_calls_list`).

## How the aggregator consumes this
This catalog IS the aggregator's metric registry: each row → `{pillar, group, metric, source, reducer, tier}`.
The aggregator computes LIVE rows now, declares the rest as `SIMULATED` with their declared source
(never faked). Build order: (1) extend the telemetry schema with the fields above, (2) add the
`autonomic_events` stream, (3) build the aggregator over this registry. MVP = all LIVE rows, which
already covers Token Spend/Density, Tool & Session activity, Secret Interception, Boundary Violations,
Sprawl, and Hardcoded Paths across both platforms.
