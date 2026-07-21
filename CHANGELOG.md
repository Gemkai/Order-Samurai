# Changelog

All notable changes to **Order Samurai** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-07-19

### Fixed — metric integrity (2026-07-19 sync from upstream)
- **No blended pillar scores anywhere**: pillar status is a worst-tier rollup
  (passing/graded counts) — a hard FAIL can never be averaged away. Radar axes
  and per-project scores are pass rates.
- **Fire-time remediation efficacy**: the engine records each metric's live value
  before and after every autonomous run; the efficacy panel counts every attempt
  (including no_change/error/timeout) instead of rendering silence.
- **Alarm quality**: metrics marked non-remediable no longer generate remediation
  reflex cards; correlation gates normalize per-session metrics before comparing
  thresholds (a raw-count bug fired a cost reflex whose own gate failed).
- **Honest thresholds**: bimodal-by-workload metrics (e.g. Avg_Session_Turns)
  opt out of percentile calibration; simulated metrics collapse by default in the
  dashboard until their emitter produces real data.
- **Ship the payload schema**: a gitignore pattern for generated payloads also
  swallowed `schema/wid_payload.schema.json` — fresh clones failed 13 schema
  tests and API startup validation. Found by the release clone-and-run test.
- Dashboard `ThresholdSparkline` had an undefined gradient id that only surfaced
  on cache-free builds (stale tsbuildinfo masked it in dev).

### Security
- **Autonomous patch-apply is OFF by default** (`REFLEX_AUTO_APPLY`, opt in with `=true`): a
  code-modifying remediation that passes the maker-checker audit + pytest gate is now saved to
  `state/pending_remediation_*.patch` for human review instead of being applied to the live
  repo, so a fresh clone never rewrites a working tree unattended. The audit + pytest gate run
  identically either way.
- **Daemon validate-command hardening**: `bin/ronin-daemon.sh` reads `DOJO_VALIDATE_CMD` from
  the environment inside Python rather than interpolating it into the source string, closing a
  code-injection path where a quote in the value could break out of the literal.
- **Removed dead secondary-model fallback**: the `runFallback` branch that spawned an absent
  `execute_remediation_gemini.py` is gone from the API server and reflex engine (Claude-only
  build). A CLI quota limit is now surfaced directly instead of erroring on a missing script.
- **API server binds to loopback by default** (`127.0.0.1`, override with `DOJO_BIND_HOST`):
  the dashboard API can spawn an auto-editing agent, so it must never be reachable off-host.
  Previously it bound to all interfaces (`0.0.0.0`).
- **WebSocket `/ws` now enforces an `Origin` allow-list**: the agent-spawning `{type:'exec'}`
  channel rejects connections from any origin outside the dashboard allow-list (browsers apply
  no CORS to WebSocket upgrades, so this is its only cross-origin gate).
- **State-mutating REST routes are localhost + same-origin gated** (`unstick`, `unstick-all`,
  `cancel`, `ronin/toggle`, `dojo/run`), mirroring the existing `/api/reflex/verdicts` gate —
  closes a CSRF / off-host path to disabling loop-breaker safety and forcing remediation runs.
- **Remediation patch filenames fully sanitized** so a reflex id can never escape `state/`.

### Fixed
- **`bin/emit_event.py` standalone resolution**: it hardcoded a developer-machine directory
  layout and, on a fresh clone, silently wrote telemetry to a per-machine `~/Desktop/...` path.
  It now resolves the repo root from its own location like the other `bin/` scripts.

### Added
- **Reflex fire-path verify-gate (`REFLEX_VERIFY_GATE`, default on)**: before spawning an
  expensive code-modifying remediation skill for a batch metric, re-measures the breach live
  (`bin/remeasure_gate.py`) and suppresses the spawn if the metric already recovered — closing
  a fail-open gap where a stale/phantom breach spent a full skill run. Fail-open on any gate error.
- **Overnight batch-defer routing (`REFLEX_BATCH_WINDOW`, default off)**: holds non-urgent,
  code-modifying remediations for a configured overnight window (verify real-time, improve
  overnight); the metric re-fires via the normal poll when the window opens.

### Added
- **14 ATT&CK Kill-Chain Security Taxonomy**: Full security detection and interception layer mapping agent execution vulnerabilities.
- **Prompt Injection Guard (Chain 13)**: Real-time PreToolUse hook intercepting role manipulation and system prompt override attacks.
- **Exfiltration & Secret Scrubber (Chain 14)**: PostToolUse hook scanning stdout, UNC paths, connection strings, and RFC1918 internal IPs.
- **4 Business Pillar Aggregates**:
  - `Kill_Chains_Disrupted` (SWORD) - Resilience count of intercepted attack vectors.
  - `Estimated_Agent_Time_Saved` (BOW) - Operation efficiency metrics from verified runtimes.
  - `Estimated_Cost_Savings` (BRUSH) - Real token expenditure & model routing delta.
  - `Estimated_Human_Time_Saved` (ARTS) - Craft productivity & documentation parity.
- **Honesty Invariant**: Explicit `MEASURED` vs `SIMULATED` calibration badge rendering across all UI and CLI surfaces.
- **One-Command Installer CLI (`bin/samurai`)**:
  - `samurai install`: Safe merge with write-through settings backup (`~/.claude/hooks/settings.json`).
  - `samurai doctor`: Complete environment, gate posture, and secret scrubber verification.
  - `samurai uninstall`: Zero-residue restoration of original developer settings.
- **Web Dashboard & Product Landing Page**: Integrated UI surface with interactive metrics, pricing calculator, and self-serve checkout interface.
