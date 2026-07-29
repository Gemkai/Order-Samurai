---
title: "Kill-chain discovery scout clusters security telemetry and proposes chains local-only (fail-closed)"
date: "2026-07-08"
category: "docs/solutions/security-issues"
module: "kill_chain_discovery_scout"
problem_type: "security_issue"
component: "tooling"
symptoms:
  - "Unmatched security events accumulate with no mechanism turning them into proposed kill chains"
  - "Risk of routing alert-cluster content (this machine's security telemetry) to a cloud LLM backend"
root_cause: "design"
resolution_type: "code_fix"
severity: "medium"
related_components:
  - "Order Samurai state/kill_chain_unmatched.jsonl"
  - "state/proposed_kill_chains.json"
  - "agentica_core model_router (local Ollama tier)"
tags: [kill-chain, security-telemetry, local-llm, fail-closed, privacy, order-samurai]
---

# Kill-chain discovery scout — local-only by construction

`kill_chain_discovery_scout.py` is the weekly discovery pass that reads unmatched security events, clusters them, and proposes new kill chains for the ReflexEngine to act on. Its defining constraint is **where the reasoning runs**.

## Why local-only, fail-closed

The clusters it reasons over *are* security telemetry from this machine — unmatched guard hits, suspicious tool sequences, boundary events. Sending that content to a cloud LLM would exfiltrate the very signal the system exists to protect. So the scout routes **only** through the local Ollama tier and is **fail-closed**: if the local model is unreachable, it does not silently fall back to a cloud backend — it declines to propose rather than leak. This mirrors the `local_only=True` sensitive-prompt routing policy in `agentica_core`.

## Dataflow

`state/kill_chain_unmatched.jsonl` (producer: the injection/boundary guards) → this scout clusters and proposes → `state/proposed_kill_chains.json` (consumed by the ReflexEngine / sensei-cycle). Keeping the unmatched log clean matters: when it fills with `Clean`-scan noise the scout proposes nothing useful (see `kill-chain-unmatched-clean-noise-2026-07-03.md`), so signal-to-noise in the producer is a prerequisite for this consumer to work.

## The rule

Any scout that reasons over security or otherwise-sensitive local telemetry must pin its model tier to local and fail closed — never degrade to a cloud tier on local-model unavailability. Availability is not worth a privacy-boundary breach.
