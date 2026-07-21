---
title: "Injection guard logged Clean scans into the kill-chain alert stream, burying real events"
date: "2026-07-03"
category: "docs/solutions/security-issues"
module: "prompt injection guard / kill chain discovery"
problem_type: "security_issue"
component: "tooling"
symptoms:
  - "kill_chain_unmatched.jsonl at 1,213 events, 99.5% of them 'prompt_injection_guard: Clean'"
  - "Kill-chain discovery scout proposes 0 chains every run despite real suspicious events existing"
  - "Orphan-mechanism audit flags the unmatched log as a producer orphan (written, never usefully consumed)"
root_cause: "logic_error"
resolution_type: "code_fix"
severity: "high"
related_components:
  - "kill_chain_discovery scout"
  - "Order Samurai state"
tags: [prompt-injection, kill-chain, telemetry, signal-to-noise, alert-stream, security-telemetry]
---

# Injection guard logged Clean scans into the kill-chain alert stream, burying real events

## Problem

`~/.claude/hooks/prompt_injection_guard.py` routed **every** scan below 0.7 confidence to
`state/kill_chain_unmatched.jsonl` — including confidence-0.0 "Clean" results, i.e. the
overwhelmingly common no-op outcome of every tool call. 1,207 of 1,213 entries were Clean
noise, burying the 6 real suspicious events and starving the kill-chain discovery scout
(0 proposals on every run). Security telemetry that logs its own all-clear into the alert
stream destroys the stream's purpose.

## Symptoms

- Alert/unmatched stream dominated by "nothing happened" events
- Downstream consumer (discovery scout, filtering `confidence >= 0.5`) finds no signal
- Static dataflow audit reports the file as a producer orphan

## What Didn't Work

- The original single `else:` branch had no discrimination between "scanned clean" and
  "suspicious but unconfirmed" — one log served as both audit trail and alert stream,
  so the audit volume drowned the alerts.

## Solution

Commit `1aef599` (branch `fix/injection-guard-clean-noise`, PR #4 on agentica-claude),
one-line routing change — detection and blocking behavior untouched:

```python
# before
else:
    _append_jsonl(unmatched_log, event_entry)

# after
elif confidence > 0:
    _append_jsonl(unmatched_log, event_entry)
```

Only suspicious-but-unconfirmed events (0 < confidence < 0.7, e.g. the 0.5
"pattern matched but semantic check denied" class) reach the unmatched log. All 7
self-tests pass. After the fix lands on the Windows host, truncate the old file
(preserving the 6 real events if desired) so the scout reads signal.

## Why This Works

An alert stream is only useful if presence-of-entry ≈ needs-attention. Clean outcomes
are the base rate, not an anomaly — if an audit trail of clean scans is ever wanted,
it belongs in a separate high-volume log, never in the stream a detector consumes.

## Prevention

- When a guard/detector writes telemetry, decide per outcome class: alert stream,
  audit log, or nothing. Never default-route the base-rate outcome into the alert path.
- Wire a consumer check: if a security log has no reader (producer orphan in
  `/find-orphan-mechanisms`), either the consumer is missing or the log is noise.
- Watch the ratio: an "unmatched/suspicious" stream where >90% of entries share one
  benign source string is a routing bug, not a threat landscape.

## Related Issues

- `Governance/docs/solutions/logic-errors/ollama-local-call-guards-2026-07-03.md`
  (same session; both are "documented intent not enforced in code" defects)
- Orphan-mechanism audit findings 2026-07-02 (`.planning/overnight/skill-hygiene-report-2026-07-02.md`)
