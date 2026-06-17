## Handoff — 2026-06-15 Determinize mechanical ronin mechanisms (batch)

### Scope
Five additional deterministic mechanisms extracted from 0%-success LLM skills; rival adversarial review applied to each classifier. Full suite now: 7 determinized mechanisms, 216 tests, 0 regressions.

### Files changed

| File | Reason |
|------|--------|
| `bin/subagent_audit.py` | Deterministic spawn classifier; metric: `brush:Subagent_Efficiency_Index`; SA-01 fix: parallel rule requires non-trivial description |
| `tests/test_subagent_audit.py` | 26 tests; includes 2 false-positive regression guards (SA-01, SA-02) |
| `bin/policy_enforcement_audit.py` | Deterministic policy-reader classifier; metric: `sword:Rule_Violations`; PE-02: intent-context narrowing; PE-03: snippet-scoped classification |
| `tests/test_policy_enforcement_audit.py` | 31 tests; includes `test_classifies_raise_key_error_as_observer` and snippet-scope guards |
| `bin/model_selector.py` | Deterministic model routing mechanism; metric: `brush:Local_Routing_Share` |
| `tests/test_model_selector.py` | Fixture-driven + idempotency tests |
| `bin/canary_fault_detect.py` | Deterministic canary failure detector; metric: `bow:Canary_Failures` |
| `tests/test_canary_fault_detect.py` | Fixture-driven + idempotency tests |
| `bin/skill_consolidator.py` | Deterministic skill dedup detector; metric: `arts:Skills_Optimized` |
| `tests/test_skill_consolidator.py` | Fixture-driven + idempotency tests |
| `RONIN-MECHANISM-ROUTE-PLAN.md` | v2 verified wiring plan for live Governance kernel (supersedes draft) — staged, not applied |

### Tests run

```
python -m pytest tests/test_subagent_audit.py tests/test_policy_enforcement_audit.py \
    tests/test_model_selector.py tests/test_canary_fault_detect.py \
    tests/test_skill_consolidator.py tests/test_codebase_deps_audit.py -q
→  139 passed in 0.15s

python -m pytest tests/ -q
→  216 passed in 0.32s
```

### Key lessons captured (rival review found these despite green tests)

1. **Priority rule before exclusion filter** (`SA-01`): `turn_spawn_count >= 3` fired before `TRIVIAL_KEYWORDS` check — trivial 3-spawn turns got `justified_parallel`. Fix: `and not any(kw in desc_low for kw in TRIVIAL_KEYWORDS)` added to parallel rule.
2. **Intent-context required in semantic regex** (`PE-02`): `r"\braise\b.*[Ee]rror"` matched `raise KeyError`, `raise AttributeError` anywhere in file → false ENFORCER. Fix: `r"\braise\b.*(polic|block|violat|deny|[Pp]ermission)"`.
3. **Classify the excerpt, not the file** (`PE-03`): `classify_reader(content)` on 500-line file → `return False` in any `__eq__` method triggered ENFORCER. Fix: extract ±2-line snippet around the policy filename reference, classify snippet only.
4. **Real I/O fns must precede `run_audit`** (`PC-02`): Python evaluates default arg values at definition time — functions used as defaults must be defined first in the file.

### Open risks

- All 7 mechanisms are staged only: `RONIN-MECHANISM-ROUTE-PLAN.md` wires them but the live-kernel edits to `insights.py` + `reflex-engine.ts` have NOT been applied. Skill efficacy rates remain 0% until wired.
- `audit-mechanisms` skill still LLM-only (last remaining mechanical candidate).

### Security surface

- No new endpoints, no auth changes, no user input paths
- All mechanisms use `subprocess.run` with `shell=False` list args + explicit timeouts
- `--apply` / `--fix` flags are opt-in; defaults are plan/report-only

### Rollback plan

`git revert ae90577 52c803d 0833474 3902bb1` removes all five mechanisms. Each test file has no external dependencies; no state files are written on revert.

