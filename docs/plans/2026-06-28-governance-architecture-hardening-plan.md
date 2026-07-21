# Plan — 2026-06-28 Governance Architecture Hardening

Phase-2 artifact. Implements the architecture recommendations from the 2026-06-28 review
session (follows PR #8 — ronin/sensei dispatch hardening). **No coding until this plan is
approved.** Each phase is an independent, revertable PR gated on `doctor.py` green.

## Goal
Turn three loosely-coupled governance loops (dojo build · ReflexEngine run · sensei-cycle
oversight) into one self-improving system with a trustworthy gate, a typed contract, a
shared deterministic-mechanism kernel, and a closed oversight→build feedback edge — without
merging the loops or weakening their privilege isolation.

## Cross-cutting constraints (apply to every phase)
1. **Quiesce first.** A background daemon writes this repo (the tree shifted mid-session).
   Before any phase: stop the scheduled sensei task + reflex-engine writers, OR work entirely
   in a `git worktree` off `origin/main`. verify: `git status --short` stable for 60s.
2. **Gate discipline.** `doctor.py` is the authoritative gate and is currently RED. Phase 1
   makes it green; **no later phase merges until `doctor.py` FAIL=0**.
3. **One logical change per PR**, `chore/`|`fix/`|`feat/` branch off `origin/main`, never
   `git add -A` in the live tree (stage explicit paths), don't bundle unrelated WIP.
4. **Cross-language parity.** Python builder + TS executor changes that share a contract ship
   in the same PR (P3, P4) so the seam is never half-migrated.
5. **New mechanism = 3 steps** (Mechanism Rule): write + register + verify-consumed. Every new
   verifier/mechanism must pass `/audit-mechanisms` before its phase is "done".

---

## Phase 0 — Safety baseline (gates everything)
- **Goal**: a clean, reproducible starting point that won't collide with the daemon.
- **Steps**
  1. Stop scheduled sensei task + reflex-engine; confirm no writers. verify: `git status` stable 60s.
  2. Record the doctor baseline. verify: capture `python execution/doctor.py` → `OK=12 WARN=2 FAIL=1` (or current).
  3. Cut working worktree off `origin/main` (longpaths already enabled). verify: target files present.
- **Out of scope**: any source edit.

---

## Phase 1 — Trustworthy gate + drift lockout  *(URGENT — detailed)*
One PR: `fix/governance-gate-green`. Combines P1 + P2 + P7 (all gate/safety, no behaviour change).

### 1a. Get `doctor.py` GREEN (P1)
- **Files**: `agentica_core/reflexes.py`, `agentica_core/ronin_metrics.py`, `execution/runtime_paths.py` (authority, read-only ref), the path-authority-scan verifier (likely `execution/verify_runtime_contract.py` or `score_architecture.py`).
- **Steps**
  1. Read the path-authority-scan verifier to learn exactly which path patterns it forbids. verify: state the rule in one line.
  2. Grep the two offenders for the flagged literals. verify: list each offending line.
  3. Replace each literal with the canonical `runtime_paths.py` accessor (no new constants — reuse the authority). verify: `doctor.py` `path-authority-scan` → OK.
  4. Full run. verify: `python execution/doctor.py` → **FAIL=0** (WARNs may remain).

### 1b. Drift-gate verifier (P2)
- **Files (new)**: `execution/verify_no_stale_paths.py`; **register** in `execution/doctor.py`, `config/architecture_scorecard.json`, `config/anti_drift_policy.json` (existing doctor-wiring pattern). Config: forbidden patterns (`Desktop\\`, `localhost:1234`, literal home roots) + scan scope (`prompts/`, `**/skills/**`, `docs/`, `config/`, `*.md`).
- **Steps**
  1. Write the verifier: scan scope for forbidden patterns, return FAIL with file:line on any hit. verify: planted `Desktop\` string makes doctor FAIL; removed → OK.
  2. Register in the three wiring files. verify: `doctor.py` lists the new check; `/audit-mechanisms` passes.
  3. Fix known instances: `sub-bundles/claude/skills/sensei-cycle/SKILL.md` Desktop paths → derive from `ORDER_SAMURAI_ROOT`. verify: drift-gate OK.
  4. **(Approval-gated, out-of-repo)** global `~/.claude/CLAUDE.md` "Local LLM Routing": `LM Studio:1234` → `Ollama:11434`, model IDs → `deepseek-r1:7b`/`gemma4:e4b`/`gemma4:e2b`. *Flag separately — edits every project; do only on explicit OK.* verify: drift-gate OK on the doc.

### 1c. Close fail-open gaps (P7)
- **Files**: `bin/bushido_check.py` (fail-CLOSED when `tier ∈ {security, CRITICAL}` on the exit-3/error path); `prompts/ronin_*.md` + `bin/ronin-local` (explicit timeout on the LM call); `api/src/reflex-engine.ts` (confirm the spawn kill-ladder has an explicit timeout — it does; assert the value).
- **Steps**: each change + a test that the closed path blocks and the open path still allows. verify: `pytest tests/test_bushido_check.py`; manual ronin-local timeout fires.

- **Rollback (Phase 1)**: `git revert` the PR; verifiers are additive, path fixes are mechanical.
- **Out of scope**: typing the envelope, wiring mechanisms, trace, feedback edge.

---

## Phase 2 — Typed contract + structural honesty  *(milestone)*
PR: `feat/typed-metric-envelope`. Depends on Phase 1 green.
- **P4 — type the Python⇄TS seam**: author a versioned JSON Schema for the `wid_payload.json`
  reflex/metric envelope; validate on **write** (`agentica_core/aggregate.py`) and on **read**
  (`api/src/reflex-engine.ts` startup). Both sides in one PR.
  - verify: malformed envelope fails fast on both sides; current payload validates clean.
- **P5 — honesty invariant becomes structural**: doctor check — `status==LIVE ⇒ source path
  resolves AND was written within the metric window`.
  - verify: a LIVE metric pointed at a missing/stale source makes doctor FAIL.
- **Key files**: `aggregate.py`, `reflex-engine.ts`, new `schema/wid_payload.schema.json`, new `execution/verify_live_sources.py`.
- **Rollback**: revert PR; schema validation is opt-in at boundaries.

---

## Phase 3 — Determinism Tier-1 (cost + reliability)  *(milestone)*
PR(s): execute the **already-verified** `RONIN-MECHANISM-ROUTE-PLAN.md §5` (Tier-1 only:
`codebase_deps_audit.py`, `policy_enforcement_audit.py`, `subagent_audit.py`,
`canary_fault_detect.py`). Read-only detect mechanisms wired into `reflex-engine.ts` so LLM
skills stop firing where a deterministic `bin/*.py` exists.
- **Notes**: follow that plan's cautious show-each-diff protocol + explicit server restart;
  add `mechanism` block to `insights.py` METRIC_CONFIG; `kind:"mechanism"` on exec_log; honest
  expectation that detect-only mechanisms won't move `improved` (measure on correct-verdict).
- **Depends on**: Phase 2 P4 (the `mechanism` key should be schema-covered).
- **verify**: dry-run one cycle — mechanism runs, exec_log row `kind:"mechanism"`, fallback to
  LLM skill on non-zero exit; `doctor.py` + `aggregate.py` no new WARN.
- **Rollback**: feature-flag the mechanism branch in the engine; revert the insights.py keys.

---

## Phase 4 — Shared kernel + oversight→build feedback  *(milestone — biggest payoff)*
PR(s): the cross-pollination. Keep the loops separate; unify substrate.
- **4a Shared mechanism kernel**: sensei-cycle scouts re-measure via the same `bin/*.py`
  mechanisms (deterministic) instead of LLM re-derivation where one exists. verify: scout
  verdict for a covered metric matches the mechanism output.
- **4b Reuse `rival` for the dojo**: Sensei runs `rival` (post-audit mode) on a write-ronin's
  commit **before** cherry-pick. verify: a commit whose `improved` claim isn't backed by the
  diff is refused.
- **4c Close the feedback edge** *(keystone)*: a REFUTED or `structural` sensei-cycle verdict
  files a dojo "re-instrument `<metric>`" item into the shared `PROPOSED_BACKLOG.json` (already
  human-gated by `bin/ronin promote`). verify: a forced REFUTED verdict produces a tagged,
  unapproved dojo backlog item.
- **Depends on**: Phases 2–3. **Rollback**: each sub-edge independently revertable.

---

## Sequencing & rationale
`Phase 0 → 1` first and non-negotiable: every guarantee rides on a green gate + no drift.
`Phase 2` types the substrate that `3` and `4` build on. `Phase 3` is the big cost win and is
mostly *executing an already-verified plan*. `Phase 4` is the architectural payoff but only
safe once the gate, contract, and mechanisms are solid.

## Out of scope (whole plan)
- Merging the two loops (explicitly rejected — privilege isolation is a feature).
- Adding an LLM planning/planner stage (use code-as-mechanism instead).
- The mutating `pip_safe_upgrade --apply` autonomous route (stays human-gated).
- Rewriting the TS engine beyond the typed-read + mechanism-branch additions.

## Open decisions for the user (resolve before Phase 1)
1. **Quiesce vs worktree**: stop the daemon for the work window, or isolate in worktrees only?
2. **Global CLAUDE.md edit (1b.4)**: in-scope now, or split to its own approval since it
   affects every project?
3. **Phase 1 PR shape**: ship 1a+1b+1c as one "gate hardening" PR, or split 1c (fail-open) out?
