# Antigravity Anti-Drift / Anti-Sprawl Report

Date: 2026-04-12
Author: Codex
Target system: `C:\Users\jemak\.gemini\antigravity`

## Executive Summary

This report documents the anti-drift and anti-sprawl remediation work performed on Antigravity, why the architecture drifted into this state, and what should have been put in place from day zero to avoid needing this cleanup.

At the start of this sequence, Antigravity had strong ambition and many good architectural instincts, but the runtime and control plane were not fully aligned. The main issues were:

- live runtime scripts still hardcoded machine-local hub paths
- registry and inventory artifacts had overlapping but incomplete responsibilities
- capability surfaces existed without one explicit governance model
- the hub root had grown broad without a machine-checkable classification policy
- exploratory or one-off logic had leaked into the live orchestration plane

The remediation program fixed those issues in four connected batches:

1. runtime path hardening and invariant verification
2. canonical skill inventory and registry validation
3. surface governance and ecosystem-wide capability discovery
4. root hygiene and archive-boundary enforcement

The result is a system that is materially more deterministic, portable, discoverable, and governable.

## Final Outcome

### Architecture Score

- Starting score: `83/100`
- Final score: `96/100`
- Net improvement: `+13`

### Key Quantified Gains

- Major governed hub surfaces: `0 -> 11`
- Classified top-level root entries: `0 -> 71`
- Passing invariant checks: `5 -> 12`
- Focused regression tests added and passing: `0 -> 18`
- Hardcoded live hub-root references on scanned runtime surface: `>0 -> 0`
- Live runtime references to archive/exploratory roots: `2 -> 0`
- Discoverable capability coverage: `279 skill entries -> 783 total indexed capabilities`
- Capability index breakdown:
  - `284` skills
  - `173` directives
  - `24` workflows
  - `165` agents
  - `137` subagents

### Current Health State

The final invariant pass reported:

- `OK=12`
- `WARN=0`
- `FAIL=0`

The final doctor pass was also clean.

## What Was Done

## Batch 1: Runtime Hardening And Anti-Drift Enforcement

### Intent

Stop live runtime drift by making the active execution plane use one canonical path model and by adding an explicit architecture verifier.

### Changes

- Added `execution/verify_hub_invariants.py`
- Added focused verifier tests in `tests/test_verify_hub_invariants.py`
- Normalized live runtime files to use `execution/runtime_paths.py`
- Updated `execution/doctor.py` to invoke the invariant gate
- Removed remaining hardcoded hub-root usage from active runtime paths under:
  - `execution/`
  - `orchestration/`
  - `llm/`
  - `tools/`
  - `global_skills/superpowers-workflow/scripts/`

### Why This Mattered

Before this batch, the architecture had doctrine about deterministic behavior, but no reliable runtime proof that the live surface was coherent. This batch turned portability and path discipline from a preference into an enforceable contract.

## Batch 2: Canonical Skill Inventory

### Intent

Separate three different questions that had been blended together:

- what skills exist on disk
- what skills are installed or deployed
- what the registry says projects use or care about

### Changes

- Added `execution/sync_skill_inventory.py`
- Generated `global_skills/skill-sync/skill_inventory.json`
- Extended `verify_hub_invariants.py` so:
  - the inventory must parse and resolve
  - `skills-lock.json` must resolve against the inventory
  - `skill_registry.json` must resolve against the inventory
  - stale registry project paths are surfaced separately
- Updated `global_skills/skill-sync/SKILL.md` to document the three-layer model

### Why This Mattered

The core metadata problem was not just “bad data.” It was role confusion. The system did not have one canonical answer to “what skills are real right now?” The generated inventory solved that by grounding validation in disk reality.

## Batch 3: Surface Governance And Capability Discovery

### Intent

Reduce ecosystem sprawl by explicitly naming the major hub surfaces and building one ecosystem-wide capability discovery layer.

### Changes

- Added `config/hub_surface_matrix.json`
- Added `execution/sync_hub_capability_manifest.py`
- Generated `config/hub_capability_manifest.json`
- Added `execution/find_capability.py`
- Extended `verify_hub_invariants.py` to validate:
  - surface ownership matrix
  - allowed surface roles
  - discoverable surface resolution
  - capability manifest path validity
