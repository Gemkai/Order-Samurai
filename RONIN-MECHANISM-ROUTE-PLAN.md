# Mechanism-route execution plan (v2 — VERIFIED) — wire deterministic mechanisms into the reflex engine

**Status: STAGED, NOT APPLIED.** This supersedes `RONIN-MECHANISM-ROUTE-DRAFT.md`, which
was written against stale assumptions. Three read-only recon agents verified the live
Governance kernel on 2026-06-16; the draft's core edits targeted the wrong files. Execute
this in a fresh, cautious session, showing each diff before writing.

> The mechanisms (`bin/*.py`) + their evals (`tests/test_*.py`) are already built, committed,
> and green (215 tests pass). This plan is ONLY the wiring that makes the reflex engine *run*
> them instead of spawning the LLM skill.

---

## §0 — Corrections to the draft (why v2 exists)

| Draft assumption | Reality (verified 2026-06-16) | Source |
|---|---|---|
| `METRIC_CONFIG` lives in `reflexes.py` | It's in **`agentica_core/insights.py`** (62 entries) | recon A |
| The runner is Python; patch `_nudge_command()` | The executor is **TypeScript `api/src/reflex-engine.ts`**. `reflexes.py:_nudge_command` is a *builder*, never executes, and does not read `entry["skill"]` | recon B |
| Reflex dict has a `"skill"` field the runner keys on | Runner keys entirely off **`command`** (the `/skill` string); `skill` is derived in TS by stripping `/` | recon B (`reflex-engine.ts:826`) |
| Emit `routing_efficient` to feed Cost-Savings comp2 | **comp2 was removed** from `_estimated_cost_savings` (never calibrated). Routing efficiency must surface as a COUNT elsewhere, not as cost-savings dollars | recon C |
| `mechanism` cmd as `["python","bin/x.py"]` (relative) | Works today but fragile. Use **`path.join(ORDER_SAMURAI_ROOT,'bin','x.py')`** absolute; the junction path is a red herring | recon B |

---

## §1 — Verified facts the edits depend on

- **METRIC_CONFIG**: `agentica_core/insights.py`. Each entry is a plain dict with `"skill"`,
  `"command"`, optional `"dir"/"warn"/"fail"/"weight"/"readonly"/"per"`. Adding a sibling
  `"mechanism"` key is safe.
- **Executor**: `api/src/reflex-engine.ts`.
  - Primary spawn `:523-538`: `spawn(cmd, args, { cwd: ORDER_SAMURAI_ROOT, ... })`. No native timeout — a manual SIGTERM→SIGKILL kill-ladder (`EXEC_TIMEOUT_MS = 5min`, `:545-573`).
  - `buildSkillSpawnArgs` `:79-90` is the only command→argv translation.
  - Eligibility gate `SAFE_COMMAND_RE` `:401` keys on `command`.
  - exec_log row built `:849-861`, appended to `state/exec_log.jsonl` (`:864`). **No `kind` field today.**
  - `ORDER_SAMURAI_ROOT` = the REAL repo path (`state.ts:8-10`), which is the runner's cwd.
  - ⚠️ The existing `runFallback` (`:588-675`) targets `bin/execute_remediation_gemini.py`, which **does not exist** — copy its *shape*, not its target.
- **Reducer seams** (`agentica_core/aggregate.py`):
  - ✅ Agent-Time-Saved reads `item.get("kind","skill")` → `ops_coef[kind]["benchmark_min"]` (`:862-868`). A `kind:"mechanism"` row is benchmarked per-kind.
  - ❌ Cost-Savings comp2 (`routing_efficient`) — REMOVED (`:921-926`). Do not depend on it.
- **emit_event.py** (this repo, `bin/emit_event.py`): additive `--routing-efficient` / `--kind`
  flags are safe (omit-None pattern, `:89-96`); `mechanism_run` is already a canonical event
  (`telemetry.py` `AUTONOMIC_EVENTS`).

---

## §2 — Design (the clean shape)

Keep `command` = the LLM fallback skill (UNCHANGED — preserves the `SAFE_COMMAND_RE` gate,
skill-derivation, cooldown key, and efficacy lookups, all of which parse `command`). Add an
**optional** `mechanism` block. The TS runner, when `mechanism` is present and eligible:

1. Resolve `path.join(ORDER_SAMURAI_ROOT, 'bin', <script>)`, spawn `python <abs> [args]` with
   `cwd: ORDER_SAMURAI_ROOT` and an **explicit timeout** (60–120s — these are not LLM calls;
   the mandatory-timeout rule applies).
2. Exit 0 → record exec_log row with `kind:"mechanism"`, emit `mechanism_run` (routing-efficient).
3. Non-zero → run the existing skill path (`command`) and record `kind:"skill"`,
   `fallback_from:"mechanism"`.

`kind` defaults to `"skill"` on the existing path so historical rows + the LLM branch are
unchanged (back-compatible — `remediation.py:79` reads `skill`, not `kind`).

---

## §3 — Mechanism → metric mapping, TIERED by semantic fit

Recon A found a METRIC_CONFIG home for all 7, but a mechanism should only auto-route where
running it actually serves the metric. **Tiering is a v2 addition — not all 7 should be wired.**

### Tier 1 — wire first (deterministic detect/report, LLM skill stays as judgment fallback)
| Mechanism | Metric (insights.py line) | Notes |
|---|---|---|
| `codebase_deps_audit.py` | `Deprecated_Deps` (40) | read-only scan; produces `dependency_audit.json` |
| `policy_enforcement_audit.py` | `Rule_Violations` (34) | read-only (`"readonly":True`) |
| `subagent_audit.py` | `Subagent_Efficiency_Index` (50) | read-only log analysis |
| `canary_fault_detect.py` | `Gate_Canary_Fault` (36), `Canary_Failures` (35) | read-only; detect-half only — LLM keeps the gate-broken repair tail |