### 5 grep checks

| # | Check | Result |
|---|-------|--------|
| 1 | No raw user input in subprocess args | PASS — all args are hardcoded lists |
| 2 | No secrets in log calls | PASS — no `console.log`/`logger.` calls |
| 3 | Every new route has auth middleware | N/A — no new routes |
| 4 | Every new env var is read in code | N/A — no new env vars |
| 5 | Every changed line traces to stated goal | PASS — all insertions in mechanism + eval files |

### Expected Antigravity tasks

- Apply `RONIN-MECHANISM-ROUTE-PLAN.md` wiring to live Governance kernel (`insights.py` METRIC_CONFIG + `reflex-engine.ts`) to activate autonomous execution path
- Wire exec log writes so `no_op` vs `never_fired` is distinguishable in dashboard
- Post-wire: confirm `skill_efficacy.json` rates flip from 0% after first live run

---

## Handoff — 2026-06-15 Determinize pip-safe-upgrade mechanism

### Files changed

| File | Reason |
|------|--------|
| `bin/pip_safe_upgrade.py` | New deterministic mechanism: triage by risk tier, ML constraint detection, dry-run parsing, apply/block/skip decisions — all as pure functions with injected I/O; `_SAFE_PKG_NAME` regex rejects URL-scheme names at intake |
| `tests/test_pip_safe_upgrade.py` | 25-test eval harness covering all decision paths + URL-scheme injection rejection; no subprocess calls (lambda fixtures) |
| `docs/solutions/best-practices/inject-io-callables-for-pure-testable-mechanisms-2026-06-15.md` | Solution doc capturing the I/O injection pattern |
| `.mex/patterns/determinize-llm-skill-mechanism.md` | New pattern: step-by-step guide for extracting any LLM skill into a testable mechanism |
| `.mex/patterns/INDEX.md` | Added pointer to new pattern |

### Tests run

```
python -m pytest tests/test_pip_safe_upgrade.py -v  →  25 passed in 0.06s
```

### Open risks

1. **No JSON schema validation on the audit file (security gate Medium finding)**: The mechanism validates package name format (`_SAFE_PKG_NAME`) but does not validate the overall JSON schema of `dependency_audit.json`. A crafted file with correct name format but wrong types (e.g., non-string `version`) could produce a misleading report. Near-term: add a structural type-check on the two top-level arrays after the file is read. For higher assurance: HMAC signature on audit file from `dependency_audit.py`.

2. **`mechanism` field in live-kernel route not yet merged to `main`**: The wiring in `insights.py` + `reflex-engine.ts` is on branch `feat/reflex-mechanism-route` in the Agentica OS repo. This branch delivers the mechanism but the reflex engine won't invoke it automatically until that branch merges.
2. **`torch` CVE (GHSA-rrmf-rvhw-rf47) has no upstream fix**: Mechanism correctly blocks it; no action needed until torch ≥2.13.0 is compatible with torchvision/accelerate pins.
3. **`state/exec_log.jsonl` write not yet implemented in the mechanism**: The mechanism prints a report but does not write to the exec log, so the dashboard "ran and found nothing to do" vs "never fired" distinction is not yet surfaced.
4. **`docs/solutions/best-practices/` doc uses 2026-06-15 date** despite file creation on 2026-06-17 (session spanned dates). Date reflects the work date, not the doc creation date.

### Security surface

- No new endpoints, no auth changes, no user input paths
- `bin/pip_safe_upgrade.py` shells out to `pip install --upgrade` via `subprocess.run` — explicitly `shell=False` (list args), explicit 300s timeout, never reads or executes user-supplied strings
- `--apply` flag is opt-in; default is plan-only (no side effects)

### Rollback plan

`git revert 604c15c 7d8ec26` removes both committed files cleanly. The test file has no external dependencies; the mechanism file has no state files. Zero risk of data loss on revert.

