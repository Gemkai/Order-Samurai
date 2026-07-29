# Remediation Back-Test — 2026-07-19 (read-only reconstruction)

**What this is:** a retrospective analysis of every remediation run in
`state/exec_log.jsonl` (157 rows, 2026-06-08 → 2026-07-19T12:13Z) using the
attempt-counting and correlation logic shipped in the §A1 fix
(`2026-07-19-metric-surface-review.md`, Part D §3).

**What this is NOT:** a backfill. Nothing here was written into `state/`,
`exec_log.jsonl`, or the payload. Historical runs never recorded fire-time
`metric_before`/`metric_after` values, and reconstructing them from the ~20
`metrics_history.jsonl` snapshots would fabricate measurements the runs never
took — poisoning `skill_efficacy.json` cooldowns, the loop-breaker ledger, and
rival's post-audit ("removal, never faking"). The judged events below are only
those the historical record honestly supports.

---

## Headline

| | |
|---|---|
| Attempts (all outcomes) | **157** — 139 reflex-engine, 18 human/dashboard |
| Completed (`done`) | **69** — 51 reflex + 18 human |
| Reflex non-completions | `no_change` 44 · `error` 33 · `timeout` 11 |
| Judged events (real before/after) | **25** — 9 improved · 5 regressed · 11 flat |
| Success rate (improved/judged) | **33.3%** — the dashboard's "33" |
| Reflex-attributed judged events | **2**, both 2026-06-08 (the drought the audit diagnosed) |

The 33% is not "the engine works a third of the time" — it is "of the 25 runs
that happened to be measurable, a third moved their metric." The other 132 runs
were invisible to judgment because no before/after existed; that blind spot is
what the fire-time capture closes going forward.

## Weekly arc (reflex-engine runs only)

| Week | done | error | timeout | no_change | Reading |
|---|---|---|---|---|---|
| 2026-W23 | 8 | 8 | — | — | first live days; the 2 reflex-judged events land here |
| 2026-W24 | 40 | 8 | 8 | — | peak throughput |
| 2026-W25 | — | 2 | — | — | near-idle |
| 2026-W28 | 2 | 12 | 2 | — | error wave |
| 2026-W29 | 1 | 3 | 1 | 44 | the "silent" period: 44 runs produced zero repo diff |

W29 is the finding the panel could not show before the fix: the engine was
firing constantly and almost never changing anything — attempted 49, improved 0.

## Per-skill lifetime (reflex-engine attempts)

| Skill | Attempts | done | Note |
|---|---|---|---|
| simplify | 24 | 15 | workhorse |
| wiki | 18 | 6 | |
| model-selector | 14 | 4 | |
| insights | 13 | 2 | read-only diagnostic — `no_change` is its normal exit |
| **consolidate-memory** | **12** | **0** | chronic 0% — audit Part C demotion candidate |
| codebase-cleanup-deps-audit | 10 | 7 | |
| audit-mechanisms | 8 | 6 | |
| humanizer | 8 | 3 | |
| **pip-safe-upgrade** | **4** | **0** | Part C: may be CVE-quiet period, watch |
| **token-optimizer** | **4** | **0** | chronic 0% — demotion candidate |
| **security-audit** | **4** | **0** | |
| subagent-audit | 3 | 0 | |
| context-optimization | 3 | 3 | |
| others (9 skills) | ≤2 each | — | |

## Judged events (all 25 the history supports)

Only 2 of 25 are reflex-attributed (2026-06-08: Rework_Loops 152→42 improved via
insights; Local_Routing_Share 8.5→0 regressed via model-selector). The remaining
23 are human/telemetry-correlated. Standouts:

- **Improved:** Token_Execution_Density 444k→77k and 444k→96k (token-optimizer,
  via correlation — note the same skill is 0/4 on *reflex* completions),
  Rule_Violations 914→150, Chain_Depth_Avg 45→3.5 and 45→4.0,
  Local_Routing_Share 0→31.1.
- **Regressed:** Frustration_Signals 87→120→208, Rework_Loops 207→269→489
  (three consecutive worsening windows around /insights use — /insights is
  diagnostic, not causal; these track the mid-July workload).
- **Flat:** Wiki_Orphans pinned at 58, Faithfulness_Score pinned at 40,
  Context_Cliff_Events pinned at 22 — the "honest flat" the audit predicted.

Caveat: correlation-event counts shift slightly between refreshes (a new
metrics_history snapshot re-brackets windows). Fire-time events, once they
accumulate, are immutable per-run measurements and won't do this.

## Forward state

Fire-time capture went live 2026-07-19 ~15:46Z (service restart onto the new
engine). Every autonomous run from now on writes `metric_before`/`metric_after`
to its exec_log row regardless of outcome; the panel accumulates ⚡reflex events
from real measurements. This document is the baseline to compare against.
