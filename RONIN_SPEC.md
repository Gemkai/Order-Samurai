# Order Samurai: Ronin Agent Specification

**Definition**: A Ronin is an autonomous, masterless agent designed to enforce the Order Samurai Bushido across all IDE namespaces and repositories.

---

## Core Architecture

### 1. The All-Seeing Eye (Namespace Capture)

- Captures context across all open IDE windows and terminal sessions
- Synchronizes with the Order Samurai Dashboard for real-time telemetry

### 2. The Bushido Engine (Decision Logic)

- Evaluates every action against the four pillars: Bow, Sword, Brush, and Arts
- Rejects any action that introduces dishonorable drift, such as secrets in logs or messy code paths
- Consumes policy and scorecard artifacts from `config/` so architectural judgment is executable rather than purely narrative

### 3. The Sword-Scrub Module (Execution)

- Proactively identifies and redacts sensitive data
- Automates dependency hardening and vulnerability patching

---

## Deployment Tiers

- **Tier 1: Human-in-the-loop**: Ronin suggests changes via Antigravity and a human approves.
- **Tier 2: Autonomous Guardian**: Ronin performs background maintenance, such as secret scrubbing and linting, automatically.
- **Tier 3: Shogun Protocol**: Fully autonomous agentic swarm managing complex repository refactors.

---

## Safety And Integrity

- **Non-Destructive**: Ronins never delete data without a `.ronin_backup`.
- **Transparent**: All Ronin actions are logged to `artifacts/ronin_logs.md`.
- **Immutable Core**: Ronin logic is defined in `directives/` and cannot be modified by the agent itself.

## Control Plane

- `config/architecture_scorecard.json` defines the weighted architecture contract.
- `config/anti_drift_policy.json` defines path authority, generated truth, and verification discipline.
- `config/anti_sprawl_policy.json` defines surface governance, root hygiene, and archive isolation.
- `config/root_hygiene_policy.json` defines allowed top-level root entries and archive-boundary scan rules.
- `config/promotion_policy.json` defines how exploratory assets are promoted into live runtime.
- `config/claude_architecture_scorecard.json` defines the Claude-runtime-specific weighted architecture contract.
- `config/claude_anti_drift_policy.json` defines Claude-runtime path authority, generated truth, runtime coupling, and doctor truthfulness.
- `config/claude_anti_sprawl_policy.json` defines Claude-runtime surface governance, root hygiene, and boundary isolation.
- `config/claude_root_hygiene_policy.json` defines allowed top-level Claude-home entries and forbidden runtime boundary crossings.
- `config/claude_promotion_policy.json` defines how Claude runtime assets are promoted into live or compatibility planes.
- `config/claude_surface_matrix.json` defines the major Claude surfaces, their roles, and their discoverability contracts.
- `execution/verify_path_authority.py` enforces the single-path-authority rule on the live execution surface.
- `execution/verify_runtime_contract.py` enforces required runtime artifacts, canonical path containment, and doctor-entrypoint coherence.
- `execution/doctor.py` aggregates the active enforcement passes into one operator-facing health command.
- `backlog/verifier_backlog.md` defines the implementation order for executable enforcement.
- `backlog/claude_verifier_backlog.md` defines the implementation order for executable governance against the Claude runtime.

---

## Terminology

The dojo has three nested units of work. Naming them precisely prevents the drift that
makes "run the cycle" / "the sweep" / "the session" ambiguous.

- **Keiko** (稽古) — *one bounded run of the dojo*: a `run_id` with a start and deadline that
  executes a series of cycles (e.g. `bin/dojo_overnight.sh`, the ~6-hour batch of up to 60
  cycles, or a shorter manual sitting). "Keiko" is the dojo-native word for a training session.
  **Do not call this a "session"** — that term is reserved for telemetry/work sessions
  (`Session_Count`, `Avg_Session_Turns`, Claude/Codex/Antigravity work sessions) and would collide.
- **Cycle** — *one iteration inside a keiko*: one ronin, one pillar, one work-unit, advanced through
  Steps B→F of `prompts/dojo_cycle.md`. Tracked by the `cycle` counter in `state/DOJO_STATE.json`.
  The dashboard **RUN button triggers a single cycle**, not a keiko.
- **Work-unit** — *one backlog item* (`kind` ∈ stream/field/scout/skill) a cycle advances
  `todo → doing → done`. A completed, timestamped work-unit is one calibration sample for the
  Est. Agent Hours Saved coefficients (`state/calibration_coefficients.json`).

**Two triggers, opposite jobs.** *Auto-remediation* fires a cycle **reactively** when a pillar's
`live_current < live_baseline` — it defends the floor (fix regressions). The **RUN button** fires a
cycle **proactively** regardless of regression — it raises the ceiling (work the backlog, accumulate
calibration samples). When all pillars sit at/above baseline, auto is silent and the manual trigger
is the only thing that runs.

---

### Last Updated: 2026-06-19