If the live-kernel route (`feat/reflex-mechanism-route`) is already merged before revert: also revert the `insights.py` and `reflex-engine.ts` changes in the Agentica OS repo — the `mechanism` field will be ignored if the script doesn't exist, but it's cleaner to remove it.

### 5 grep checks

| # | Check | Result |
|---|-------|--------|
| 1 | No raw user input in subprocess args | PASS — no `spawn()`; args are hardcoded list |
| 2 | No secrets in log calls | PASS — no `console.log`/`logger.` calls |
| 3 | Every new route has auth middleware | N/A — no new routes |
| 4 | Every new env var is read in code | N/A — no new env vars |
| 5 | Every changed line traces to stated goal | PASS — 709 insertions, all in mechanism + eval |

### Expected Antigravity tasks

- Merge `feat/reflex-mechanism-route` (Agentica OS) to activate the autonomous execution path
- Wire exec log write to `state/exec_log.jsonl` so no-op runs are visible in the dashboard
- Post-merge: verify `Deprecated_Deps` reflex fires `bin/pip_safe_upgrade.py --tiers cve,security` via the TS engine

---

## Handoff — 2026-06-10 Aggregate Metrics Rethink + Kill Chain Security Layer
_Generated by /grill-me — Act 3_

### Source artifacts
- `PLAN.md` — locked plan (grilled + 3 rounds of local model adversarial review)
- `PLAN-REVIEW-LOG.md` — full argument transcript

---

### Codex review outcome
- **Rounds**: 3 (via local `google/gemma-4-e4b` — Codex CLI blocked on free ChatGPT account)
- **Final verdict**: APPROVED (by Claude as final arbiter after round 3)
- **Key concerns raised and addressed**:
  - Atomic JSONL appends → added temp+rename requirement to Step 2
  - Corrupt-record handling → added per-line try/except to all JSONL reducers
  - Source unavailability → added reducer contract: return `{val: None, error: "source unavailable"}` on FileNotFoundError/IOError
  - Datetime validation → Step 5 validates ISO-8601 before write; pre-plan items excluded from calibration count
  - Stale-data guard → Step 8 sets `data_gap: true` when event stream goes silent

---

### Files to be changed

| File | Action | Why |
|------|--------|-----|
| `state/kill_chain_taxonomy.json` | **Create** | 14-chain ATT&CK taxonomy, source of truth for kill chain reducer |
| `state/kill_chain_events.jsonl` | **Create** | Unified remediation log for all security hook detections |
| `state/kill_chain_unmatched.jsonl` | **Create** | Accumulator for unmatched events feeding the discovery scout |
| `state/proposed_kill_chains.json` | **Create** | Human review queue for dynamically proposed chains |
| `state/calibration_coefficients.json` | **Create** | Industry benchmark placeholders + calibration sample counters |
| `state/DOJO_STATE.json` | **Edit** | Add `started_at: null` to all backlog items (non-breaking) |
| `agentica_core/aggregate.py` | **Edit** | Add 5 new reducers: kill_chains_disrupted, estimated_agent_time_saved, estimated_cost_savings, estimated_human_time_saved, pending_chain_proposals |
| `~/.claude/hooks/prompt_injection_guard.py` | **Create** | Chain 13 PreToolUse hook — pattern + semantic prompt injection detection |
| `~/.claude/hooks/settings.json` | **Edit** | Register prompt_injection_guard as PreToolUse, async: false |
| `~/.claude/scripts/secret_scrubber_realtime.py` | **Edit** | Extend PostToolUse to Bash/Agent/Read results; add internal_ip, db_connection_string, internal_path patterns for Chain 14 |
| `scouts/kill_chain_discovery_scout.py` | **Create** | Weekly scout: clusters unmatched events, proposes new chains via gemma-4-e4b |
| `dashboard-ui/src/` (locate via grep) | **Edit** | Update aggregate display labels and add calibration state indicator |

