---
title: "Skill routing adherence + work volume — paired ratio/volume metrics over the router hook logs"
date: "2026-07-19"
category: "docs/solutions/best-practices"
module: "skill_routing_adherence"
problem_type: "best_practice"
component: "tooling"
symptoms:
  - "Hand-rolled critical work (security reviews, code reviews done without the governing skill) left no governance telemetry at all"
  - "An adherence ratio alone reads ~0 for a busy hand-rolled session — indistinguishable from a quiet week"
root_cause: "design"
resolution_type: "code_fix"
severity: "medium"
related_components:
  - "~/.claude/scripts/skill_router_nudge.py (writes data/skill_routing.jsonl)"
  - "~/.claude/scripts/skill_invocation_logger.py (writes data/skill_invocations.jsonl)"
  - "~/.claude/scripts/skill_routing_gate.py (Stop-hook enforcement tier)"
tags: [skill-routing, governance, sword, metrics, order-samurai]
---

# Skill_Routing_Adherence + Governance_Work_Volume

`execution/skill_routing_adherence.py` computes the paired sword metrics over the
router hook's logs:

- **Skill_Routing_Adherence** = 100 × (critical-work prompts where the governing
  skill WAS invoked) / (critical-work prompts detected). Bar is 80, not 100 — a
  documented one-line-fix skip is legitimate.
- **Governance_Work_Volume** (`compute_work_volume()`) = how much critical work was
  DETECTED, routed or not, in a 30-day window — the volume signal that keeps
  "high volume, low adherence" from vanishing into a ~0 ratio. Same per-pair
  counting unit as the adherence denominator so the numbers are directly
  comparable.

## Producer chain (all three hooks, wired 2026-07-19 via release lane f1a64cd)

`skill-router-nudge` (UserPromptSubmit, sync) detects critical-work vocabulary —
calibrated from 1,605 real prompts, deliberately low-false-positive — injects a
routing directive, and appends the detection to `data/skill_routing.jsonl`.
`skill-invocation-logger` (PostToolUse on Skill, async) records what skills
actually fired. `skill-routing-gate` (Stop, sync) nudges in-session when a
detection had no matching invocation.

## Gotcha

Both metrics are SIMULATED until the router hook logs its first real detection —
an empty `skill_routing.jsonl` on a freshly-wired mechanism is health, not a bug
(the 2026-07-19 false-alarm: a hyphen/underscore grep mismatch briefly diagnosed
the wired hooks as undispatched).
