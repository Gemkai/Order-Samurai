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
- **LLM-JUDGED** — scored now by an LLM classifier-judge (`agentica_core/evals/`); reproducible only up to model variance — honest-lower than a deterministic LIVE, honest-higher than SIMULATED. Carries a "· llm-judged" badge and is never shown as ground truth. (Ratified 2026-07-15.)

---

## ✅ CURRENT LIVE REGISTRY (synced 2026-06-07) — 47 metrics, 0 SIMULATED

Source of truth = `agentica_core/aggregate.py` (REGISTRY + scouts/insights injection). This doc tracks it;
the **design catalog below is the roadmap** (untapped candidate rows + `+FIELD`/`+SCOUT` items not yet wired).
Tier: **AUTO** = verifier/log-derived · **DERIVED** = computed from canonical telemetry. All real.
Wired reducers in `aggregate.py`: **22** (was 19; +3 added 2026-06-07: Governance_Pass_Rate, Principle_Violations, Loop_Breaker_Fires).

**🏹 Bow (21)** — Activity: Error_Rate, Latency_P50, Latency_P95, Throughput, Tool_Calls, Tool_Diversity, Session_Count, Avg_Session_Turns, MCP_Smoke_Fails · Autonomic: Processes_Reaped, Config_Drift_Rate, Agent_Process_Count, Mechanism_Orphans · Governance: Governance_Pass_Rate, Verifier_Failures, Principle_Violations · Failure: Hook_Failure_Rate, Zombie_Process_Count, Loop_Breaker_Fires · Agent Operation: Lesson_Graduation_Rate

**⚔️ Sword (11)** — Vulnerability: Open_CVEs · Code Security: Boundary_Violations, Secrets_Detected, Gate_Fires, Secret_Scrubs · Governance: Rule_Violations · Audit Trail: Canary_Failures, Gate_Canary_Fault · Posture: Security_Scorecard · Supply Chain: Skill_Safety_Findings, Deprecated_Deps

**🖌️ Brush (13)** — Token Efficiency: Total_Cost, Token_Spend, Cost_Per_Task, Token_Execution_Density, Model_Tier_Mix, Local_Routing_Share, MCP_vs_CLI_Ratio, Cache_Hit_Rate · Code Health: Revision_Ratio, Hardcoded_Path_Incidents, Root_Hygiene_Issues · Orchestration: Subagent_Spawns · Architecture: Architecture_Scorecard_Grade

**🎭 Arts (9)** — Output Quality: Slop_Density · Interaction: Frustration_Signals, Rework_Loops, Stop_Hook_Loops · Process: Simplify_Runs · Docs: Doc_Parity_Issues · Craft: Skills_Optimized, Skill_Promotions, Skill_Conflicts

**Metric_Live_Fraction: 22/47 = 47%** (wired reducers ÷ full catalog)

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
| Lesson Graduation Rate | % of skills ever added to the real lesson ledger that graduated to a proven-effective RULE | `~/.claude/data/skill_improve_queue.jsonl` (ledger) vs. `~/.claude/data/auto_eureka_skills.md` RULE section (`_lesson_graduation_rate`, `aggregate.py`) | **LIVE** (AUTO-017, 2026-07-14) |

### Harness Weakness (cluster W — 2026-07-16, self-harness M2 intake)
> **The mechanism layer.** Every metric above fires on a *symptom* (a number crossed a line). These
> two fire on a *recurring behavioural mechanism* — the missing input to stage 1 of the self-harness
> loop (`bin/weakness_mining_scout.py`, Research/SELF_HARNESS_EVOLUTION_PLAN.md M2). A cluster is a
> group of failed runs sharing an exact `(terminal_cause, causal_status, mechanism)` signature.
> Counted only when recurrent (support ≥ 3) AND addressable by a declared editable-surface key —
> the paper's own rule that not every cluster deserves a fix (task difficulty, flakiness, and model
> capability limits are excluded, not patched).
>
> **Known substrate gap (measured 2026-07-16):** trace coverage is **0.083** — `reflex_output.jsonl`
> began ~2026-07-13 and captures stdout for only some metrics, so 11 of 12 failed runs in the window
> have no trace to attribute. The judge is not the bottleneck (0 judge gaps). These metrics will read
> near-zero until per-run trace capture exists; that is an honest gap, not a clean bill of health.