---

### Tests to run

```bash
# Step 1 — taxonomy well-formed
python -c "import json; d=json.load(open('state/kill_chain_taxonomy.json')); assert len(d['chains'])==14"

# Step 5 — started_at dates valid
python -c "import json,datetime; d=json.load(open('state/DOJO_STATE.json')); [datetime.datetime.fromisoformat(i['started_at']) for i in d['backlog'] if i.get('started_at')]"

# Step 6 — kill chain reducer registered
python -c "from agentica_core.aggregate import REGISTRY; assert any(r['key']=='Kill_Chains_Disrupted' for r in REGISTRY)"

# Step 9 — calibration coefficients complete
python -c "import json; d=json.load(open('state/calibration_coefficients.json')); assert all(k in d for k in ['operations','architecture','craft','calibration_threshold'])"

# Step 11 — prompt injection hook self-test
python ~/.claude/hooks/prompt_injection_guard.py --test

# Step 12 — Chain 14 patterns present
python -c "from scripts.secret_scrubber_realtime import PATTERNS; assert any(p['name']=='internal_ip' for p in PATTERNS)"

# Step 13 — discovery scout dry run
python scouts/kill_chain_discovery_scout.py --dry-run

# Step 15 — TypeScript clean
cd "C:\Users\jemak\Desktop\Agentica OS\Governance\dashboard-ui" && npx tsc --noEmit
```

---

### Open risks

1. **gemma-4-e4b latency on Chain 13 PreToolUse hook**: 3s timeout fires on every tool call. If LM Studio is offline, hook must fail open (log + continue). Most likely production friction point.
2. **budget_ledger.json format**: Current file may have only a single record. Week-over-week comparison in Step 8 requires multiple dated records — verify before implementing.
3. **Proposed chain review UX**: Review/approval is manual JSON edit this phase. No dashboard approval flow.
4. **Aggregate display file location**: Step 15 target not confirmed — grep for label definitions before editing.
5. **Security gate deferred**: No code written yet. Security gate must run against the implementation diff before merge.
6. **Cross-model review gap**: Codex CLI unavailable (free ChatGPT account). Adversarial review used local gemma-4-e4b. Consider running Codex review post-implementation if subscription obtained.

---

### Security surface

- **New hook** (`prompt_injection_guard.py`): fires PreToolUse on ALL tool calls — high-frequency, must never block on LM Studio timeout
- **Extended scrubber** (`secret_scrubber_realtime.py`): now reads Bash stdout and Agent outputs — new attack surface is false positives quarantining legitimate output; threshold must be conservative
- **No new network endpoints** — all detection uses local LM Studio only
- **No new auth changes** — hooks run in existing Claude session context
- **New state files**: JSONL files are append-only, not committed to git — no secrets-in-repo risk

---

### Rollback plan

| Component | Rollback |
|-----------|----------|
| Reducers | Remove from REGISTRY in `aggregate.py` — metric vanishes cleanly, no data lost |
| DOJO_STATE schema | `started_at: null` is non-breaking — removal has no effect |
| kill_chain_taxonomy.json | Delete — reducer returns 0 (safe fallback) |
| Chain 13 hook | Remove from `settings.json` — stops firing immediately |
| Chain 14 scrubber extension | `git revert` the scrubber commit |
| All JSONL state files | Not committed — no rollback needed (accumulate forward-only) |

---

### Expected Antigravity tasks
- Integration validation (reducers surface correctly in dashboard API response)
- Security audit of Chain 13 hook (pattern list review, false positive rate)
- Security audit of Chain 14 extended scrubber (new pattern coverage)
- Deploy
- Post-deploy: verify kill chain reducer reads correctly in staging before promoting

---

### Pre-existing uncommitted stragglers (not from this plan)

`agentica_core/aggregate.py` and `scouts/autonomic_events_scout.py` have minor pre-existing refactor changes (variable renames `line`→`raw`, `continue`→`pass`, minor code cleanup from a prior session). These do not conflict with this plan's changes and should be committed separately before implementation begins.

