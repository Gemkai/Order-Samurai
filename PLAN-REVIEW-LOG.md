# Plan Review Log: Order Samurai — Aggregate Metrics Rethink + Kill Chain Security Layer

Act 1 (grill) complete — plan locked with the user. MAX_ROUNDS=5.

## Grill summary

12 questions resolved across all key dimensions:

| Q | Dimension | Decision |
|---|-----------|----------|
| Q1 | Data ownership | System-generated immutable sources only |
| Q2 | Coefficient method | Empirical calibration (B) — 20 samples / 4 weeks |
| Q3 | Calibration display | Industry benchmark placeholder, visually distinct, tooltip |
| Q4 | Task type taxonomy | Backlog `kind` field (stream/field/scout/skill) for Operations |
| Q5 | Architecture cost | Actual spend (budget_ledger.json) + routing delta at Anthropic list price |
| Q6 | Operations timing | Add `started_at` to backlog items; calibration window before real coefficients |
| Q7 | Kill chain scope | Build full ATT&CK 14-chain taxonomy now, not two-phase |
| Q8 | Cyber kill chain model | Lockheed Martin 7-phase + MITRE ATT&CK technique mapping |
| Q9 | Chain 13 detection | Pattern-based + gemma-4-e4b semantic scoring, log-not-block for semantic |
| Q10 | Chain 14 detection | Extend secret_scrubber_realtime PostToolUse to message results |
| Q11 | Feedback loop | Weekly discovery scout + gemma-4-e4b proposals + human review gate |
| Q12 | Craft coefficients | 4 signals, documented industry benchmarks, empirically replaced |

## Act 2 — Local Model Adversarial Review (gemma-4-e4b)

**Note:** Codex CLI blocked (402 deactivated_workspace — free ChatGPT account). Used local `google/gemma-4-e4b` via LM Studio as substitute reviewer.

### Round 1 — gemma-4-e4b findings

FLAW 1: Race condition on concurrent JSONL appends from hooks
FLAW 2: No error handling for corrupted JSONL records in reducers
FLAW 3: No cryptographic signing on PostToolUse messages
FLAW 4: Invalid datetime strings in started_at not validated
FLAW 5: No alert when routing-efficient event stream goes silent
FLAW 6: Taxonomy file needs API/transaction layer for updates
FLAW 7: MITRE technique IDs need external versioned lookup

VERDICT: REVISE

### Claude's response — Round 1

Changed:
- Step 2: added atomic append requirement (temp+rename) for JSONL writers
- Step 6: added per-line try/except with skip+log for malformed records
- Step 8: added stale-data guard (`data_gap: true`) when event stream goes silent
- Step 5: added ISO-8601 validation and explicit exclusion from calibration count

Rejected:
- Flaw 3 (cryptographic signing): wrong threat model — local Python script appending to local file, not a message bus; local write access = full compromise regardless
- Flaw 6 (API/transaction layer): overengineered for single JSON file; human review IS the transaction boundary; JSON schema validation on read is sufficient
- Flaw 7 (external MITRE lookup): ATT&CK technique IDs are stable identifiers; hardcoded in versioned JSON is correct pattern; version field handles future updates

### Round 2 — gemma-4-e4b findings

FLAW 1: Plan truncated mid-sentence (artifact of context trim, not real)
FLAW 2: Concurrent writes to DOJO_STATE / proposed_kill_chains
FLAW 3: Missing-week delta misleading
FLAW 4: Source file unavailability → silent crash

VERDICT: REVISE

### Claude's response — Round 2

Changed:
- Added reducer contract to Phase B: all reducers catch FileNotFoundError/IOError, return {val: None, error: "source unavailable"}

Rejected:
- Flaw 1: artifact of 6000-char context truncation, not a real plan flaw
- Flaw 2: reducers are read-only; only single-threaded scouts write; no concurrent-write scenario
- Flaw 3: zero disruptions is a valid reading; stale-data guard in Step 8 already covers source failure

### Round 3 — gemma-4-e4b findings

FLAW 1: Metric poisoning via compromised logging agent
FLAW 2: Detection evasion via resource exhaustion / supply chain integrity

VERDICT: REVISE (model did not finish — cut off)

### Claude's response — Round 3 (CONVERGENCE)

Rejected all Round 3 flaws: same threat model error as rejected Round 1 Flaw 3. These are enterprise SIEM / distributed system concerns. Order Samurai is a local single-machine tool. Compromising local JSONL files requires host access = full compromise already.

**FINAL VERDICT: APPROVED** (by Claude as final arbiter — 3 rounds, material issues resolved)

What the review improved:
1. Atomic JSONL appends — prevent torn records under concurrent hook fires
2. Per-reducer error handling contract — graceful degradation vs. crash on source unavailability
3. Datetime validation + calibration exclusion for pre-plan items
