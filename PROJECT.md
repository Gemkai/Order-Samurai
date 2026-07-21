# Project: Order Samurai

**Objective**: A deterministic, security-focused counterpart to Chaos Monkey. Order Samurai enforces repository integrity, architectural excellence, and vibe alignment across all IDE namespaces.

---

## The Four Pillars

### 1. The Way of the Bow (Operational Status & Agent Activity)

**Focus**: Precision, monitoring, and operational activity.

- Real-time telemetry, task error rates, latencies, and tool call volume.
- Zero-drift enforcement, reaped processes, and daemon restart events.
- Predictive self-monitoring and liveness checks via the Unified Doctor.

### 2. The Way of the Sword (Security Integrity)

**Focus**: Threat protection, quarantine, and code security.

- Real-time secret scrubbing at terminal boundaries (`verify_secrets`).
- Blocking of out-of-scope paths, boundary violations, and exfiltration attempts.
- Automated vulnerability audits, canary liveness, and supply-chain safety scans.

### 3. The Way of the Brush (Architecture Optimization & Token Efficiency)

**Focus**: Code health, modular design, and token economy.

- Token spend, cost per task, model tier mix, and API budget efficiency.
- Code hygiene (revision ratios, path authority, and top-level root sprawl).
- Subagent orchestration chain depth, fan-out, and routing accuracy.

### 4. The Way of the Cultural Arts (UX, Docs, & Vibe Alignment)

**Focus**: Copy refinement, documentation parity, and UX polish.

- Real-time document/runtime parity and documentation latency tracking.
- Interactive UX telemetry (frustration signals, rework loops, and simplify runs).
- Copywriting, commit hygiene, and AI-generated "slop" checks (Anti-Slop Vibe Score).

---

## Execution Strategy

1. **Human-in-the-loop (Current)**: Guided by Antigravity's specialized Security Hardening Ninja vibe pack.
2. **Autonomous Ronins (Future)**: Background agents that enforce Bushido pillars without manual intervention. (See [Autonomous Ronins Capabilities](Research/autonomous_ronins.md))

## Control Plane Artifacts

- `config/architecture_scorecard.json`: Weighted rubric for architecture quality and release gating.
- `config/anti_drift_policy.json`: Executable anti-drift contract for path authority, generated truth, and doc parity.
- `config/anti_sprawl_policy.json`: Executable anti-sprawl contract for surface governance, root hygiene, and archive isolation.
- `config/root_hygiene_policy.json`: Classification of top-level root entries plus archive-boundary scan rules.
- `config/promotion_policy.json`: Runtime promotion and retirement gate for tools and scripts.
- `config/claude_architecture_scorecard.json`: Claude-runtime-specific weighted contract for the audited live control plane under `~/.claude`.
- `config/claude_anti_drift_policy.json`: Claude-runtime anti-drift contract for generated config, path authority, runtime coupling, and doctor truthfulness.
- `config/claude_anti_sprawl_policy.json`: Claude-runtime anti-sprawl contract for surface roles, boundary isolation, and optional capability activation.
- `config/claude_root_hygiene_policy.json`: Top-level classification and boundary rules for the Claude home root.
- `config/claude_promotion_policy.json`: Promotion and compatibility gate for Claude runtime assets.
- `config/claude_surface_matrix.json`: Declared role, owner, and discoverability contract for major Claude runtime surfaces.
- `execution/verify_path_authority.py`: Verifier for hardcoded repo-local or machine-local path drift in the live execution surface.
- `execution/verify_runtime_contract.py`: Verifier for required runtime artifacts, canonical path containment, and doctor-entrypoint coherence.
- `execution/doctor.py`: Operator entrypoint for path authority, runtime contract, root hygiene, and archive-boundary enforcement.
- `execution/runtime_paths.py`: Canonical path constants (repo root, config, artifacts, policies) shared by every execution module — the single anti-drift authority for where things live.
- `execution/sync_inventory.py`: Generated-truth producer — classifies every root entry against the hygiene policy and writes `artifacts/inventory.json` (deterministic, disk-derived; answers "what exists").
- `execution/sync_capability_manifest.py`: Generated-truth producer — emits `config/hub_capability_manifest.json`, the discoverable live/support surface list consumed by `verify_registry_truth.py`.
- `agentica_core/telemetry.py`: Canonical telemetry record schema — defines all optional fields that emitters populate and aggregators consume. Never invents values; absent fields stay None.
- `agentica_core/aggregate.py`: Metric aggregator — REGISTRY of live reducers (one per LIVE metric), `compute_metric` public API, and `load_telemetry_records` loader. The single source of truth for what is actually measurable now.
- `backlog/verifier_backlog.md`: Ordered implementation plan for turning these policies into real verifiers.
- `backlog/claude_verifier_backlog.md`: Ordered implementation plan for making the Claude enforcement pack executable from Order Samurai.

## Day-Zero Bushido Rules

- **No Hidden Paths**: Live runtime code must never hardcode machine-local or repo-local absolute paths.
- **Generated Truth First**: Anything that answers "what exists?" must be generated.
- **Surface Ownership**: No new surface without a role, owner, and discoverability decision.
- **Archive Isolation**: Runtime code must not depend on scratch, archive, playground, or exploratory roots.
- **Docs Move With Runtime**: Architecture docs and runtime contracts must evolve in the same change.

---

### Last Updated: 2026-06-06