---

## Handoff — 2026-06-06 Per-Pillar Ronin Mode Toggle

### Files changed

| File | Reason |
|------|--------|
| `bin/ronin-pillar` | Created — single-pillar SENSEI launcher missing from main (existed only on overnight branch commit `7daeadc`, never cherry-picked). The TUI's `runRoninForPillar()` already expected this path. |
| `.mex/src/tui.ts` | Modified (not git-tracked — `.mex/` is gitignored): added `toggleRoninMode()`, `TOGGLE_SHORTCUTS`, `PILLAR_TOGGLE_KEYS`, updated `Summary` pillar row rendering, added keyboard handlers, added auto-remediation `useEffect`, updated footer help text. |

### What was built

**Per-pillar ronin mode toggle button on the dashboard summary rows.**

Each pillar row now shows a mode-aware button — `[◉ Ronin ON]` (green) or `[○ Ronin  ]` (dim) — read from `state/DOJO_STATE.json`. Pressing `Shift+1/2/3/4` (`!/@/#/$`) on the dashboard toggles that pillar's `ronin_mode` field between `"ronin"` and `"dormant"` and refreshes immediately.

Auto-remediation: a `useEffect` on `state.data` fires after every data load and calls `runRoninForPillar()` for any pillar where `ronin_mode === "ronin"` AND `live_current < live_baseline` AND no agent is already running. This implements autonomous guardian behavior (RONIN_SPEC Tier 2) for each pillar independently.

### Tests run

```
cd .mex && npx tsc --noEmit   → PASS (TypeScript clean, twice — before and after /simplify)
bash bin/ronin-pillar badpillar → exit 1, "Unknown pillar: badpillar" (arg validation OK)
```

### Open risks

- `bin/ronin-pillar` actually launches `claude -p …` which requires the `claude` CLI to be in PATH and authenticated. The TUI shows `[!! error ]` if the spawn fails — user will see it.
- The auto-remediation `useEffect` reads `state.roninStatus` at time of data load. If a pillar transitions to running mid-refresh, there's a window where it could double-trigger. Existing `if (currentStatus === "running") continue` guard mitigates this but race is not impossible in rapid refresh scenarios.
- `live_current` is `null` for all pillars in the current seed state — auto-remediation only fires when `live_current` is a real number, so no spurious fires on a fresh repo.

### Security surface

No new endpoints, no auth changes. The only user-facing input path is the keyboard `input` character in `useInput`, which is constrained by `PILLAR_TOGGLE_KEYS` record lookup — only `!/@/#/$` resolve to a valid `PillarSlug`; all other characters are no-ops. No raw input reaches `spawn()`.

### Rollback plan

**git revert** — `bin/ronin-pillar` is the only committed artifact. Reverting `20e8b33` removes it:

```bash
git revert 20e8b33 --no-edit
```

The `tui.ts` changes are outside git (`.mex/` gitignored). To revert `tui.ts` manually, restore the 5 changed sections:
1. `import { readFileSync }` (remove `writeFileSync`)
2. Remove `toggleRoninMode()`, `TOGGLE_SHORTCUTS`, `PILLAR_TOGGLE_KEYS`
3. Restore original `rLabel`/`rColor` ternaries (no `isEnabled` branch)
4. Remove `!/@/#/$` keyboard handler block, restore 4-line original
5. Remove auto-remediation `useEffect`
6. Restore original footer string (drop `!/@/#/$` hint)

### Expected Antigravity tasks

- Integration test: launch `mex` TUI, verify toggle buttons render and respond
- Verify auto-remediation fires correctly when a pillar drops below baseline (requires real `live_current` data flowing)
- Consider adding a 60-second auto-refresh interval when any pillar has `ronin_mode === "ronin"` (currently auto-remediation only fires on manual refresh)
