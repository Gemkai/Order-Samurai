# Harness eval suite — the yardstick for self-harness proposals (M3)

This directory grades the Order Samurai harness. It is the held-in/held-out gate that
`bin/self_harness_cycle.py` (M4) runs against every candidate edit to
`harness/editable_surface.json`. Design source: Self-Harness (arXiv 2606.09498) +
Research/SELF_HARNESS_EVOLUTION_PLAN.md.

## The one rule that shaped everything here

**Every task must be sensitive to at least one declared surface knob.**

The plan's first draft imagined agent-behaviour tasks (long-context retention, leak checks,
tool-vs-guess) run against headless `claude` spawns. Those tasks are good tests of an *agent*,
but the v1 editable surface is six numeric knobs consumed by the governance engine's scouts,
judges, reducers, and loop-breaker — and none of those tasks reads any of them. A suite that
cannot detect the effect of ANY legal proposal is a spectator, not a gate (`/policy-enforcement-audit`
smell): every candidate would sail through with Δ=0 everywhere, and noise would pick winners.

So each task here exercises a knob's real consumer, and the suite is deliberately cheap:
deterministic Python + 3 local-LLM calls, no headless spawns. When the surface widens to
instruction/text knobs (v2), agent-behaviour tasks become sensitive and belong here — not before.

## Layout

- `tasks/*.json` — one task per file: `{id, group, kind, description, grounded_in, sensitive_to,
  fixture, grader, repeats}`. `kind: miner` = expected RED today (encodes a known weakness);
  `kind: guard` = expected GREEN today (its only job is to fail loudly when a fix breaks it).
- `fixtures.py` — programmatic fixture builders. The runner materialises them into a temp
  workspace per repeat and **fingerprints every seeded file**; a changed fingerprint after
  grading fails the task outright (the answer-key-edit trap from the Carbon Layer video).
- `graders.py` — deterministic pass/fail functions. **Must never import `harness_config`**:
  expectations are literals pinned the day the task was written. A grader that compares against
  the live editable value auto-passes every knob change (the video's own first dry-run bug).
  The coupling audit test enforces this.
- `replay_sim.py` — a small simulator of the reflex-engine loop-breaker/cooldown CONTRACT
  (hard vs incomplete buckets, park limits, cooldown spacing, improvement reset). It reads the
  knobs — it is the consumer-under-test for group D. Kept honest by tests that mirror
  `reflex-engine.ts` semantics; if the TS contract changes, change BOTH and say so in the commit.
- `split.json` — the frozen held-in / held-out assignment. Held-out task ids are NEVER given to
  the proposer. Rotation policy: after every 5 accepted edits (or 90 days), retire ≥1 held-out
  task and author a replacement from the newest weakness cluster — selection against held-out
  leaks information into surviving fixes even though the proposer never reads it.

## Task groups → knob coverage

| Group | Knob(s) | Consumer exercised | Miner |
|---|---|---|---|
| A bounded transcript scan | `scout_max_files` | `bin/tool_trust_annotator.py` | a2 (held-in), a4 (held-out — same root cause, hidden: the A2/A4 pair from the video) |
| B judge pipeline | `judge_max_tokens` | `agentica_core/evals/judge.py` (live gemma, 3 repeats) | — |
| C cliff reducer | `context_cliff_token_threshold` | `agentica_core/aggregate.py r_context_cliff_events` | — |
| D loop-breaker replay | `loop_breaker_limit`, `incomplete_limit`, `reflex_cooldown_minutes` | `replay_sim.py` (contract stand-in) | — |

Two red miners (a2, a4) encode the one weakness the suite is grounded in: bounded scans miss
deep history (measured live 2026-07-16 — trace coverage 8.3%; the video's clamp failure is the
same mechanism). Both are fixable by one surface key, which is exactly what a proposal round
should discover.

## Known v1 limitations (deliberate, documented)

- `repeats` is 1 for deterministic tasks (repeating an identical pure computation is waste) and
  3 for the live-LLM task b1. Fractions only matter where stochasticity exists.
- Validation runs use **env-override pinning** (`OS_HARNESS_<KEY>=<value>`), the escape hatch
  `harness_config.get_value` was designed with — not git worktrees. No agent writes files during
  validation, so there is nothing for a worktree to contain. File edits happen only at delivery,
  as a human-applied patch.
- Group B/C consumers read their knobs via `agentica_core` files that carry uncommitted M1 edits;
  until those are committed, a git-worktree-based runner would silently test the literal values.
  Env-override validation in the live tree avoids that trap too.
