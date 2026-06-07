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

### Last Updated: 2026-04-12