### Tier 2 — wire with care
| Mechanism | Metric | Caveat |
|---|---|---|
| `pip_safe_upgrade.py` | `Vulnerability_MTTR` (30), `Deprecated_Deps` (40) | the only **mutating** mechanism (`--apply`). Default plan-only is read-only. Must run AFTER deps-audit (it consumes `dependency_audit.json`). Apply path interacts with the blast-radius approval gate. |
| `skill_consolidator.py` | `Skill_Conflicts` (64) | detect-only (produces MERGE candidates); the actual merge stays human/LLM |

### Tier 3 — do NOT wire as a reflex
| Mechanism | Mapped metric | Why not |
|---|---|---|
| `model_selector.py` | `Local_Routing_Share` (48), `Fallback_Recovery_Rate` (20) | **Semantic mismatch.** It scores task complexity → picks a *cloud* tier (haiku/sonnet/opus). It does not increase local-LLM routing share or recover fallbacks. Keep it a `/command` advisor; do not auto-route it. |

---

## §4 — Important behavioural note (set expectations honestly)

The RONIN-DETERMINIZATION-PLAN promised "watch `skill_efficacy.json` flip off 0%." That holds
only for **mutating** mechanisms. Tier-1 mechanisms are **detect/report** — running them does
not move their metric, so `improved` (which requires the metric to actually change after the
run, `reflex-engine.ts:734-764`) will stay `false`, same as the LLM skill. Their value is
**speed + cost (no LLM call) + a trustworthy deterministic verdict** for a human/downstream to
act on, plus Agent-Time-Saved credit via the `kind` benchmark. Do not expect the efficacy
number to climb for detect-only mechanisms; measure them on the right axis (runs that produce
a correct verdict), not on `improved`.

---

## §5 — Edit list (CORRECTED targets)

1. **`agentica_core/insights.py`** — add a `"mechanism"` block to each Tier-1 (and chosen Tier-2)
   METRIC_CONFIG entry; leave `"skill"`/`"command"` untouched. Show each entry's exact literal
   before editing (recon A has them verbatim).
   ```python
   "mechanism": {
       "script": "bin/codebase_deps_audit.py",   # resolved via ORDER_SAMURAI_ROOT in TS
       "args": [],
       "read_only": True,
       "timeout_s": 120,
   },
   ```
2. **`api/src/reflex-engine.ts`** — the real work:
   - Extend the reflex/entry type with optional `mechanism`.
   - In the spawn path: if `mechanism` present + eligible, spawn
     `python path.join(ORDER_SAMURAI_ROOT,'bin',script)` with `{ cwd: ORDER_SAMURAI_ROOT, timeout }`;
     on non-zero exit fall through to the existing `command` skill spawn.
   - Add `kind` to the exec_log record (`:849-861`), default `'skill'`.
   - Mirror the `runFallback` *shape* (kill ladder / close handler) but point at a real path.
   - Decide: should a read-only mechanism bypass the `REFLEX_CODE_APPROVAL_MS` gate (it's keyed
     off the skill's `code_modifying`, not the mechanism)?
3. **`bin/emit_event.py`** (this repo) — add `--routing-efficient` (store_true) + `--kind`
   (default None), set on the event only when provided. Emit `mechanism_run` on a successful
   mechanism run. (Do NOT wire this to Cost-Savings comp2 — it's gone; treat it as a count/marker.)
4. **Carry `mechanism` through the Python builder → wid_payload → TS**: confirm how reflex dicts
   flow from `reflexes.py`/insights into `wid_payload.json` that the TS engine reads, so the new
   key survives serialization. (Recon B: TS reads `entry.command`; verify it can also read
   `entry.mechanism`.)

---

## §6 — Validate / dry-run / activate (live-kernel session)

1. Pull each exact METRIC_CONFIG literal (recon A has them); add `mechanism` (Edit 1). Show diff.
2. Implement the TS branch (Edit 2). Show diff. **`npm run build` / restart the API server** —
   `tsx watch` auto-reloads on save, but confirm the engine actually restarted (TS changes are
   NOT picked up by `refresh_dashboard.py`, which mirrors DATA not code).
3. Add emit_event flags (Edit 3). Show diff.
4. Validate: `python execution/doctor.py && python agentica_core/aggregate.py` — no new WARN.
5. Dry-run one cycle (`DOJO_DRYRUN=1` if supported, or a single forced reflex): confirm the
   mechanism runs, exec_log row carries `kind:"mechanism"`, and the expected report file is written
   (`dependency_audit.json` for deps-audit).
6. Confirm fallback: force a non-zero mechanism exit → the LLM skill runs, row shows
   `fallback_from:"mechanism"`.

---

## §7 — Open decisions for the user (resolve before executing)

1. **Scope**: start with Tier 1 only (recommended — prove the route), or include Tier 2?
2. **Approval gate**: should read-only mechanisms bypass `REFLEX_CODE_APPROVAL_MS`?
3. **Efficacy blending**: cooldown/efficacy is keyed by `${id}::${command}` — a mechanism and its
   fallback skill share stats unless separated. Accept blending, or split the key by `kind`?
4. **pip-safe `--apply`**: keep mutating apply OUT of the autonomous route (plan-only), yes?

## §8 — Safety
Read-only mechanisms (Tier 1 + detect Tier 2) never mutate, so the route is safe to run
unattended once wired. The only mutating path is `pip_safe_upgrade --apply` (kept out of the
autonomous route per §7.4). The TS-engine edit is the one requiring the cautious show-first
protocol + an explicit server rebuild/restart.
