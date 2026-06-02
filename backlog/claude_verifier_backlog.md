# Claude Runtime Verifier Backlog

This backlog turns the Claude hardening report into an executable enforcement pack against `C:\Users\jemak\.claude`.

## Objective

Make Order Samurai able to score, gate, and continuously re-verify Claude's runtime architecture without relying on one-off manual audits.

## Target Runtime

- Runtime root: `C:\Users\jemak\.claude`
- Source report: `reports/2026-04-12-claude-architecture-hardening-report.md`
- Scorecard: `config/claude_architecture_scorecard.json`
- Policy set:
  - `config/claude_anti_drift_policy.json`
  - `config/claude_anti_sprawl_policy.json`
  - `config/claude_root_hygiene_policy.json`
  - `config/claude_promotion_policy.json`
  - `config/claude_surface_matrix.json`

## P0: Foundational External-Runtime Verifiers

### 1. `execution/claude_runtime_target.py`

Purpose:
Centralize the Claude runtime root, policy paths, scorecard path, and report path so every Claude verifier uses the same target contract.

Acceptance criteria:
- one canonical target module for `C:\Users\jemak\.claude`
- all Claude verifiers import it rather than re-declaring target paths
- no side effects on import

### 2. `execution/verify_claude_path_authority.py`

Purpose:
Fail when live Claude runtime code hardcodes Claude-home or Antigravity-owned absolute paths outside approved bridge rules.

Depends on:
- `config/claude_anti_drift_policy.json`

Acceptance criteria:
- scans only approved live-runtime surfaces
- catches backslash and forward-slash literal forms
- distinguishes sibling-runtime coupling from acceptable local references

### 3. `execution/verify_claude_hook_contract.py`

Purpose:
Validate that `settings.json` is generated from the hook registry and that live hook entrypoints remain portable.

Depends on:
- `config/claude_anti_drift_policy.json`
- `config/claude_promotion_policy.json`

Acceptance criteria:
- verifies `scripts/hook_registry.py` and `scripts/sync_settings_config.py` exist
- checks `settings.json` is generator-aligned
- checks live hook commands avoid pinned Claude-home paths and `bash` dependence

### 4. `execution/verify_claude_mcp_contract.py`

Purpose:
Validate that `mcp.json` is generated from the launcher-backed registry and that optional servers use explicit activation policy.

Depends on:
- `config/claude_anti_drift_policy.json`
- `config/claude_anti_sprawl_policy.json`

Acceptance criteria:
- verifies all launcher-capable servers are launcher-backed
- verifies optional servers with required env are disabled when activation env is absent
- distinguishes disabled-by-policy from enabled-but-broken

### 5. `execution/verify_claude_generated_truth.py`

Purpose:
Verify that runtime truth artifacts are fresh and remain the authority for existence questions.

Depends on:
- `config/claude_anti_drift_policy.json`

Acceptance criteria:
- checks freshness and structural validity of `settings.json`, `mcp.json`, `runtime_inventory.json`, and `runtime_summary.md`
- fails if handwritten docs attempt to impersonate generated runtime inventory
- emits deterministic `OK/WARN/FAIL` output

### 6. `execution/verify_claude_runtime_contract.py`

Purpose:
Aggregate the foundational Claude runtime checks into one Order Samurai verifier that can score or gate the runtime without shelling into ad hoc manual review.

Depends on:
- `config/claude_architecture_scorecard.json`
- `config/claude_anti_drift_policy.json`

Acceptance criteria:
- validates required runtime artifacts exist
- shells out to Claude's doctor and invariant entrypoints or faithfully reproduces their contract
- emits human-readable and machine-parseable results

## P1: Anti-Sprawl And Boundary Verifiers

### 7. `execution/verify_claude_surface_governance.py`

Purpose:
Validate that every major Claude surface has a declared role, owner, and discoverability contract.

Depends on:
- `config/claude_surface_matrix.json`

Acceptance criteria:
- required surfaces must exist
- surface roles must be valid
- compatibility surfaces must point to canonical owners rather than competing with them

### 8. `execution/verify_claude_root_hygiene.py`