- Updated `PROJECT.md` and `AGENTS.md` to reflect the governance model

### Why This Mattered

Antigravity had become a broad system with many valid surfaces:

- `global_skills`
- `skills`
- `skill-source`
- `directives`
- `global_workflows`
- `agents`
- `subagents`

Without a machine-readable ownership model, capability discovery became tribal knowledge. This batch created a formal control plane for that breadth.

## Batch 4: Root Hygiene And Archive Boundary Enforcement

### Intent

Finish the anti-sprawl work by classifying the hub root itself and preventing live runtime logic from depending on archive or exploratory surfaces.

### Changes

- Added `config/root_hygiene_policy.json`
- Added root-hygiene validation logic to `verify_hub_invariants.py`
- Added:
  - unclassified root entry detection
  - archive-boundary scanning
  - required-vs-allowed root policy support
- Refactored `orchestration/minimax_repo_audit.py`
  - removed hardcoded dependency on `scratch/Test/infographic-app`
  - replaced with explicit CLI arguments and environment-driven target selection
- Refactored `orchestration/test_app_pilot.py`
  - removed hardcoded dependency on `scratch/Test/infographic-app`
  - replaced with explicit CLI arguments and environment-driven target selection
- Updated `PROJECT.md` and `AGENTS.md` to reflect root hygiene and archive boundary rules

### Why This Mattered

By this point, Antigravity had a cleaner runtime and a better discovery model, but the top-level root still behaved like an unmanaged ecosystem. This batch made the root legible and governable.

## Root Cause Analysis

The anti-drift / anti-sprawl work was necessary because multiple architectural forces accumulated at the same time.

## Root Cause 1: Growth Outpaced Governance

Antigravity expanded capability much faster than it expanded control-plane artifacts.

Symptoms:

- more surfaces were added
- more scripts entered the runtime plane
- more registries appeared
- more one-off operational needs were solved locally

But governance did not grow at the same rate. The result was a platform with strong ideas and weak normalization.

### Why This Happens

Fast-moving agent systems often optimize first for usefulness:

- add the skill
- add the script
- add the agent
- add the report
- add the workflow

That is rational in the short term. The failure mode is that usefulness compounds faster than structure unless the structure is designed to scale ahead of demand.

## Root Cause 2: No Single Executable Control Plane

The system had good architectural beliefs in prose, but too much of that guidance remained documentary rather than executable.

Examples:

- path discipline was recommended but not universally enforced
- registries existed but did not answer distinct questions cleanly
- operators could infer where things lived, but the system could not prove it

This is the core anti-drift lesson:

> doctrine without a verifier is advisory, not architectural

## Root Cause 3: Missing Artifact Separation

Several kinds of truth needed to exist separately from day one:

- path truth
- inventory truth
- deployment truth
- logical registry truth
- capability discovery truth
- root classification truth

Instead, parts of those truths were merged, partially duplicated, or absent. That created ambiguity around:

- what is source
- what is runtime
- what is installed
- what is discoverable
- what is archival

Once those questions blur, drift becomes structurally likely.

## Root Cause 4: Promotion Without Intake Gates

Exploratory or one-off scripts were allowed to live too close to the live orchestration plane without a hard promotion checklist.

That is why runtime code ended up depending on `scratch/` targets. The problem was not just a bad path; it was a missing lifecycle boundary.

The lifecycle should have been:

1. experiment in exploratory space
2. decide whether the tool is worth keeping
3. promote it into runtime only if it passes governance requirements
4. archive the exploratory version

Instead, the exploratory and runtime worlds partially overlapped.

## Root Cause 5: Root-Level Entropy Was Unmeasured

The hub root contained many valid things, but no formal accounting of what those things were.

Without a root policy:

- adding another top-level directory is cheap
- naming drift is cheap
- classification drift is invisible
- archive and live surfaces become visually adjacent

That makes the system harder to reason about even when nothing is technically broken.

## Root Cause 6: Health Checks Existed, But Architectural Checks Were Incomplete

