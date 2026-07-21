---
title: Canary_Failures and Gate_Canary_Fault read two different files — by design, not divergence
date: 2026-06-20
category: docs/solutions/best-practices/
module: dojo / sword pillar / reflex remediation
problem_type: source_of_truth_clarification
component: agentica_core/scouts + aggregate.py
severity: medium
applies_when:
  - A rival/scout reports a Canary_Failures breach the aggregate reducer seems to contradict
  - Reconciling a metric against the wrong reducer in the wrong kernel
  - Auditing whether a canary all-fail is real or a harness artifact
tags: [canary, source-of-truth, sword, reflex, measure-act, two-kernels, windows]
---

## TL;DR

`Canary_Failures` and `Gate_Canary_Fault` are **two distinct sword/Audit-Trail metrics
that read two different files on purpose.** They are NOT two definitions of one metric.
The reported "divergence" is the rival's *reproducibility lens* misattributing — it read
the **frozen repo-local** `aggregate.py:_canary_health` (which feeds `Canary_Health` /
`Gate_Canary_Fault`, sourced from `security_gate_canary.json`) and concluded that was the
source for `Canary_Failures`. It is not. In the **live Governance kernel**,
`Canary_Failures` is correctly sourced from `canary_status.json`.

So: **do not point the scout and the reducer at the same file.** The split is correct.
Documenting it (this file) is the resolution. The real defects are downstream (below).

## The two metrics — authoritative sources

| Metric | Authoritative file | Producer | What it measures | Current value |
|---|---|---|---|---|
| `Canary_Failures` | `~/.claude/data/canary_status.json` (`failed`) | `behavioral_canary.py` → `run_canary_scout.py` (SessionStart, regenerates when ≥7d stale) | # behavioral-canary skills that did not pass their last suite run | **5** (history [5,5,5,5], stuck) |
| `Gate_Canary_Fault` / `Canary_Health` | `~/.claude/data/security_gate_canary.json` | `/security-gate` skill | Is the security-gate self-test working + fresh (≤max_age_days)? | **0** (healthy; last_run 2026-06-06, max_age 35) |

Wiring (live Governance kernel, `~/Desktop\Agentica OS\Governance`):
- `agentica_core/scouts/__init__.py:80-82` reads `canary_status.json` → `out["canary_failures"] = failed`
- `agentica_core/scouts/__init__.py:86-99` reads `security_gate_canary.json` → `out["gate_canary_fault"]`
- `agentica_core/aggregate.py:1344-1347` maps both into the sword "Audit Trail" group.

The repo-local Order Samurai `agentica_core/aggregate.py:259-284` (`_canary_health` →
`Canary_Health`) is the **frozen kernel** copy (FAIL-only). It does not define
`Canary_Failures` at all. Reconcile live-dashboard metrics against the **Governance**
copy, never the repo-local one (see memory: "Two agentica_core kernels").

## The REAL defects (the split itself is fine) — all fixed 2026-06-20

### Defect A — mitigation mis-routing (Measure→Act violation)
Both metrics carry the same `mitigation_command: /canary-fault-diagnosis`. That skill
(via `bin/canary_fault_detect.py`, `DEFAULT_CANARY_PATH = security_gate_canary.json`)
inspects and regenerates **`security_gate_canary.json` only**. It never touches
`canary_status.json`. So for `Canary_Failures`:
- The wired remediation can never move the metric → reflex fires, fails, retries,
  hits `stuck:true` (consecutive=2) → escalates to `state/backlog/needs_human_Canary_Failures.md`.
This is a dead-samurai metric: a number with no *effective* wired action behind it.
**Fixed:** the reflex engine only resolves mechanisms from `<OrderSamurai>/bin/`
(reflex-engine.ts:1221), and a behavioral skill regression has no cheap deterministic
auto-fix anyway — re-running the LLM suite is expensive and can't repair code. So
`insights.py` METRIC_CONFIG now marks `Canary_Failures` `auto_remediable: False` (engine
skips remediation → no more doomed loop / false needs_human escalation; reflex-engine.ts:465)
and points the human-facing command at `python ~/.claude/scripts/behavioral_canary.py`
(re-measure, then investigate the regressed skill). `Gate_Canary_Fault` keeps
`/canary-fault-diagnosis` — correct for *its* file.

### Defect B — the all-fail is a harness artifact, not skill regression
`canary_status.json` last wrote 2026-06-13 with `failed=5, passed=0`. All five canaries
failed with **identical** `exit 3221225794` = `0xC0000142` = `STATUS_DLL_INIT_FAILED`:
the `claude --print` child process failed to *initialize* on Windows headless. This is a
spawn/environment fault, not five rotted skills. `canary_history.jsonl` confirms the
pattern — every all-fail run is a harness error class (`claude CLI not on PATH`,
`timeout`, `exit 1`, `exit 0xC0000142`); when the spawn succeeds the suite passes
(3/5 on 2026-06-06, 5/5 on 2026-04-23). `_run_claude_print` returns these as
`passed=False`, and the scout summed them into `failed`, so a broken spawn inflated
`Canary_Failures` to 5.
**Fixed:** `behavioral_canary.py` now tags a `CanaryResult.could_not_run` whenever the
CLI cannot run to a clean verdict (spawn/init/timeout fault), writes `could_not_run` +
skill-only `failed` to `canary_status.json`, and excludes could-not-run from regression
detection. The scout (`scouts/__init__.py`) suppresses `Canary_Failures` entirely when
`could_not_run >= total` (all harness-fault) → metric stays SIMULATED (honest unknown)
rather than a false 0 or false 5. A stale all-fail is as untrustworthy as a stale all-clear.

### Defect C — dry-run wrote a fake all-pass to the live status file
`behavioral_canary.py main()` called `_write_report` unconditionally, so
`--dry-run` (fixture validation, no LLM calls) stamped a `5/5 pass` into the production
`canary_status.json` — a false all-clear the scout reads as `Canary_Failures=0`.
**Fixed:** `_write_report`/`_append_history` are now gated behind `not args.dry_run`;
a dry-run only prints to the console and never persists.

## Liveness verdict
`canary_status.json` is **live-produced, not a frozen orphan**: it has a producer
(`behavioral_canary.py`) and a refresh hook (`run_canary_scout.py`, regenerates at ≥7d).
The value it held (failed=5 from 2026-06-13) was a **stale all-fail from a broken spawn**.
With Defect B fixed, the next hook re-run will write `could_not_run=5, failed=0` if the
spawn is still broken → the scout suppresses the metric → `Canary_Failures` reads
SIMULATED (honest "cannot currently measure") instead of a false all-fail. The live file
was restored to the real 2026-06-13 snapshot (≥7 days old → the SessionStart hook will
re-measure), after a dry-run during this work briefly clobbered it (Defect C, now fixed).

Same anti-pattern family as `project_architecture_score_divergence` (two files, two
definitions) — but here the two files are *correctly* two metrics; the bug is the
remediation routing and the harness conflation, not the source split.