Purpose:
Warn on unclassified Claude-home root entries and validate the root hygiene policy against the actual `~/.claude` layout.

Depends on:
- `config/claude_root_hygiene_policy.json`

Acceptance criteria:
- required directories and files must exist
- unclassified top-level entries produce warnings
- output distinguishes runtime, source, state, dependency, support, and archive findings

### 9. `execution/verify_claude_runtime_coupling.py`

Purpose:
Fail when live Claude runtime surfaces reference Antigravity paths, backup roots, or other forbidden historical surfaces.

Depends on:
- `config/claude_root_hygiene_policy.json`
- `config/claude_anti_drift_policy.json`

Acceptance criteria:
- approved scan paths are configurable
- forbidden patterns and forbidden roots are configurable
- offenders are reported with path plus violating pattern/root

### 10. `execution/verify_claude_promotion_policy.py`

Purpose:
Ensure new Claude runtime assets satisfy the promotion checklist before entering live runtime, operator, or compatibility planes.

Depends on:
- `config/claude_promotion_policy.json`
- `config/claude_surface_matrix.json`

Acceptance criteria:
- owner and purpose are required
- generated-config integration is required when applicable
- doctor/invariant visibility is required
- optional capability activation metadata is required when applicable

## P2: Truthfulness, Docs, And Operator Signal

### 11. `execution/verify_claude_doctor_truthfulness.py`

Purpose:
Check that Claude's doctor output reflects effective runtime state rather than stale or misleading raw counts.

Depends on:
- `config/claude_anti_drift_policy.json`

Acceptance criteria:
- doctor reports effective gateway lanes
- disabled-by-policy optional servers do not surface as broken active infrastructure
- canonical doctor and compatibility shim produce equivalent high-level status

### 12. `execution/verify_claude_doc_parity.py`

Purpose:
Catch changes to runtime contracts that land without the corresponding Claude human-readable docs and Order Samurai report pack being updated.

Depends on:
- `config/claude_architecture_scorecard.json`

Acceptance criteria:
- maps runtime surfaces to required docs
- checks `CLAUDE.md`, `AGENTS.md`, `commands/doctor.md`, and `directives/mcp-server-inventory.md`
- checks the Claude hardening report and enforcement-pack docs move in the same batch

## P3: Scoring And Continuous Governance

### 13. `execution/score_claude_architecture.py`

Purpose:
Compute Claude's architecture score from the scorecard plus live verifier evidence.

Depends on:
- `config/claude_architecture_scorecard.json`

Acceptance criteria:
- emits JSON and Markdown score artifacts
- computes category scores from verifier evidence
- distinguishes advisory gaps from blocking failures
- can explain why Claude is below `100/100` with file-backed reasons

### 14. `execution/verify_claude_pack_integrity.py`

Purpose:
Ensure the Claude enforcement pack itself remains coherent as policy JSONs, report, backlog, and docs evolve.

Depends on:
- all Claude pack artifacts in `config/`, `backlog/`, and `reports/`

Acceptance criteria:
- no scorecard references missing pack artifacts
- no policy references missing verifiers
- docs point to the current Claude pack, not just the generic or Antigravity pack

## Implementation Order

1. `claude_runtime_target.py`
2. `verify_claude_path_authority.py`
3. `verify_claude_hook_contract.py`
4. `verify_claude_mcp_contract.py`
5. `verify_claude_generated_truth.py`
6. `verify_claude_runtime_contract.py`
7. `verify_claude_surface_governance.py`
8. `verify_claude_root_hygiene.py`
9. `verify_claude_runtime_coupling.py`
10. `verify_claude_promotion_policy.py`
11. `verify_claude_doctor_truthfulness.py`
12. `verify_claude_doc_parity.py`
13. `score_claude_architecture.py`
14. `verify_claude_pack_integrity.py`

## Definition Of Done

The Claude enforcement pack is considered operational when:

- the Claude-specific policy JSONs are consumed by code rather than only read by humans
- Order Samurai can score Claude from live verifier evidence
- Order Samurai can explain every lost point with file-backed reasons
- Claude runtime regressions can be caught the same day they are introduced
- Claude's `100/100` score becomes a continuously enforced condition rather than a one-time manual judgment