There were already useful health-oriented tools, but they were not yet sufficient as architectural guardians.

The missing checks were exactly the ones architecture scoring depends on:

- source-of-truth discipline
- discoverability discipline
- live-vs-archive separation
- root classification
- machine-verifiable surface governance

## Why This Was Not Just “Messiness”

This was not primarily a code cleanliness problem.

It was a systems-governance problem:

- too many real surfaces
- too little explicit ownership metadata
- too few lifecycle gates
- not enough generated truth

That distinction matters. If the issue had been treated as “clean up some files,” the real problem would have returned.

## If Starting Over: What Should Have Been In Place On Day Zero

If the goal were to avoid this remediation entirely and target a `100/100` architecture score from the start, I would recommend putting the following non-negotiables in place before the platform reached meaningful scale.

## Day-Zero Discipline

### 1. One Runtime Path Authority

Create a single canonical module for all path construction on day one.

Requirements:

- no absolute machine-local literals in live logic
- every hub-local path is built from one path module
- env overrides are explicit and typed
- portability is validated in CI and by a local doctor command

### 2. Generated Truth Over Hand-Maintained Truth

Any artifact that answers “what exists?” should be generated.

That includes:

- skill inventory
- capability manifest
- root classification snapshots where possible

Hand-maintained files should answer policy or intent questions, not reality questions.

### 3. Every Surface Must Have One Job

No surface should exist without a single sentence that answers:

- what it is for
- whether it is source, runtime, registry, operator, state, support, dependency, or archive
- whether it is discoverable
- whether live code may depend on it

### 4. Promotion Gates For Runtime Code

No script should be allowed into `execution/`, `orchestration/`, `llm/`, or equivalent live planes unless it passes a promotion checklist.

Minimum checklist:

- explicit inputs and outputs
- canonical path usage
- no dependency on archive or exploratory roots
- tests or smoke verification
- documented owner and purpose
- inclusion in verifier coverage where relevant

### 5. Live And Exploratory Worlds Must Be Structurally Separate

Exploration is healthy. Runtime contamination is not.

Use a strict lifecycle:

- `scratch/` or `playground/` for experiments
- `candidate/` or reviewed PR flow for promotion
- live runtime only after passing the gate
- `archive/` for retired material

Do not let live orchestration depend directly on exploratory roots.

### 6. Root Hygiene Must Be A First-Class Policy

The hub root should be treated like a governed namespace, not a casual directory.

Every top-level entry should be:

- classified
- either required or optional
- either live, state, support, dependency, or archive
- visible to the verifier

### 7. The Architecture Must Score Itself Continuously

A `100/100` architecture score should not be a one-time judgment call. It should be continuously approximated by a scorecard with machine-readable criteria.

Recommended categories:

- portability
- runtime coherence
- inventory truth
- discoverability
- governance coverage
- archive isolation
- documentation parity
- verification depth

## Day-Zero Artifacts I Would Have Required

These artifacts should exist almost immediately in any system intended to scale like Antigravity.

### Required Control-Plane Artifacts

1. `execution/runtime_paths.py`
   - one source of truth for hub-local paths

2. `config/hub_surface_matrix.json`
   - what each major surface is for

3. `config/root_hygiene_policy.json`
   - what root entries are allowed and how they are classified

4. `global_skills/skill-sync/skill_inventory.json`
   - generated inventory of actual skill assets

5. `config/hub_capability_manifest.json`
   - generated index of discoverable capabilities

6. `skills-lock.json`
   - deployment/install truth only

7. `global_skills/skill-sync/skill_registry.json`
   - logical/project metadata only

8. `execution/verify_hub_invariants.py`
   - architecture gate

9. `execution/doctor.py`
   - operator entrypoint for architecture health

10. `docs/architecture/scorecard.md` or `config/architecture_scorecard.json`
   - explicit scoring rubric and current status

### Recommended Lifecycle Artifacts

11. `config/promotion_policy.json`
   - rules for moving experimental scripts into runtime

12. `config/deprecation_policy.json`
   - how things leave the live system

13. `reports/architecture-drift-log.md`
   - running record of drift incidents and fixes

