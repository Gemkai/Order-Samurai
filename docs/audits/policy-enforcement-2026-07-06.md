# Policy Enforcement Audit — Order Samurai + dojo API (2026-07-06)

Question: which health/gate checks are SPECTATORS (log/report only) vs real GATES
(a violation changes what happens next)? Calibration miss this audit was scored
against: `/api/health` returned `ok:true` for weeks while `DOJO_STATE_PATH`
resolved to a nonexistent Windows path.

Method: enumerate every check/policy in scope, trace each reader call site,
classify ENFORCER vs OBSERVER, and **verify the enforcers fire** (each fix below
was exercised against a real violating input, not just code-read).

## Verdict table

| Mechanism | Readers / consumers | Verdict (pre-audit) | What a violation did |
|---|---|---|---|
| `/api/health` (`api/src/server.ts`) | curl / monitors (no dashboard consumer exists) | **SPECTATOR** → now GATE | Returned `ok:true` unconditionally. Verified nothing. **FIXED**: verifies state readability, `ronin-pillar` presence, claude spawnability, telemetry recency; degraded → `ok:false` + HTTP 503 |
| Claude telemetry recency | *(no check existed anywhere)* | **UNOBSERVED** → now GATE | 2026-06-21→07-06 emitter outage ran 15 days undetected (emitter swallows all errors by design). **FIXED**: `doctor.py _run_claude_telemetry_checks()` FAILs (exit 1) when newest record >48h old / sink missing / unparseable |
| `doctor.py` exit code | `VALIDATE_CMD` (dojo.env), STEP A-prime in `prompts/dojo_cycle.md`, ronin-daemon `count_warns` | GATE (weak) | FAIL families exit 1; **dojo-timestamps and local-llm are WARN-only by design and never gate**. A-prime consumes the WARN *count* (ratchet), so WARNs gate only on *increase* |
| `VALIDATE_CMD` fallback literal | `dojo_overnight.sh`, `ronin-daemon.sh` | **BROKEN GATE** → fixed | Fallback ran `python agentica_core/aggregate.py`, which never resolves from REPO_DIR (Order Samurai) — with dojo.env absent the A-prime gate command errored. **FIXED**: fallback aligned to dojo.env (`python execution/doctor.py`) |
| STEP A-prime WARN ratchet | `dojo_cycle.md:31-35` (LLM-honored), `ronin-daemon.sh count_warns` + discard-on-increase (mechanical) | GATE (two-tier) | Mechanical in ronin-daemon (WARN increase → `git checkout -- .`, consecutive_fails++); LLM-honored in dojo_overnight cycles. Note: counts WARN **text**, ignores exit code — a FAIL that adds no WARN line bypasses the ratchet (daemon path) |
| Preflights (claude on PATH, prompt file, git repo, clean tree) | `dojo_overnight.sh:41-46`, `ronin-daemon.sh` | GATE | exit 1 before any cycle |
| `tmo()` timeout shim | both cycle runners | GATE (env) | GNU `timeout` absent on macOS killed every cycle spawn; shim (PR-#27-branch commit `c5a50cf`) was **stranded off main** until this branch merged it |
| Daily budget (`DAILY_BUDGET_USD`) | `dojo_overnight.sh:98`, `ronin-daemon.sh check_budget` | GATE (fail-open) | Halts keiko at cap; fails open if cost field absent/ledger corrupt |
| ReflexEngine eligibility (cooldown, loop-breaker, cross-channel dedup, REFUTED verdicts, non-remediable list) | `reflex-engine.ts _isEligible()` | GATE | Skips execution; loop-breaker persists across restarts (operator unstick endpoint exists) |
| `REFLEX_REQUIRE_GRANT` | `reflex-engine.ts:96` | **DECLARED-BUT-OFF** (default false) | Maturity/grant markers are telemetry-only until the env flag is flipped |
| `BUSHIDO_FAIL_OPEN` | `reflex-engine.ts:102` | **GATE, FAIL-OPEN by default** | Default `true`: a bushido_check.py error silently allows code-modifying skills. Recommended: set `BUSHIDO_FAIL_OPEN=false` in the api service env once bushido_check is proven stable |
| Maker-checker staging (audit_remediation_patch + pytest) | `reflex-engine.ts _afterRun()` | GATE | Rejected patch → status error, patch parked in `state/` (no auto-retry — known gap, by design) |
| WID payload schema (P4) | api startup + Python producer | GATE | AJV throws at boot if payload violates contract; missing file tolerated |
| Security-gate canary (`canary_fault_detect.py`) | `/canary-fault-diagnosis` skill only | OBSERVER | Fault classification feeds diagnosis; nothing blocks on a faulted canary |
| `config/*.json` policies (root hygiene, anti-drift, promotion, anti-sprawl) | doctor verifier family | OBSERVER→GATE via doctor | Violations become FAIL/WARN statuses; gate strength = doctor's exit-code consumers |

## Residual gaps (reported, intentionally not changed here)

1. **`BUSHIDO_FAIL_OPEN` defaults open** — flipping it can halt remediation on a
   bushido bug; needs an operator decision, not a drive-by default change.
2. **`REFLEX_REQUIRE_GRANT` defaults off** — same category: enable once reflexes
   carry maturity markers.
3. **ronin-daemon WARN ratchet ignores doctor's exit code** — a no-new-WARN FAIL
   bypasses the discard. Fix belongs in `count_warns`/cycle-accept logic.
4. **`dojo.env` pins `RONIN_LOCAL_MODEL="gemma4:e2b"`** — a Windows-era tag not
   installed on this Mac (installed: gemma4:4b, gemma4:12b, qwen3.6:35b,
   nomic-embed-text). `bin/ronin-local` calls will miss until repointed.
5. **Canary fault blocks nothing** — decide whether a faulted security-gate canary
   should gate reflex execution or stay diagnostic.

## Fixes shipped in this branch (each verified against a violating input)

- `/api/health` gate — verified: empty root → 503 `ok:false` (state_readable,
  ronin_pillar_present false); healthy sandbox → 200 `ok:true`.
- `doctor.py` claude-telemetry FAIL — verified: missing sink → `[FAIL]` +
  exit 1; live run → `[OK] newest record 0.0h old`, exit 0. 6 unit tests.
- `VALIDATE_CMD` fallback alignment in both cycle runners.
- (Task 1, same branch) single-instance guard: engine boot deferred until port
  bind; duplicate instance exits loudly instead of running a shadow ReflexEngine.
