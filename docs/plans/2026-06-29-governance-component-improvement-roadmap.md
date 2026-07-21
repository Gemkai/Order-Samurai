# Plan — 2026-06-29 Governance Component Improvement Roadmap

Phase-2 artifact. Lifts every rated component of the Agentica OS governance architecture.
Builds on shipped work (#8 dispatch, #9 gate-green, #10 Ollama, #12 drift-gate, #13 bushido
fail-closed, #14 typed envelope + live-source honesty). **No coding until approved & prioritized.**
Each workstream is an independent, revertable PR gated on `doctor.py` FAIL=0.

## Goal
Raise as-delivered execution from ~7 to ~8.5 by (a) plugging in the built-but-unwired
determinism layer, (b) rebuilding the weak connective tissue (run-trace, calibration), and
(c) hardening the loops' responsiveness, robustness, and lifecycle — without merging the
three control planes.

## Component → workstream → target rating (coverage matrix)
| Component | Now | Target | Workstream |
|---|---|---|---|
| Mechanism library | 5.5 | 8 | **W1** |
| Determinism (delivered) | 4 | 8 | **W1** |
| ReflexEngine | 8 | 8.5 | W1 + W4 |
| Observability / run-trace | 5 | 8 | **W2** |
| Calibration | 5 | 8 | **W3** |
| Sensei-cycle | 8 | 9 | W4 + W5 |
| Rival | 9 | 9.5 | **W5** |
| Bushido gate | 7.5 | 8.5 | **W6** |
| State / backlog lifecycle | 6.5 | 8 | **W7** |
| Sensei (orchestrator) | 7.5 | 8.5 | W4 + W7 |
| Dojo | 6.5 | 8 | **W8** + W3 |
| Pillar ronins | 7.5 | 8.5 | W8 + W1 |
| Scouts | 7.5 | 8.5 | W1 + W4 |
| Metrics pipeline | 7 | 8.5 | **W9** |
| Typed envelope | 7.5 | 8.5 | W9 |
| Path authority | 6.5 | 8 | **W10** |
| doctor.py + verifiers | 8 | 9 | W2 + W3 + W6 |

---

## W1 — Wire the determinism layer (the #1 lever)
**Lifts:** mechanism library 5.5→8, determinism 4→8, ReflexEngine →8.5.
Execute the already-verified `RONIN-MECHANISM-ROUTE-PLAN.md §5`, Tier-1 first, then Tier-2.
- **Files:** `agentica_core/insights.py` (add `mechanism` block to Tier-1 METRIC_CONFIG entries),
  `api/src/reflex-engine.ts` (mechanism spawn branch w/ explicit timeout + `kind` on exec_log +
  fallback to LLM skill on non-zero exit), `bin/emit_event.py` (`--kind`/`--routing-efficient`).
- **Steps** (each → verify):
  1. Tier-1 only (`codebase_deps_audit`, `policy_enforcement_audit`, `subagent_audit`,
     `canary_fault_detect`). verify: dry-run one cycle — mechanism runs, exec_log row `kind:"mechanism"`.
  2. Force a non-zero mechanism exit → confirm fallback to the LLM skill, row `fallback_from:"mechanism"`.
  3. `doctor.py` + `aggregate.py` → no new WARN; server rebuilt/restarted.
  4. Tier-2 (`pip_safe_upgrade` plan-only, `skill_consolidator` detect-only) once Tier-1 proven.
- **Risk/rollback:** feature-flag the mechanism branch; revert insights.py keys. Read-only mechanisms only.

## W2 — Unified run-trace (rebuild the weakest layer)
**Lifts:** observability 5→8, doctor.py →9.
A metric's life spans `autonomic_events.jsonl → wid_payload.json → SENSEI_LEDGER.jsonl →
exec_log.jsonl → ronin_logs.md`. Make it traceable.
- **Files:** `telemetry.py`, `aggregate.py`, sensei-cycle writers, `reflex-engine.ts` (exec_log),
  new `bin/trace.py`, new `execution/verify_trace_keys.py`.
- **Steps:**
  1. Ensure every writer stamps `(cycle_id, reflex_id, metric)` (some already do). verify: each ledger row carries the triple.
  2. `bin/trace <metric>` joins the 5 sources into one chronological lifecycle view. verify: trace a known metric end-to-end.
  3. doctor check: rows missing the trace triple → WARN. verify: planted untagged row warns.
- **Risk/rollback:** additive fields + a read-only reader; revert is clean.

## W3 — Calibration forward-capture (stop the silent data loss)
**Lifts:** calibration 5→8, dojo →(supports 8), doctor.py →9.
10 `started_at` samples were permanently lost; the timestamp-backstop patches a forward-capture
gap. Make the timestamp non-null *by construction*.
- **Files:** `bin/stamp_dojo_timestamps.py`, dojo dispatch path, `aggregate.py` calibration reducer,
  `execution/doctor.py` (promote `dojo-timestamps.lost-samples` WARN→FAIL once forward-capture is guaranteed),
  re-examine the removed cost-savings comp2 (`_estimated_cost_savings`).
- **Steps:**
  1. Set `started_at` in the same atomic write that flips an item to `doing`; reject any item reaching
     `done` without it. verify: an item can't reach done without started_at (unit test).
  2. Backstop becomes belt-and-suspenders, not the primary capture. verify: backstop run is a no-op on a clean cycle.
  3. Decide on comp2 (routing-efficiency → a COUNT, not cost-savings dollars). verify: calibration coefficients accumulate from real pairs.
- **Risk/rollback:** the FAIL promotion is the only behavior change — guard behind a clean baseline first.

## W4 — Event-triggered verification for CRITICAL (responsiveness)
**Lifts:** sensei-cycle 8→9, Sensei →8.5, ReflexEngine →8.5, scouts →8.5.
The 6h sweep is right for the broad audit, but a new CRITICAL outlier shouldn't wait up to 6h for
verification. Tier the cadence; trigger targeted verification on demand.
- **Files:** `api/src/reflex-engine.ts` (emit a `needs_verification` signal when a CRITICAL reflex first
  appears or fires N× consecutively), a lightweight trigger that invokes a **single-reflex** sensei
  verification, `sensei-cycle/SKILL.md` (accept a single-reflex mode), `register_sensei_task.ps1` (keep 6h sweep).
- **Steps:**
  1. Add single-reflex verification mode (one scout + one rival, scoped). verify: runs in minutes, posts one verdict.
  2. Engine emits the trigger on new-CRITICAL / N-consecutive-fires (cost-gated, debounced). verify: a forced CRITICAL triggers exactly one verification.
  3. Tier: CRITICAL→event, HIGH→6h, rest→daily. verify: budget_ledger spend stays bounded.
- **Risk/rollback:** debounce + budget guard prevent storms; the 6h sweep is untouched as the floor.

## W5 — Multi-vote rival for CRITICAL (adversarial robustness)
**Lifts:** rival 9→9.5, sensei-cycle →9 (security).
Single-vote verification can miss; for CRITICAL/security only, use a perspective-diverse panel.
- **Files:** `sensei-cycle/SKILL.md` (CRITICAL tier → spawn N rivals with distinct lenses —
  correctness / security / reproducibility — majority decides), `rival` agent (accept a lens param).
- **Steps:** majority-of-3 for CRITICAL; single-vote unchanged for HIGH/below (cost-gated). verify: a CRITICAL finding requires ≥2 of 3 to confirm/refute; cost only rises for CRITICAL.
- **Risk/rollback:** scoped to CRITICAL; revert is a config flip.

## W6 — Bushido hardening
**Lifts:** bushido 7.5→8.5, doctor.py →9.
Two gaps: the **import-failure path still fails open** (it exits 3 before the skill is classified), and
`skill_tiers.json` is hand-maintained (drift risk).
- **Files:** `bin/bushido_check.py` (defer the engine import / parse args first so the import-failure path
  can also classify and fail closed for sensitive skills), a generator/validator for `skill_tiers.json`,
  `execution/verify_skill_tiers.py` (every reflex-mapped skill is tier-classified).
- **Steps:** (1) restructure so both error paths classify; (2) doctor check for tier coverage. verify: a forced import error on a sensitive skill returns HARD_STOP; an unclassified reflex-mapped skill FAILs doctor.
- **Risk/rollback:** import restructure is the sensitive bit — keep `from __future__ import annotations` so type hints survive.

## W7 — State/backlog lifecycle + the oversight→build feedback edge (cross-pollination keystone)
**Lifts:** state/backlog 6.5→8, Sensei →8.5.
- **Files:** `bin/ronin` (add `decline`), `bin/replenish_backlog.py` (auto-age stale proposals,
  `source` tag), `sensei-cycle/SKILL.md` (a REFUTED/structural verdict files a dojo "re-instrument
  `<metric>`" item into the shared `PROPOSED_BACKLOG.json`), `PROPOSED_BACKLOG.json` schema.
- **Steps:** (1) `ronin decline <id>` + auto-age; (2) **close the feedback edge** — oversight defects
  become build tasks. verify: a forced REFUTED verdict files a tagged, unapproved dojo backlog item;
  `ronin decline` removes a proposal with an audit note.
- **Risk/rollback:** human gate (`ronin promote`) preserved; feedback items are unapproved by default.

## W8 — Dojo reactivation + rival-audited commits
**Lifts:** dojo 6.5→8, pillar ronins →8.5.
The dojo is dormant. Once W1 (mechanisms) + W3 (calibration) land, reactivate with the hardened
contract and add adversarial verification of ronin commits.
- **Files:** `prompts/dojo_cycle.md`, `sensei.md`, `prompts/ronin_*.md`, the rival-for-dojo integration
  (Sensei runs `rival` post-audit on a ronin commit **before** cherry-pick).
- **Steps:** (1) reuse `rival` to audit a write-ronin's commit pre-cherry-pick; (2) flip `ronin_mode`
  back on per-pillar after a clean dry cycle. verify: a commit whose `improved` claim isn't backed by the diff is refused; a full dry cycle is green.
- **Risk/rollback:** reactivate one pillar at a time; cherry-pick gate + rival audit bound the blast radius.

## W9 — Metrics-kernel de-drift + full envelope coverage
**Lifts:** metrics pipeline 7→8.5, typed envelope →8.5.
Resolve the two-kernel duplication (frozen repo-local vs Governance `agentica_core`) the kernel-drift
test only *detects*; extend the #14 schema to cover every envelope field.
- **Files:** both `aggregate.py` copies, `tests/test_kernel_drift.py`, `schema/wid_payload.schema.json`.
- **Steps:** (1) plan + execute the deferred kernel merge (single source); (2) schema-cover all fields
  incl. the W1 `mechanism`/`kind` additions. verify: one kernel, drift test retired or trivially green; malformed-field payloads fail both sides.
- **Risk/rollback:** the merge is the riskiest item — do it last, behind the full test suite.

## W10 — Path-authority completion
**Lifts:** path authority 6.5→8, doctor.py →9.
- **Files:** `execution/runtime_paths.py` (single source for ALL artifact paths + the Ollama endpoint),
  config consumers (make `agentica_surface_matrix.json` targetRoot env-derived, not a hardcoded abs path),
  `execution/verify_no_stale_paths.py` (broaden surfaces), the global `~/.claude/CLAUDE.md`
  LM-Studio→Ollama fix **(separate explicit approval — affects every project)**.
- **Steps:** (1) centralize endpoints/roots in runtime_paths; (2) broaden the drift-gate. verify: no consumer hardcodes a root/endpoint; drift-gate covers the broadened surface.
- **Risk/rollback:** env-derived roots need each consumer updated in lockstep — one PR.

---

## Sequencing (each phase independently shippable, gated on doctor FAIL=0)
- **Phase 1 (highest leverage):** W1 determinism.
- **Phase 2 (connective tissue):** W2 run-trace + W3 calibration — they make everything else debuggable & trustworthy.
- **Phase 3 (oversight):** W4 event-trigger + W5 multi-vote.
- **Phase 4 (gate & lifecycle):** W6 bushido + W7 backlog/feedback + W10 path-authority.
- **Phase 5 (reactivation & cleanup):** W8 dojo + W9 kernel de-drift.

## Cross-cutting constraints
- Work in a `git worktree` off `origin/main` (core.longpaths already true); worktree isolation means
  no need to quiesce the daemon — only disable `OrderSamurai-SenseiCycle` if editing the live tree.
- `doctor.py` FAIL=0 before every PR; the 2 standing WARNs are known/out-of-scope until W3 closes one.
- One logical change per PR; never `git add -A` in the live tree; do NOT `--delete-branch` on a PR
  another open PR is stacked on (it closes the dependent). Cross-language (Python+TS) changes ship together.
- PowerShell has no heredoc — use `git commit -F <file>` / `gh pr create --body-file <file>`.

## Out of scope
- Merging the three control planes (privilege isolation is intentional).
- Adding an LLM planning stage (use code-as-mechanism — W1).
- Autonomous `pip_safe_upgrade --apply` (stays human-gated).

## Decisions (recorded 2026-06-29)
1. **Status:** plan APPROVED; implementation **deferred until the user says "go"**. When started,
   begin with **Phase 1 / W1** (determinism) unless re-directed.
2. **Determinism scope (W1):** **Tier-1 only first** (the 4 read-only detect mechanisms) — prove the
   route end-to-end, then Tier-2 as a separate pass.
3. **Dojo reactivation (W8):** **sword (security) pillar as a canary first** — watch a few cycles behind
   the hardened contract + rival-audited commits, then expand to the other pillars.
4. **Global `~/.claude/CLAUDE.md` (W10):** still open — handle as a **separate explicit approval**
   (it affects every project, not just this repo).