| Metric | Measures | Source | Status |
|--------|----------|--------|--------|
| Weakness_Cluster_Count | recurring, addressable agent failure mechanisms (dir=lower; 0 = no known fixable weakness) | `state/weakness_clusters.json` → `actionable_count` (`bin/weakness_mining_scout.py`) | +SCOUT (intake — awaiting replenish_backlog → ronin propose) |
| Top_Cluster_Support | size of the largest actionable cluster (dir=lower; how concentrated the worst weakness is) | `state/weakness_clusters.json` → `top_cluster_support` | +SCOUT (intake — awaiting replenish_backlog → ronin propose) |
| Tool_Retry_Rate | share of tool calls re-issued with IDENTICAL arguments — the blind-retry mechanism, measured not judged (dir=lower) | `state/tool_trust.json` → `retry_rate` (`bin/tool_trust_annotator.py`, **heuristic** kind — no LLM) | +SCOUT (intake — awaiting replenish_backlog → ronin propose) |

> **Tool_Retry_Rate is deliberately a NEW metric, not a change to `Tool_Response_Utilization`.**
> The tool-trust annotator produces a *deterministic* downstream-fate label, which outranks that
> metric's 12B judgment on the honesty ladder — but rewiring a LIVE metric's computation is a
> semantics change that belongs in intake and ratification, not in a mechanical milestone. First
> live reading (2026-07-16, 3,286 calls / 60 sessions): trust 93.1%, retry 1.8%, errored-no-retry
> 5.1% — and one real finding already: `mcp__Claude_Browser__computer` re-issues **41%** (45/110)
> of its calls with identical arguments, versus ~0–2% for Bash/Edit/Write.

### Agent Yield / Delivery (cluster Y — 2026-07-14 grill, qwen-reviewed)
> **The output layer.** The dashboard measured hygiene (ratios flat when healthy) and cost, but not
> *yield* — a week of 146 commits / 33 wargames / 32 graduated lessons moved zero metrics. Design rule
> from the grill: **credit is only counted when gated by a completion/quality check — never by raw
> volume.** Commit-count, LOC, and `feat:`-prefix candidates (Merge_Throughput, Docs_Written,
> Net_Code_Delta, Feature_Ship_Rate, raw Tests_Added) were REJECTED as gameable vanity. Intake only:
> these flow `METRICS.md → replenish_backlog → ronin propose`; the LIVE registry is never wired directly.

