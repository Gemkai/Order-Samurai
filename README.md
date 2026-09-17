# Order Samurai

A deterministic governance and security layer for repositories worked on by autonomous coding
agents. Policy lives in `config/` as executable JSON contracts; one verifier in `execution/`
enforces each contract; `order-samurai audit` runs the repository-policy set as a CI gate and
`execution/doctor.py` runs the workstation-health set.

`PROJECT.md` and `RONIN_SPEC.md` describe what the system is for and how it is meant to behave.
This file describes how to run it.

## Requires a checkout

**`audit` needs the `config/` contracts, and `pip install` does not ship them.** Installing the
package gives you the verifiers but not the policy they enforce, so run the audit from a checkout:

```bash
cd <order-samurai-checkout>
python3 -m execution.cli audit
```

Run from an install instead and the command exits 2 and tells you this, rather than reporting a
verdict on its own site-packages directory. That refusal is deliberate. Shipping `config/` as
package data would clear the error and make the path-authority check report a clean bill of health
across "the Governance code surface" while actually scanning site-packages — a false pass from a
security tool, which is worse than no answer. Packaging the contracts is only safe together with
target resolution (auditing the repository the operator means), never before it.

`order-samurai --help` and `order-samurai version` work from an install.

## Commands

| Command | What it does |
|---|---|
| `python3 -m execution.cli audit` | Repository-policy verifiers. Exit 1 on any FAIL, 2 if it cannot run |
| `python3 -m execution.cli audit --format json` | Same, machine-readable |
| `python3 -m execution.cli audit --warn-as-error` | Stricter gate: WARN also exits non-zero |
| `python3 execution/doctor.py` | Workstation health — daemons, telemetry, local LLM, exec-chain |
| `python3 execution/score_architecture.py` | Architecture score |
| `python3 -m pytest tests/ -q -m "not live_machine"` | The portable test suite |

`audit` deliberately excludes runtime-health checks (telemetry freshness, local-LLM liveness, daemon
state, live-source payloads). Those describe a live workstation and would fail in a clean CI
checkout for reasons that say nothing about the code under review. They are `doctor`'s job.

Tests marked `live_machine` assert against local services and real launchd state; the profile above
excludes them. Run them only on a host that has those services.

## Audit profiles

Root hygiene asserts a different tier depending on which tree it is looking at, because some
requirements are universal and others are this project's own conventions:

| Tier | Applies to | Requires |
|---|---|---|
| `full` | A nested Agentica checkout — the development repo | `backlog/`, `config/`, `execution/`, `reports/`, `tests/`, `PROJECT.md`, `RONIN_SPEC.md` |
| `baseline` | A standalone distribution | `config/`, `execution/` — without these there is no policy to enforce and nothing to enforce it |

The tier is **derived from the layout**, so the development repo gets the strict tier without anyone
setting anything. Override with `ORDER_SAMURAI_AUDIT_PROFILE=full|baseline`. Every verifier prints
its active profile, so a run at the lenient tier is never mistaken for a strict one.

## Layout

| Path | Contents |
|---|---|
| `config/` | Executable policy contracts. `X.json` governs the repository; `claude_X.json` governs a `~/.claude` runtime — separate surfaces, not duplicates |
| `execution/` | One `verify_*.py` per contract; `doctor.py` aggregates them |
| `bin/` | Operational scripts (triage, audits, scrubbing, installer) |
| `schema/` | JSON Schemas for the agent-output contracts |
| `state/` | Machine-written runtime truth. Read freely; never hand-edit an event log |
| `tests/` | The suite |

## Exit codes

`0` clean · `1` findings · `2` the command could not run (usage error, or policy contracts absent).
A gate that cannot distinguish "clean" from "could not check" is not a gate, so these never collapse.

## Recent Major Advancements

- **Retirement of Manual Meditation Control**: Faults no longer pause the engine for manual operator clicking. The engine uses **Autonomous Ronin Repairs** running in disposable, isolated Git worktrees with regression checks.
- **Touch ID Hardware Authorization**: Operator policy adjustments and high-blast-radius operations require Darwin LocalAuthentication authorization.
- **Fail-Closed Bushido Security**: `BUSHIDO_FAIL_OPEN=false`, `REFLEX_REQUIRE_GRANT=true` enforced deterministically.
- **9 Live Knowledge Retrieval Measurements**: Real-time tracking of embedding cache hit rates, p50/p95 latency, collection search failures, and prompt cache reuse across Claude and Codex.

## Commercial Tiering (Strictly 2 Tiers — Zero Subscriptions)

1. **Free Core ($0 Forever)**: 100% fail-closed ATT&CK kill-chain interception, in-memory secret scrubbing, 4-pillar diagnostics, 7-day log history, and **manual staged `.patch` generation**.
2. **Pro Lifetime ($199 One-Time / Perpetual)**: **Autonomous Ronin auto-apply** in isolated Git worktrees, Touch ID hardware authorization (LocalAuthentication), Sensei multi-model rival verification, 9-point deep knowledge telemetry & prompt-cache tracking, 90-day archive, and cryptographic SHA-256 hash-chain ledger.
