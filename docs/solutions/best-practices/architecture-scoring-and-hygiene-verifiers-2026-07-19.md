---
title: "Earned architecture scoring, remediation-patch auditing, and the third root-hygiene surface"
date: "2026-07-19"
category: "docs/solutions/best-practices"
module: "score_claude_architecture, audit_remediation_patch, verify_agentica_root_hygiene"
problem_type: "best_practice"
component: "tooling"
symptoms:
  - "Architecture grade could read 100 while required verifiers were unbuilt (vacuous perfection)"
  - "Failed autonomous remediation patches were discarded with no adversarial review of what the engine attempted"
  - "The AgenticaOS repo root accumulated unclassified entries with no drift pressure"
root_cause: "design"
resolution_type: "code_fix"
severity: "medium"
related_components:
  - "config/claude_architecture_scorecard.json"
  - "config/agentica_root_hygiene_policy.json"
  - "state/failed_remediation_*.patch artifacts"
tags: [architecture-score, root-hygiene, remediation, order-samurai]
---

# Three 2026-07 mechanisms, one doc

## score_claude_architecture.py — earned, never vacuous

Computes the ~/.claude architecture score from the scorecard config plus live
verifier evidence. A category earns its weight only when EVERY requiredVerifier
is built and passing; a FAIL zeroes the category even if siblings are unbuilt
(never excused as unmeasured); unbuilt-and-not-failing = unmeasured, excluded
from earned/possible. This is the anti-vacuous-100 pattern: the score can only
be as good as what is actually measured.

## audit_remediation_patch.py — review what the engine tried

When an autonomous remediation produces a patch that failed to land or improve
its metric, the patch artifact (`state/failed_remediation_*.patch`) gets an
LLM-assisted adversarial audit instead of silent disposal — what did the engine
try, why did it not work, is the reflex mis-designed? Feeds the sensei loop's
structural-defect escalations.

## verify_agentica_root_hygiene.py — the third hygiene surface

Same policy-as-code pattern as the Order Samurai and ~/.claude root verifiers,
targeting the AgenticaOS repo root. Unclassified top-level entries WARN (drift
pressure); missing required entries FAIL. File entries may be fnmatch globs so
a single active `HANDOFF-*.md` never carries a standing WARN. Gotcha: the
required-entry check (e.g. `STATE.md`) can only pass in the LIVE repo — bare
worktree checkouts lack gitignored runtime files, so this verifier's test is
environment-coupled by design.