| Metric | Measures | Source | Status |
|--------|----------|--------|--------|
| Remediation Efficacy | % of fired remediations that **causally improved** their metric (`improved ÷ applied`); the `improved` flag is set by the **rival adversarial verifier**, NOT self-reported | `wid_payload.json.remediation_efficacy` (already computed: 18/25 = 72%; from `exec_log.jsonl` + sensei rival verdicts, `aggregate.py`) | **LIVE — display+grade only, no emitter.** dir=higher; thresholds PROVISIONAL (warn 60 / fail 45) and gated: show ungraded until n≥20 applied-in-window (mirrors `_MIN_HISTORY` sigma-gate) — n=25 makes 60/45 jittery |
| Backlog Burn Rate | validated backlog items **whose ronin-validation gate passed AND whose commit landed** per window, value-weighted — explicitly NOT raw `proposed→live` status flips (agent-writable, fakeable) | `PROPOSED_BACKLOG.json` (status+approved+triaged_at) + landed-commit check | +STREAM (needs a status-transition ledger emitter) |
| Metric Live Fraction | catalog dark→live conversion = LIVE reducers ÷ full catalog (doc computes 22/47 today); a row counts LIVE only if its reducer is **wired + validated** | `aggregate.py` REGISTRY vs this catalog | LIVE (**Meta** class — distinct from per-pillar Instrumentation_Coverage; it tracks the ronins' own output, not agent work) |

### Capability Growth (cluster K — capacity, not yield)
> qwen review finding #4: skill accumulation is **capacity-building**, not immediate output — kept out
> of the Yield cluster to avoid implying double-count with Lesson Graduation (lessons build skills).

| Metric | Measures | Source | Status |
|--------|----------|--------|--------|
| Skill Promotions | skills promoted a tier (proven-effective) per window — promotion-gated, distinct from Lesson Graduation (lesson→RULE) | already in `METRIC_CONFIG` (`/skill-creator`); `skill_efficacy.json` | surface (already mapped, not currently displayed) |

### Roadmap — honest test yield (not added; +SCOUT)
> The ONLY honest test-yield signal. Raw `Tests_Added` (LOC) was cut as gameable vanity.

| Metric | Measures | Source | Status |
|--------|----------|--------|--------|
| Test Coverage Delta | net coverage-% change per window (real yield, not LOC) | `pytest --cov` run over time | +SCOUT (needs a coverage collector) |

### Failure & Mechanism Health (cluster D)
| Metric | Measures | Source | Status |
|--------|----------|--------|--------|
| Loop-Breaker Fires | times the 3×-same-error breaker tripped | `loop_breaker_state.json` emissions | **LIVE** |
| Self-Correction Rate | % of applied autonomous remediations that IMPROVED their target metric (improved ÷ applied) — the reflex/sensei self-healing success rate | `remediation.efficacy()` (real per-event before/after) → bow/Autonomic/Self_Correction_Rate | **LIVE** (AUTO-005, 2026-07-09; calibrated day-one, no seed coefficient) |
| Mechanism Liveness | count of real `mechanism_run` events (registered mechanisms that ran AND had output consumed, 3-step Mechanism Rule) this window | `~/.claude/data/mechanism_audit.json` (scouts/autonomic_events_scout.py emitter) + ReflexEngine exec_log bridge (`refresh_dashboard.py`) → `Data/telemetry/autonomic_events.jsonl` → bow/Autonomic/Mechanism_Liveness | **LIVE** (AUTO-019, 2026-07-12; count-based, not per-mechanism ratio — data_gap until the emitter has produced an event) |
| Stale Scheduled Tasks | never_run / failed / stale (automation_scout taxonomy) | scheduled-task scout | +SCOUT |
| Hook Failure Rate | hooks that errored | state/autonomic_events.jsonl | LIVE |
| Skill Index Staleness | claude_skills points whose payload path no longer resolves on disk (→ 0) | Qdrant `claude_skills` scroll + `os.path.exists` | PROPOSED 2026-07-06 · source-ready |
| Skill Discovery Recall | recall@1 of semantic skill-search over claude_skills (self/fragment probe) | `benchmarks/bench_skill_discovery.py` json | PROPOSED 2026-07-06 · needs scheduled run |

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
| Dangerous Tool Invocations | real `guardrails` PreToolUse hook blocks (dangerous Bash/git pattern or protected-path Read) | `telemetry.dangerous_tool_invocations` field, populated by `count_dangerous_tool_invocations()` reading `~/.claude/data/hook_timings.jsonl` (hook_dispatch.py's own dispatch log; hook=="guardrails", status=="blocked") | +FIELD (AUTO-013, 2026-07-12 — schema + real reader landed; not yet wired into an autonomic_events emitter or aggregate.py REGISTRY) |
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
| Cache Hit Rate | prompt-cache reuse (`cache_read_input_tokens` / total input) | `~/.claude/projects/**/*.jsonl` message.usage (`_cache_hit_rate`, `aggregate.py`) | LIVE |
| Model Tier Mix | distribution across model tiers | telemetry.model_tier | LIVE |
| Revision Ratio | CLOBBER vs SURGICAL edits (rework signal) | telemetry.mod_type | LIVE |
| **Orchestrator Chain Depth & Fan-out** | orchestration fan-out: Agent+Task calls/session (`chain_depth`); Agent spawns (`subagent_spawns`) — NOT nesting depth | — | +FIELD (`orchestrator`, `chain_depth`, `subagent_spawns`) |
| **Subagent Cost Multiplier** | subagent token cost vs inline (CLAUDE.md: 7–10×) | — | +FIELD (`parent_task` attribution) |
| **Skill Utility / Dead Skills** | which skills used; orchestrators & sub-skills never invoked | telemetry.skill_hits ⨯ skill inventory | LIVE (approx) |

### Skill & Orchestration efficiency (cluster A)
| Metric | Measures | Source | Status |
|--------|----------|--------|--------|
| Skill Selection Efficiency | used the lowest-pattern skill that covers the need (priority matrix) | telemetry.skill_tier vs task | +FIELD (`skill_tier`) |
| Skill Escalation Rate | light skill → heavier skill escalations | telemetry chain | +FIELD |
| Orchestrator Routing Accuracy | Master routed to the correct pillar orchestrator | telemetry.orchestrator + outcome | +FIELD |
| Handoff Integrity | % subagent calls with complete context (rule #6) | result-envelope completeness | +FIELD/+STREAM |
| Skill Dead-Ref Count | DEAD/RETIRED cross-refs in the skill chain graph (→ 0) | `~/.claude/data/skill_chain_map.md` (already tags DEAD/RETIRED) | PROPOSED 2026-07-06 · source-ready |
| Skill Selector Token Weight | tokens of active-skill descriptions loaded per session | sum of `description:` in `~/.claude/skills/*/SKILL.md` | PROPOSED 2026-07-06 · source-ready |

### Token-routing discipline (cluster B — sharpest token-optimization metrics)
| Metric | Measures | Source | Status |
|--------|----------|--------|--------|
| MCP-vs-CLI Ratio | MCP calls that should have been CLI (MCP ≈ 35× tokens) | telemetry.mcp_or_cli | LIVE |
| Model Selection Adherence | Opus usage % (target < 20%); Sonnet/Haiku mix | telemetry.model | +FIELD (`model`) |
| Context Cliff Events | sessions whose max context exceeded ~140k tokens (absolute cutoff; models here are ~1M-window, so a %-of-window rule never fires) | transcript usage blocks (`aggregate.r_context_cliff_events`) | LIVE (approx) |
| Compaction Events | # /compact + pre-compact extraction compliance | autonomic events (compaction) | +STREAM |
| Cost per Outcome | $ per merged PR / resolved task (not just per task) | telemetry.total_cost + outcome link | +FIELD |

## 🧭 Workflow & Rule Governance (cluster C — cross-pillar; "Self-Governing" under Brush)
| Metric | Measures | Source | Status |
|--------|----------|--------|--------|
| Phase-Gate Compliance | % tasks through Discovery→Plan→Implement with approval | telemetry.phase + approved | +FIELD (`phase`, `approved`) |
| Plan-First Adherence | % 3+ step tasks with a plan artifact before code | telemetry.phase | +FIELD |
| Scope-Drift Incidents | scope changed mid-phase without re-approval | autonomic events (scope_change) | +STREAM |
| Rule Firing Rate | which CLAUDE.md rules fire (and how often) | rule telemetry | +STREAM |
| Rule Violation Rate / Principle_Violations | principle violations | `principle_violations.jsonl` | **LIVE** |
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

### Output Quality — Tool Use (evals.tool_triad; llm-judged, context-only v1)
Three SEPARATE scores (never blended). Data already lives in transcripts; scored by a
classifier-judge on the local tier. `SIMULATED` until the reducer is wired + first run, then
`LLM-JUDGED` (proposed legend token — an llm judgment, never a deterministic `LIVE`).
| Metric | Measures | Source | Status |
|--------|----------|--------|--------|
| Tool_Selection_Accuracy | llm-judge: was an appropriate tool chosen for the task | `bin/tool_quality_scout.py` → `state/tool_quality.json` | LLM-JUDGED |
| Tool_Arg_Correctness | llm-judge: were the tool's arguments well-formed and correct | `bin/tool_quality_scout.py` → `state/tool_quality.json` | LLM-JUDGED |
| Tool_Response_Utilization | llm-judge: did the agent use the tool's result correctly | `bin/tool_quality_scout.py` → `state/tool_quality.json` | LLM-JUDGED |
| Faithfulness_Score | llm-judge (red_team/qwen): output faithful to in-transcript context, not hallucinated; context-bearing turns only | `bin/tool_quality_scout.py` → `state/tool_quality.json` | SIMULATED |
| Refusal_Appropriateness | llm-judge: among detected refusals, was refusing appropriate vs an over-refusal | `bin/tool_quality_scout.py` → `state/tool_quality.json` | SIMULATED |
| Retrieval_Relevance | llm-judge: do top-k Qdrant chunks support a FIXED seed query set (claude_skills benchmark, not live traffic) | `bin/tool_quality_scout.py` (seed `config/retrieval_seed_queries.json`) → `state/tool_quality.json` | SIMULATED |

---

## Instrumentation gaps — what must be added to make these live

The canonical telemetry schema (`agentica_core/telemetry.py`) was harvested from Jarvis and is
**task-level**. To see the agent-operation metrics above, it needs to grow to **orchestration-level**:

**New optional canonical-record fields:**
- `orchestrator` — which orchestrator/skill drove the task (Master/Bow/Sword/Brush/Arts/none)
- `chain_depth` — orchestration fan-out: count of Agent+Task calls per session (NOT nesting depth — true parent→child nesting is structurally unobservable in transcripts)
- `subagent_spawns` + `parent_task` — subagent fan-out and cost attribution (the 7–10× multiplier)
- `knowledge_refs` — count/ids of knowledge/lessons/context surfaced
- ~~`cache_read_tokens` — prompt-cache reuse~~ RESOLVED (AUTO-009): no new canonical-record
  field needed — `_cache_hit_rate` (`aggregate.py`) reads `cache_read_input_tokens` /
  `cache_creation_input_tokens` / `input_tokens` directly off each transcript's real
  `message.usage` block (`~/.claude/projects/**/*.jsonl`).
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
| Principle Violations | `principle_audit.py` → `principle_violations.jsonl` | **LIVE** |
| Canary Health | `security_gate_canary.py` / `behavioral_canary.py` alive? | +SCOUT |

### 🎭 Arts — mine the conversation + quality skills
| Metric | Signal source | Status |
|--------|---------------|--------|
| **Slop_Density** | transcript: AI-slop markers + em-dashes / 1k words (`humanizer`/`ai-slop-cleaner`) | **LIVE** |
| **Frustration_Signals** | transcript: user dissatisfaction turns | **LIVE** |
| **Rework_Loops** | transcript: user correction/redo turns | **LIVE** |
| **Stop_Hook_Loops** | `stop_hook_refires` — assistant self-repetition against a stuck Stop/goal hook; emitter = stop-hook-breaker hook (CP-9, staged) | **DERIVED (0=clean; pending CP-9 emitter for positive counts)** |
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
