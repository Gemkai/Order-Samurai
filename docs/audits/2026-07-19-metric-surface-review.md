# Metric Surface Review — 2026-07-19

Full review of every Order Samurai metric surface: the metric itself, its popup/doc
text, its companion visual, and its remediation command — with a stay / improve /
retire verdict per item. Companion to `docs/plans/metrics-deaggregation-plan.md`
(this review executes its doctrine: per-metric SLO signals, no rollups as targets).

Evidence sources: `agentica_core/insights.py` METRIC_CONFIG (59 metrics),
`Data/wid_payload.json` (74 live envelopes), `dashboard-ui/src/lib/data.ts`
(METRIC_LABELS/METRIC_DOCS, 50 keys), `dashboard-ui/src/lib/metrics.ts` (viz
assignment), `state/exec_log.jsonl` (157 runs), `state/skill_efficacy.json`,
`state/reflex_engine_state.json`, `agentica_core/remediation.py`.

---

## Part A — Section verdicts

### A1. Remediation Efficacy panel — **IMPROVE (the panel is right; its data feed is broken)**

The user-visible symptom is real: **no `actor:"reflex"` event since 2026-06-08**
(the system's first two days), while the engine itself fired as recently as
2026-07-19T12:13Z. The engine is NOT dead — 139 of 157 exec_log entries are
`source=reflex_engine`, launchd keeps `com.agentica.order-samurai-api` alive, and
`REFLEX_REQUIRE_GRANT=true` only restricts the 9 DRY-RUN-GRADED metrics.

Why the panel shows nothing:

1. **`remediation.py:_skill_uses()` only counts `status:"done"` rows.** Zero reflex
   runs have completed `done` since 2026-07-13 (last: `runtime-refactor-hardening`
   13:35Z). The 49 runs since ended `no_change` (44), `error` (3), `timeout` (1) —
   all invisible to the panel by construction.
2. **Even a `done` run must land between two `metrics_history.jsonl` snapshots**
   (health<40 before, any value after). That file has ~20 rows over 4.5 months —
   effectively weekly-to-monthly density. The intersection "done AND bracketed by
   snapshots" has not occurred since 2026-06-08.
3. Compounding: `skill_efficacy.json` shows the engine's favorite APPLY-tier skills
   mostly never improve their metric (consolidate-memory 0/12, humanizer 0/6,
   token-optimizer 0/4, pip-safe-upgrade 0/4) — so even fixing (1)+(2) would
   surface a lot of honest "flat".

**Recommendation (in order):**
- Capture before/after AT FIRE TIME in the engine (remeasure_gate already
  re-measures the metric live before spawning — record that value and re-measure
  after completion), instead of correlating against sparse global snapshots.
- Count `no_change`/`error`/`timeout` runs as *attempts* in the panel (attempted /
  completed / improved), so "the engine tried 49 times this week and improved
  nothing" is visible instead of silence — that IS the finding that matters.
- Demote chronically-0% skills to advisory (see Part C).

### A2. Metric popups (MetricModal, `METRIC_DOCS`) — **IMPROVE**

The what/fix copy that exists is good and correctly wired (modal + Scoring Rubric
both read it). But coverage has drifted badly:

- **22 live payload metrics have NO popup doc** (fall back to a bare label):
  Archive_Ratio, Cache_Hit_Rate, Config_Drift_Rate, Context_Cliff_Events,
  Cost_Per_Outcome, Faithfulness_Score, Index_Drift, Knowledge_Prompted,
  Knowledge_Staleness_Days, Lesson_Graduation_Rate, MCP_vs_CLI_Ratio,
  Mechanism_Liveness, OKF_Conformance, Orphan_Concepts, Refusal_Appropriateness,
  Retrieval_Relevance, Self_Correction_Rate, Skill_Routing_Adherence,
  Stop_Hook_Loops, Tool_Arg_Correctness, Tool_Response_Utilization,
  Tool_Selection_Accuracy. Several are currently WARN/FAIL (Faithfulness_Score,
  Retrieval_Relevance, Context_Cliff_Events) — the metrics most in need of an
  explanation are the ones without one.
- **4 documented keys no longer exist in the payload** (dead doc entries):
  Loop_Breaker_Fires, Secret_Scrubs, Skills_Optimized, Skill_Promotions.

### A3. Companion visuals (MetricViz / assignViz) — **IMPROVE (bug fixed 2026-07-19)**

The scoring/diversity system (`vizScores`/`assignViz`) was **entirely dead in the
running UI**: `assignViz` stored keys as `group + key` (no separator) while
PillarPage looked up `` `${group}::${key}` `` — every lookup missed and every
ungraded metric rendered the fallback line sparkline. **Fixed** (metrics.ts now
stores `group::key`; 25 UI tests pass). Remaining improvements:
- `sankey` can never render on PillarPage (no `slices` prop is passed) — either
  wire slices for the mix/distribution metrics or drop the kind from scoring.
- Graded metrics correctly bypass this system for `ThresholdSparkline` (trend +
  warn/fail lines) — that split is right; **stay**.

### A4. Reflex deck / remediation buttons — **STAY**

The four-kind gating (auto_fix / advisory / session_hygiene / mis_route) is
consistent at all three call sites (PillarPage, MetricModal, ReflexList), with
honest labels ("diagnostic", "no auto-fix", "run in your session ↗"). No changes.
One nit: the gating logic is triplicated — a shared helper would prevent drift.

### A5. Needs Attention — **STAY**

The one legitimate composite (a decomposable breach count, never a target).
Matches the de-aggregation doctrine's §9 exactly.

### A6. Reports section — **REVAMPED (this change, 2026-07-19)**

- Pillar-score trend SVG and score radar **retired** (aggregate scores).
- Every report now: plain-English weekly blurb (layman-summary style: headline,
  outcomes with real reducer numbers, ≤4 standouts) + per-metric tables with
  `This week | Weekly avg ± σ | vs usual` (>1σ flagged; volume metrics read
  busier/quieter, quality metrics better/worse).
- All 31 historical reports regenerated in the new format and mirrored to the
  dashboard public/dist dirs.
- Sword remains a labeled point-in-time snapshot until weekly security history
  exists (candidate improvement: persist weekly sword snapshots the way
  metrics_history persists graded values).
- The Reports tab's hardcoded "Composite & Headline Metrics" prose still
  advertises composites — trim to match the de-aggregation doctrine.

### A7. Scoring Rubric tab — **STAY**

Reads effective (post-calibration) rules straight from the payload — the drift
that motivated it is real. With reports de-aggregated it is the single place
weights still appear; fine, weights are sort/rollup hints, not displayed grades.

---

## Part B — Per-metric verdicts

Legend: **S** stay · **I** improve (note says what) · **R** retire.
"doc" = has METRIC_DOCS popup text. Status from live payload 2026-07-19.

### Bow — Operations

| Metric | Status | Kind | Doc | Verdict | Note |
|---|---|---|---|---|---|
| Error_Rate | SIMULATED | advisory (DRY-RUN-GRADED) | ✓ | **S** | Simulated by design (min-sample + wired-channel guard) — honest, keep |
| Latency_P95 | OK | advisory | ✓ | **S** | Correctly demoted to advisory (a skill can't move infra latency) |
| Complexity_Weighted_Throughput | live | advisory | ✓ | **S** | Volume signal; no threshold, correct |
| Tool_Calls | live | advisory | ✓ | **S** | Dedup fix already in |
| Session_Count | live | — | ✓ | **S** | Informational |
| Avg_Session_Turns | needs:human | advisory | ✓ | **I** | Stuck 4× via /insights (reflex_engine_state noImprovement) — the metric is fine; unmap the futile auto-path, keep advisory |
| Estimated_Agent_Time_Saved | live | — | ✓ | **S** | Calibration-gated hero; honesty labels already in place |
| MCP_Smoke_Fails | OK | auto_fix | ✓ | **S** | |
| Self_Correction_Rate | live | — | ✗ | **I** | Add doc text |
| Mechanism_Liveness | live (50) | — | ✗ | **I** | Add doc text; 50% deserves a threshold once baselined |
| Agent_Process_Count | live | mis_route | ✓ | **S** | Correctly quarantined (self-heal DO-NOT-USE); purely informational |
| Mechanism_Orphans | FAIL (5) | advisory | ✓ | **S** | Human-work metric, correctly advisory; act on it via /audit-mechanisms |
| Lesson_Graduation_Rate | live | — | ✗ | **I** | Add doc text |
| Governance_Pass_Rate | OK | auto_fix | ✓ | **S** | |
| Config_Drift_Rate | live (5817) | — | ✗ | **I** | Number is meaningless to a reader without doc + unit; document or fold into doctor |
| Loop_Breaker_Fires | **absent from payload** | (config: graded, weight 2) | ✓ | **R** | Dark graded metric — no envelope is ever built. Per "removal, never faking": retire from METRIC_CONFIG + UI docs, or wire the emitter |

### Sword — Security

| Metric | Status | Kind | Doc | Verdict | Note |
|---|---|---|---|---|---|
| Rule_Violations | OK | advisory (DRY-RUN-GRADED) | ✓ | **I** | Popup should state it's judged per-session (966 raw reads alarming; 0.5/session passes) |
| Skill_Routing_Adherence | SIMULATED | advisory | ✗ | **I** | Add doc text; scout not yet feeding it |
| Governance_Review_Findings | OK | auto_fix | ✓ | **S** | Bootstrap self-primer in refresh keeps it live |
| Kill_Chains_Disrupted | live | — | ✓ | **S** | Protective activity |
| Kill_Chains_Open | OK | mis_route | ✓ | **S** | Correctly advisory |
| Pending_Chain_Proposals | live | — | ✗ | **I** | Add doc text |
| Boundary_Violations | OK | mis_route | ✓ | **S** | Real fix (quarantine bin) still unbuilt — tracked in config comments |
| Guardrail_Blocks | SIMULATED | — | ✓ | **I** | Protective counter currently dark — check the emitter; if permanently dead, retire |
| Secrets_Detected | OK | auto_fix (DRY-RUN-GRADED) | ✓ | **S** | Weight-3, deterministic mechanism — model metric |
| Gate_Canary_Fault | live (0) | advisory | ✓ | **S** | Diagnosis skill + deterministic detector — good pattern |
| Open_CVEs | OK | auto_fix | ✓ | **S** | Honest replacement for retired Vulnerability_MTTR |
| Deprecated_Deps | OK (20) | auto_fix | ✓ | **S** | pip-safe-upgrade is causal — but see Part C (0/4 recent efficacy) |
| Secret_Scrubs | **absent from payload** | — | ✓ | **R** | Dead UI doc key; retire or rewire emitter |

### Brush — Architecture & Cost

| Metric | Status | Kind | Doc | Verdict | Note |
|---|---|---|---|---|---|
| Total_Cost | live | advisory | ✓ | **S** | |
| Token_Spend | live | auto_fix (/token-optimizer) | ✓ | **I** | token-optimizer is 0/4 lifetime — demote mapping to advisory |
| Cost_Per_Task | live | advisory | ✓ | **S** | |
| Cost_Per_Outcome | live | — | ✗ | **I** | Add doc text |
| Token_Execution_Density | OK | advisory | ✓ | **S** | Retuned ceiling honest |
| Local_Routing_Share | OK (31%) | mis_route | ✓ | **S** | Correctly quarantined (router config is the lever) |
| Cache_Hit_Rate | live (96.1) | — | ✗ | **I** | Add doc text; good candidate for a graded floor |
| Context_Cliff_Events | FAIL (18) | advisory | ✓* | **I** | *No popup doc despite FAIL — add; consider mapping to /handoff-based mitigation guidance |
| Estimated_Cost_Savings | live (0.0) | — | ✓ | **I** | Permanent data_gap until routing capture lands (refresh_dashboard deliberately won't fabricate). Wire routing capture or drop from hero rotation |
| Revision_Ratio | live (0.0) | auto_fix | ✓ | **S** | |
| Hardcoded_Path_Incidents | OK | auto_fix (/doctor) | ✓ | **S** | |
| Root_Hygiene_Issues | OK | auto_fix (/doctor) | ✓ | **S** | |
| Subagent_Efficiency_Index | FAIL (5.0) | advisory | ✓ | **I** | Known benchmark design bug (whole-session vs 3× solo median) — grade D documented. Fix the benchmark to marginal spawn cost, or stop grading it until then (a permanently-FAIL metric nobody believes trains alarm blindness) |
| MCP_vs_CLI_Ratio | live | — | ✗ | **I** | Add doc text |
| Chain_Depth_Avg | OK | auto_fix (DRY-RUN-GRADED) | ✓ | **S** | Retuned as early-warning, not anti-orchestration |
| Architecture_Scorecard_Grade | OK (100) | auto_fix | ✓ | **S** | |

### Arts — Craft & UX

| Metric | Status | Kind | Doc | Verdict | Note |
|---|---|---|---|---|---|
| Slop_Density | OK (0.09) | auto_fix (DRY-RUN-GRADED) | ✓ | **S** | |
| Tool_Selection_Accuracy | WARN (60) | advisory | ✗ | **I** | New llm-judged triad — add doc text explaining the judge + sample size |
| Tool_Arg_Correctness | OK | advisory | ✗ | **I** | Add doc text |
| Tool_Response_Utilization | SIMULATED | advisory | ✗ | **I** | Add doc text; scout not yet emitting this dimension |
| Faithfulness_Score | FAIL (40) | advisory | ✗ | **I** | Weight-3 FAIL with no popup explanation — highest-priority doc gap; verify judge sample size before trusting the 40 |
| Refusal_Appropriateness | OK | advisory | ✗ | **I** | Add doc text |
| Retrieval_Relevance | WARN (53) | advisory | ✗ | **I** | Add doc text |
| Frustration_Signals | OK | advisory | ✓ | **S** | Per-session judged |
| Rework_Loops | OK | advisory | ✓ | **S** | |
| Stop_Hook_Loops | OK (5) | advisory | ✗ | **I** | Add doc text (it's the CP-9 control's telemetry — worth explaining) |
| Simplify_Age | OK (0.4d) | auto_fix | ✓ | **S** | |
| Doc_Parity_Issues | WARN (17) | auto_fix (/wiki) | ✓ | **S** | Ratchet policy documented; /wiki is 10/20 — one of the few skills that works |
| Skill_Conflicts | OK | auto_fix (DRY-RUN-GRADED) | ✓ | **S** | |
| Skills_Optimized / Skill_Promotions | **absent from payload** | — | ✓ | **R** | Dead UI doc keys + config entries with no envelope — retire or rewire |
| Wiki_Health_Score | live (0) | auto_fix | ✓ | **I** | A constant 0 "health score" is either a broken source or a meaningless scale — diagnose; retire if the source is dead |
| Wiki_Article_Count | live | — | ✓ | **S** | Informational |
| Raw_Pending | OK | auto_fix (DRY-RUN-GRADED) | ✓ | **S** | Threshold ratified vs librarian nightly |
| Wiki_Orphans | needs:human | auto_fix (DRY-RUN-GRADED) | ✓ | **S** | Correctly stuck-flagged (4×/0 improved) — surfaced for human review as designed |
| OKF_Conformance | OK | advisory | ✗ | **I** | Add doc text |
| Orphan_Concepts | live (120) | — | ✗ | **I** | Still baselining — add doc text; threshold once history accumulates |
| Archive_Ratio | OK | auto_fix | ✗ | **I** | Add doc text; consolidate-memory is 0/12 — demote mapping to advisory |
| Index_Drift | OK | auto_fix | ✗ | **I** | Add doc text (its command genuinely fixes drift — worth saying) |
| Knowledge_Staleness_Days | OK | auto_fix | ✗ | **I** | Add doc text; consolidate-memory 0/12 → demote mapping |
| Knowledge_Prompted | live | — | ✗ | **I** | Add doc text |
| Craft_Improvements / Estimated_Human_Time_Saved | live | — | ✓ | **S** | Hero pair, honesty-gated |
| Instrumentation_Coverage | live | — | ✓ | **S** | The meta-metric that catches dark-metric drift — keep prominent |

---

## Part C — Remediation command efficacy (cross-cutting)

Lifetime per-skill improvement rates (`state/skill_efficacy.json`):

| Works (keep auto_fix) | Rate | Doesn't (demote to advisory) | Rate |
|---|---|---|---|
| audit-mechanisms | 6/8 | consolidate-memory | 0/12 |
| codebase-cleanup-deps-audit | 10/15 | humanizer | 0/6 |
| simplify | 13/20 | token-optimizer | 0/4 |
| wiki | 10/20 | pip-safe-upgrade | 0/4 |
| runtime-refactor-hardening | 1/2 | subagent-audit, mcp-setup, investigate, doctor, skill-consolidator | 0/x |

The engine's own cooldown logic already reads this file; the config-level fix is to
flip `auto_remediable: False` on the metrics mapped to chronic-0% skills so the
engine stops burning spawns on them (Token_Spend, Archive_Ratio,
Knowledge_Staleness_Days; watch Deprecated_Deps/pip-safe-upgrade — its 0/4 may be
the CVE-quiet period rather than skill failure).

## Part D — Changes applied in this review (2026-07-19)

1. **Reports revamp** — `agentica_core/weekly_report.py` rewritten (no aggregate
   scores; per-metric avg ± σ tables; layman blurb). All 31 historical reports
   regenerated + mirrored. Tests rewritten (20 pass).
2. **Viz assignment bug fixed** — `dashboard-ui/src/lib/metrics.ts` key format
   aligned with PillarPage lookup (25 UI tests pass).
3. **Remediation Efficacy visibility fix (A1) applied** — the engine now records
   `metric_before`/`metric_after` on every autonomous exec_log row (verify-gate's
   live re-measure when it ran, else the triggering snapshot; post-run: refreshed
   payload for `done`, live re-measure otherwise — `bin/remeasure_gate.py` now
   emits the metric's live `value`). `remediation.py` builds efficacy events
   directly from those fire-time values (no snapshot bracketing needed) and counts
   every run — `no_change`/`error`/`timeout` included — as `attempted`/`completed`
   (payload keys extended, none renamed). RemediationPanel shows
   Attempted/Completed alongside Applied/Improved. Tests: 805 OS + 595 core
   pytest, 92 api + 43 UI vitest — all pass. Engine restarted onto the new code
   2026-07-19 ~15:46Z; historical baseline (read-only, no backfill):
   `docs/audits/2026-07-19-remediation-backtest.md`.

## Part E — Recommended follow-ups

1. ~~Remediation Efficacy visibility fix (A1)~~ — applied, see Part D §3.
2. METRIC_DOCS: add the 22 missing popup entries; delete the 4 dead keys —
   in progress in a separate session (the dead-key retirements already landed).
3. ~~Retire the 4 dark metrics~~ — done, see Part F §2.
4. ~~Demote chronic-0% skill mappings to advisory~~ — done, see Part F §4.
5. ~~Subagent_Efficiency_Index: ungrade~~ — done, see Part F §5 (re-grade only
   with a marginal-spawn-cost benchmark).
6. ~~Persist weekly Sword snapshots~~ — done, see Part F §6.
7. ~~Trim composite prose; drop sankey~~ — done, see Part F §7.

## Part F — Goal-sweep changes (2026-07-19, second pass)

1. **Governance_Work_Volume implemented** (metrics backlog P1) —
   `compute_work_volume()` in `execution/skill_routing_adherence.py` (30d window,
   same per-pair counting unit as the adherence denominator), reducer + REGISTRY
   row (sword/Governance, count, direction-neutral) + informational METRIC_CONFIG
   entry. SIMULATED until the router hook logs its first detection.
2. **4 dark metrics fully retired** — METRIC_CONFIG entries (companion session)
   and the dormant scout-injection sites in `aggregate.py` (this session) both
   removed, so a resurrected emitter cannot re-add an unregistered metric.
3. **Wiki_Health_Score un-saturated** — `compute_score` in
   `Knowledge/vault/_scripts/vault_health.py` now CAPS each penalty class
   (uncapped orphan penalties had pinned the score at 0 for months; 58 orphans =
   −232). Score restored from 0 to a meaningful 38/100 with gradient.
4. **Chronic-0% mappings demoted to advisory** — Token_Spend (token-optimizer
   0/4), Archive_Ratio + Knowledge_Staleness_Days (consolidate-memory 0/12) now
   `auto_remediable: False`; the reflex engine skips them via
   `non_remediable_metrics.json` (verified regenerated).
5. **Subagent_Efficiency_Index ungraded** — dir/warn/fail/weight removed (value
   still shown; dry-run mechanism stays as the manual drill-down); removed from
   `_GRADED_METRIC_PILLARS`. Its permanent constructed FAIL no longer trains
   alarm blindness.
6. **Weekly Sword history** — `record_sword_snapshot()` appends per-platform
   sword values to `Data/telemetry/sword_history.jsonl` on snapshot refreshes;
   `_sword_section` renders that week's captured values (last row per
   week+platform wins) with the point-in-time fallback only for pre-history weeks.
7. **UI cleanups** — Reports-tab composite prose corrected (Security Posture row
   removed — metric retired 2026-07-11; Vault Health relabeled informational with
   capped-penalty formula; Subagent ROI relabeled advisory/ungraded); sankey
   removed from `vizScores` (unreachable on PillarPage — no `slices` source);
   remediation-kind gating/tooltip copy deduplicated into
   `src/lib/remediation-kind.ts`, consumed by PillarPage, MetricModal, ReflexList.
8. **Estimated_Cost_Savings "routing capture" follow-up CLOSED as obsolete** —
   the Part B note relied on a stale refresh_dashboard docstring. The reducer was
   redesigned to measured cost-per-task savings only (routing-events × $0.05
   component removed as unfalsifiable) and is live-calibrated today
   (val 0.0, calibrated=true, no data_gap — an honest measured zero). The stale
   docstring reference is fixed; no engine routing capture is needed.
9. **Sensei proposals triaged** — AUTO-020 fan-out / AUTO-022 / AUTO-023 marked
   `blocked_on_emitter` (no fan-out, routing-accuracy, or acceptance field exists
   in telemetry — never fake from proxies); AUTO-021 marked `deferred_low_value`
   with a concrete implementation path (skills-lock.json token-weight scout).

Verification: 594 agentica_core + 805 Order Samurai pytest, 43 dashboard-ui
vitest, `tsc --noEmit` clean; dashboard dist rebuilt; payload refreshed —
Governance_Work_Volume present (SIMULATED), Subagent_Efficiency_Index ungraded
advisory, Wiki_Health_Score 38, Loop_Breaker_Fires absent, demotions in
`non_remediable_metrics.json`.