14. `docs/architecture/surface-map.md`
   - human-readable overview generated or maintained alongside the surface matrix

## Day-Zero Policies I Would Have Enforced

## Policy Group A: Pathing

- No hardcoded absolute paths in live runtime logic
- All hub-local paths must resolve from the canonical path module
- Any new runtime path constant must be added in one place only

## Policy Group B: Inventory And Registry

- Generated artifacts answer existence questions
- Manual artifacts answer intent questions
- No registry may be considered authoritative unless a verifier can prove it against disk

## Policy Group C: Surface Governance

- No new top-level surface without classification
- No discoverable surface without inclusion in the surface matrix
- No discoverable capability without inclusion in the generated capability manifest

## Policy Group D: Runtime Promotion

- Exploratory scripts may not enter the live runtime plane without:
  - canonical pathing
  - smoke verification
  - classification
  - owner
  - documented purpose

## Policy Group E: Archive Isolation

- Live runtime code may not depend on:
  - `archive/`
  - `scratch/`
  - `playground/`
  - other exploratory or historical roots

## Policy Group F: Verification

Every change touching architecture must run:

- invariant verifier
- doctor
- compile or syntax checks
- targeted tests
- path and boundary sweeps when relevant

## Policy Group G: Documentation Parity

- Docs describing the architecture must be updated in the same change as the runtime contract
- If the machine-verifiable architecture and the prose architecture disagree, the change is incomplete

## What A True 100/100 Architecture Would Require

Antigravity is now strong, but a literal `100/100` requires more than the work already done.

To truly hit that standard, I would require all of the following to hold simultaneously:

### 1. Zero Hidden Ownership Ambiguity

Every surface, artifact, and registry has a single owner and a single defined purpose.

### 2. Zero Runtime Dependence On Exploratory History

No live code reads from archive or scratch space.

### 3. Full Control-Plane Closure

The following questions can all be answered deterministically:

- what exists
- where it lives
- why it exists
- whether it is live
- whether it is discoverable
- who owns it
- how it is validated
- how it is retired

### 4. Promotion And Retirement Are Both Governed

It is not enough to govern how things are added. A 100/100 system also governs:

- deprecation
- archiving
- migration
- replacement

### 5. Scoring Is Operational, Not Narrative

Architecture quality should be continuously computed from verifiable facts, not periodically argued from memory.

## Recommended 100/100 Scoring Rubric

If starting over, I would score architecture using a live rubric like this:

- Portability and path discipline: `15`
- Runtime coherence and invariant coverage: `15`
- Inventory and registry truth separation: `15`
- Surface governance and discoverability: `15`
- Root hygiene and archive isolation: `15`
- Promotion/deprecation lifecycle rigor: `10`
- Documentation parity with runtime reality: `10`
- Operator ergonomics and health tooling: `5`

Total: `100`

Antigravity now scores highly because it is strong in most of these categories, but it still falls short of perfection mainly because lifecycle governance is not yet as formalized as the rest of the control plane.

## What Order Samurai Should Take From This

Order Samurai’s stated mission is to enforce repository integrity and architectural excellence across namespaces. The Antigravity remediation suggests Order Samurai should treat the following as first-class enforcement domains:

1. Path normalization
2. Source-of-truth separation
3. Surface governance
4. Root hygiene
5. Archive boundary enforcement
6. Promotion policy for runtime code
7. Architecture scorecards as executable policy

If Order Samurai bakes those in as baseline governance rather than cleanup work, it can prevent the exact class of drift and sprawl that required this intervention.

## Final Assessment

The anti-drift and anti-sprawl work was not cosmetic. It converted Antigravity from a broad but partially self-describing ecosystem into a platform with a real, machine-checkable control plane.

The most important architectural lesson is this:

> drift and sprawl do not primarily come from bad developers or messy code; they come from systems that scale capability faster than they scale explicit governance

That is why the correct prevention strategy is not “be cleaner.” It is:

- define the control plane early
- separate truth artifacts by role
- gate runtime promotion
- classify the root
- verify everything continuously

With those disciplines in place from the start, most of this remediation would never have been necessary.
