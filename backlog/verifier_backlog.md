# Order Samurai Verifier Backlog

This backlog turns the enforcement pack into executable architecture governance.

## Objective

Build the verifier stack required to make the scorecard and policy JSONs operational rather than aspirational.

## P0: Foundational Verifiers

### 1. `execution/runtime_paths.py`

Purpose:
Establish one canonical path authority for the repo before any other verifier work begins.

Acceptance criteria:
- all repo-local path construction resolves through this module
- env overrides are explicit and documented
- runtime code can import it without side effects

### 2. `execution/verify_path_authority.py`

Purpose:
Fail when live runtime code hardcodes repo-local or machine-local absolute paths.

Acceptance criteria:
- scans approved live runtime surfaces only
- catches backslash and forward-slash literal forms
- emits deterministic `OK/WARN/FAIL` results

### 3. `execution/verify_runtime_contract.py`

Purpose:
Validate that required runtime artifacts exist and remain internally coherent.

Acceptance criteria:
- required files and directories are declared
- canonical paths stay under the project root
- operator summary is human-readable and machine-parseable

### 4. `execution/doctor.py`

Purpose:
Serve as the operator-facing entrypoint for all blocking architectural health checks.

Acceptance criteria:
- aggregates all current verifiers
- exits non-zero on blocking failures
- prints concise, scannable results

## P1: Anti-Sprawl Verifiers

### 5. `execution/verify_surface_governance.py`

Purpose:
Validate that every major surface has a declared role, owner, and discoverability contract.

Depends on:
- `config/hub_surface_matrix.json`

Acceptance criteria:
- required surfaces must exist
- surface roles must be valid
- discoverable surfaces must resolve on disk

### 6. `execution/verify_root_hygiene.py`

Purpose:
Warn on unclassified top-level entries and validate required-vs-allowed root policy.

Depends on:
- `config/root_hygiene_policy.json`

Acceptance criteria:
- required root entries must exist
- unclassified root entries produce warnings
- output distinguishes state, support, dependency, and archive clutter from runtime failures

### 7. `execution/verify_archive_boundaries.py`

Purpose:
Fail when live runtime code references archive, scratch, playground, or other exploratory roots.

Depends on:
- `config/root_hygiene_policy.json`
- `config/promotion_policy.json`

Acceptance criteria:
- approved scan paths are configurable
- forbidden roots are configurable
- offenders are reported with file path and violating root

## P2: Truth And Discovery Verifiers

### 8. `execution/sync_inventory.py`

Purpose:
Generate factual inventory artifacts for whatever capability surfaces Order Samurai governs.

Acceptance criteria:
- deterministic output
- generated from disk reality
- no manual curation required for existence questions

### 9. `execution/sync_capability_manifest.py`

Purpose:
Generate a discoverability manifest from approved surfaces.

Acceptance criteria:
- path-based identity
- deterministic ordering
- no archive or exploratory surfaces included

### 10. `execution/verify_generated_truth.py`

Purpose:
Verify that generated truth artifacts are fresh and structurally valid.

Acceptance criteria:
- generated artifact missing or stale is surfaced
- existence questions map to generated artifacts only

### 11. `execution/verify_registry_truth.py`

Purpose:
Validate that logical registry artifacts resolve against factual generated truth.

Acceptance criteria:
- registry entries resolve against inventory
- discoverability entries resolve against approved surfaces
- stale project or optional metadata can warn separately from hard failures

## P3: Lifecycle And Parity Verifiers

### 12. `execution/verify_promotion_policy.py`

Purpose:
Ensure anything promoted into runtime passes the promotion checklist.

Acceptance criteria:
- owner and purpose are required
- verification evidence is required
- archive-boundary cleanliness is required

### 13. `execution/verify_doc_parity.py`

Purpose:
Catch architecture changes that update runtime contracts without updating human-readable docs.

Acceptance criteria:
- checks required docs for the changed surface
- supports a declared mapping between runtime areas and docs

### 14. `execution/score_architecture.py`

Purpose:
Compute the architecture score from the scorecard and verifier outputs.

Depends on:
- `config/architecture_scorecard.json`

Acceptance criteria:
- emits JSON and Markdown score artifacts
- computes category scores from verifier evidence
- distinguishes advisory gaps from blocking failures

## Implementation Order

1. `runtime_paths.py`
2. `verify_path_authority.py`
3. `verify_runtime_contract.py`
4. `doctor.py`
5. `verify_surface_governance.py`
6. `verify_root_hygiene.py`
7. `verify_archive_boundaries.py`
8. `sync_inventory.py`
9. `sync_capability_manifest.py`
10. `verify_generated_truth.py`
11. `verify_registry_truth.py`
12. `verify_promotion_policy.py`
13. `verify_doc_parity.py`
14. `score_architecture.py`

## Definition Of Done

The enforcement pack is considered operational when:

- the core policy JSONs are consumed by code rather than only read by humans
- doctor aggregates the blocking verifiers
- architecture score emits from live verifier evidence
- drift and sprawl regressions can be caught in the same day they are introduced
