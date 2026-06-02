# Claude Architecture Hardening Report

Date: `2026-04-12`
Primary runtime audited: `C:\Users\jemak\.claude`
Report destination rationale: requested archival in `C:\Users\jemak\Desktop\Projects\Order Samurai\reports`

## Executive Summary

This report documents the architecture-hardening work performed against Claude's home runtime, the root causes behind the drift and fragility that accumulated there, and the discipline, artifacts, and policies that should have existed from the beginning to avoid needing this remediation.

The core result is that Claude's audited live runtime/control-plane surface is now effectively green and architecture-complete for that scope. The live runtime now has:
- generated `settings.json`
- generated `mcp.json`
- a canonical path authority
- canonical registries for hooks and MCP servers
- generated runtime inventory and summary artifacts
- invariant enforcement for runtime coupling, drift, and canonical docs
- a truthful doctor surface that reports effective runtime state rather than misleading raw counts
- no remaining live runtime dependency on Antigravity-owned paths

Verification at the end of the work showed:
- `53 passed` in `C:\Users\jemak\.claude\tests`
- `sync_settings_config.py --check` passed
- `sync_mcp_config.py --check` passed
- `sync_runtime_inventory.py --check` passed
- `verify_claude_home.py` passed
- `doctor.py` passed
- `execution/doctor.py` passed

Within the audited runtime/control-plane scope, Claude is now effectively at `100/100` architecture score. Outside that scope, I did not claim the same score for plugin caches, archival artifacts, or cold-storage skill content because the local evidence did not support a stronger statement.

## Scope and Constraints

This effort was explicitly done as a Claude-native audit, not a symmetry exercise with Antigravity. Antigravity was used only as a comparison point when local Claude evidence independently justified the same architectural move.

The work focused on these dimensions:
- architectural coherence
- runtime safety and enforcement
- portability and path discipline
- source-of-truth hygiene
- inventory and discoverability
- observability and operator ergonomics
- documentation parity
- lifecycle governance
- anti-drift risk
- anti-sprawl risk

This effort did **not** deeply re-architect:
- Claude memory / learning loop internals
- plugin cache content
- historical archive surfaces
- every cold-storage skill in `skill-source/`

## What I Changed

### 1. Established runtime-generated truth

Added and enforced generated runtime artifacts:
- `C:\Users\jemak\.claude\data\runtime_inventory.json`
- `C:\Users\jemak\.claude\data\runtime_summary.md`
- generator: `C:\Users\jemak\.claude\scripts\sync_runtime_inventory.py`

Why this mattered:
- Claude previously had runtime facts spread across mutable config, docs, and assumptions.
- The runtime needed a single generated truth surface that could be checked and regenerated.

Outcome:
- Runtime facts are now derived, not estimated.
- Doctor and invariants consume generated truth instead of freehand logic.

### 2. Created a canonical path authority

Added:
- `C:\Users\jemak\.claude\scripts\runtime_paths.py`

Then rewired core runtime scripts to use it, including:
- `scripts\doctor.py`
- `scripts\sync_runtime_inventory.py`
- `scripts\verify_claude_home.py`
- `scripts\launch_mcp_server.py`
- `scripts\env_bootstrap.py`
- `scripts\sync_capability_registry.py`
- `execution\doctor.py`

Why this mattered:
- Runtime path derivation was duplicated across the system.
- Repetition caused portability debt and made policy enforcement inconsistent.

Outcome:
- Claude now has one authoritative runtime root model.
- Path-sensitive logic is centralized and testable.

### 3. Canonicalized the doctor surface

Canonical entrypoint:
- `C:\Users\jemak\.claude\scripts\doctor.py`

Compatibility shim retained:
- `C:\Users\jemak\.claude\execution\doctor.py`

Related docs updated:
- `C:\Users\jemak\.claude\commands\doctor.md`
- `C:\Users\jemak\.claude\directives\mcp-server-inventory.md`

Why this mattered:
- There was control-plane ambiguity around where runtime truth and health checks should originate.
- A health command is governance infrastructure; it cannot be ambiguous.

Outcome:
- Claude now has one canonical runtime doctor surface.
- Legacy entrypoints still work without remaining canonical.

### 4. Moved all MCP servers behind the local launcher

Added:
- `C:\Users\jemak\.claude\scripts\mcp_server_registry.py`
- `C:\Users\jemak\.claude\scripts\sync_mcp_config.py`

Reworked:
- `C:\Users\jemak\.claude\scripts\launch_mcp_server.py`
- `C:\Users\jemak\.claude\mcp.json`

Why this mattered:
- Claude's MCP surface had inconsistent launcher behavior.
- Some servers were bypassing the local launcher contract entirely.
- That created portability debt and fractured control-plane enforcement.

Outcome:
- All `24` MCP servers are now launcher-backed.
- `mcp.json` is generated from a canonical registry.
- Doctor and invariants enforce the launcher-backed contract.

### 5. Replaced handwritten hook runtime with a generated hook control plane

Added:
- `C:\Users\jemak\.claude\scripts\runtime_entrypoints.py`
- `C:\Users\jemak\.claude\scripts\hook_registry.py`
- `C:\Users\jemak\.claude\scripts\hook_dispatch.py`
- `C:\Users\jemak\.claude\scripts\sync_settings_config.py`
- `C:\Users\jemak\.claude\scripts\community_hook_common.py`

Added portable hook implementations:
- `C:\Users\jemak\.claude\scripts\gsd_session_state.py`
- `C:\Users\jemak\.claude\scripts\gsd_validate_commit.py`
- `C:\Users\jemak\.claude\scripts\gsd_phase_boundary.py`

Externalized inline hook logic into durable files:
- `C:\Users\jemak\.claude\hooks\gsd-skill-touch.js`
- `C:\Users\jemak\.claude\hooks\gsd-read-track.js`

Rewrote:
- `C:\Users\jemak\.claude\settings.json`

Why this mattered:
- The live hook surface was one of Claude's highest-leverage remaining weaknesses.
- It had pinned home paths and Windows-hostile `bash` dependencies.

Outcome:
- `settings.json` is now generated.
- Hook commands are dispatcher-backed.
- The runtime summary now reports:
  - `0` commands pinned to current Claude home
  - `0` commands requiring `bash`

### 6. Removed the last live Antigravity runtime coupling

Fixed live runtime coupling in:
- `C:\Users\jemak\.claude\skills\ship\SKILL.md`

Added regression coverage in:
- `C:\Users\jemak\.claude\tests\test_claude_home_invariants.py`

Why this mattered:
- The runtime invariant layer correctly found a surviving live dependency on an Antigravity-owned path.
- That meant Claude was still not fully Claude-native at runtime, even after the larger MCP and hook cleanups.

Outcome:
- `verify_claude_home.py` now passes the runtime coupling scan.
- Claude's live runtime no longer references Antigravity-owned paths in the audited control-plane surface.

### 7. Made optional MCP capability truthful instead of merely warned-about

Adjusted env-gated activation in:
- `C:\Users\jemak\.claude\scripts\mcp_server_registry.py`

Regenerated:
- `C:\Users\jemak\.claude\mcp.json`
- `C:\Users\jemak\.claude\data\runtime_inventory.json`
- `C:\Users\jemak\.claude\data\runtime_summary.md`

Why this mattered:
- `browserbase` had become an example of ambiguous runtime state: installed, enabled, but unusable without credentials.
- Warning forever is weaker than representing the real state correctly.

Outcome:
- Optional servers are now disabled until required env is present.
- Doctor no longer reports an enabled-but-broken MCP env surface for `browserbase`.

### 8. Made doctor truthful about LLM gateway reachability

Adjusted:
- `C:\Users\jemak\.claude\scripts\doctor.py`

Grounded against real gateway behavior in:
- `C:\Users\jemak\.claude\llm\gateway.py`

Why this mattered:
- The old doctor treated missing `ANTHROPIC_API_KEY` as raw debt even though Claude's gateway already routes Anthropic-prefixed models through OpenRouter when needed.
- That was an operator-truthfulness problem.

Outcome:
- Doctor now reports effective gateway lanes instead of simplistic key counts.
- Current doctor output reports:
  - `anthropic-via-openrouter`
  - `openrouter`
  - `gemini`

## End-State Evidence

Final generated runtime evidence from `runtime_summary.md`:
- hook events: `5`
- hook commands: `25`
- shells: `python x 25`
- commands pinned to current Claude home: `0`
- commands requiring `bash`: `0`
- MCP servers total: `24`
- enabled: `14`
- disabled: `10`
- launcher-backed: `24`
- launcher-capable but direct: `0`
- direct Claude-home entrypoints: `0`
- canonical doctor entrypoint: `scripts/doctor.py`
- legacy doc references: `0`

Final doctor result:
- all checks passed

Final invariant result:
- all invariants satisfied

Final test result:
- `53 passed`

## Root Cause Analysis

### Immediate Cause

Claude's runtime had evolved as a collection of edited operational files rather than as a governed control plane.

That meant:
- mutable files like `settings.json` and `mcp.json` accumulated as handwritten truth
- docs duplicated runtime facts instead of pointing to generated truth
- shell/runtime choices were made opportunistically rather than via explicit portability policy
- optional integrations remained "configured" even when they were not actually operable
- cross-runtime contamination from imported or adapted skill content survived without invariant detection until late

### Structural Root Causes

#### 1. Runtime was treated as configuration, not architecture

The biggest cause was conceptual.

Claude's home runtime is not just a pile of config files. It is a small platform. Once a system has:
- hooks
- launchers
- MCP processes
- env activation
- doctor/verification commands
- inventories
- docs that operators rely on

it has become architecture, not just configuration.

Because it was not treated that way early enough, the runtime accreted local optimizations that were individually reasonable but globally destabilizing.

#### 2. No one-way generation discipline existed for live runtime surfaces

`settings.json` and `mcp.json` remained editable operational truth for too long.

That allowed:
- path pinning
- shell drift
- launcher inconsistency
- invisible config debt
- docs and verifiers lagging behind live behavior

If live surfaces are handwritten, drift is not an accident. It is the default outcome.

#### 3. Pathing policy was implicit instead of enforced

Claude lacked a strong early rule that said:
- no live runtime surface may embed host-specific home paths
- path resolution belongs to a single authority module
- operator-facing config must use portable entrypoints, not machine-local concrete paths

Without that, home-path pinning became normal.

#### 4. Optional capability activation had no lifecycle contract

Optional servers and integrations were allowed to exist in a half-live state:
- installed
- declared
- sometimes enabled
- but not actually usable without credentials

That creates operator ambiguity and doctor-warning creep.

A capability must be one of:
- enabled and healthy
- disabled by policy
- absent

Anything else is architecture debt.

#### 5. Docs were allowed to mirror mutable runtime facts

Handwritten inventory docs always rot if they attempt to restate changing runtime truth.

That happened here because docs were used partly as explanation and partly as inventory.

Those are different jobs.

The right split is:
- generated artifacts hold mutable truth
- docs explain how to read and operate the truth

#### 6. Cross-runtime contamination lacked a hard invariant

Claude and Antigravity had related ecosystems, shared mental models, and adjacent skills.

That made contamination likely:
- copied skill text
- runtime references to sibling systems
- assumptions about upstream path layouts

This was not mainly a "bad copy/paste" problem. It was a missing-boundary problem.

If two neighboring runtimes are both live systems, cross-runtime references must be actively forbidden unless explicitly allowlisted as legacy bridges.

#### 7. Health checks measured the wrong thing

A health system that measures raw configuration rather than effective runtime behavior will either:
- miss breakage
- overstate breakage
- or train operators to ignore it

Claude had both forms at different times:
- some real runtime debt was hidden or clipped
- some residual warnings overstated true operational risk

That is an observability design problem, not just a message problem.

## Why This Happened in Practice

If I compress the RCA to one sentence, it is this:

**Claude's runtime grew by accretion faster than its governance model grew by design.**

The runtime had enough power to need:
- registries
- generators
- invariants
- portability policy
- doc discipline
- activation policy
- coupling boundaries

before those things were formalized.

That is why the fixes that mattered most were not cosmetic cleanup. They were control-plane moves.

## If Starting Over: What Should Have Been Put in Place on Day 0

Below is the discipline, artifact set, and policy set I would recommend if rebuilding the Claude runtime from scratch and aiming for `100/100` architecture scoring without this retrofit.

## Recommended Discipline

### 1. Treat the Claude home as a productized runtime

Rule:
- `~/.claude` is a runtime platform, not a loose config directory.

Implication:
- every live operational file must have a control-plane owner
- every mutable runtime surface must be either generated or explicitly declared canonical
- every operator-facing diagnostic must be treated like production infrastructure

### 2. Runtime truth must flow one way

Rule:
- registries -> generators -> live config -> inventories -> docs
- never the reverse

Implication:
- operators do not hand-edit `settings.json` or `mcp.json`
- docs do not carry mutable counts or handwritten runtime inventories
- live config is rendered from canonical registries

### 3. Every portability property must be machine-testable

Rule:
- do not rely on "we should avoid absolute paths" as a social norm

Implication:
- path discipline is enforced by generated inventories and invariants
- shell portability is tested
- cross-runtime coupling is scanned
- launcher discipline is validated structurally

### 4. Optional capability must be explicit, never ambiguous

Rule:
- every optional integration must declare activation conditions

Implication:
- if required env vars are absent, the capability is disabled
- doctor should not have to nag forever about intentionally-unused capabilities
- active capability count should reflect actual availability

### 5. Every major runtime batch must close the loop

Required batch closeout:
1. implement
2. verify
3. regenerate artifacts
4. update docs
5. rescore / record remaining risk

Without this, architecture drifts between code, config, docs, and operator belief.

## Recommended Day-0 Artifacts

If starting over, I would require these artifacts immediately.

### Core control-plane artifacts

1. `scripts/runtime_paths.py`
- single authority for all runtime-owned paths

2. `scripts/hook_registry.py`
- canonical declaration of hook ids, dispatch targets, shells, activation, and policy metadata

3. `scripts/sync_settings_config.py`
- renderer for `settings.json`

4. `scripts/mcp_server_registry.py`
- canonical declaration of MCP servers, runtimes, env requirements, disabled defaults, and launcher metadata

5. `scripts/sync_mcp_config.py`
- renderer for `mcp.json`

6. `scripts/launch_mcp_server.py`
- single launcher abstraction for all eligible MCP servers

7. `scripts/runtime_entrypoints.py`
- portable entrypoint helpers so home path and execution semantics stay centralized

### Governance artifacts

8. `scripts/verify_claude_home.py`
- invariant gate for generated drift, coupling, doc references, and runtime governance

9. `scripts/doctor.py`
- canonical operator health surface

10. `data/runtime_inventory.json`
- machine-readable generated runtime truth

11. `data/runtime_summary.md`
- operator-readable generated summary

12. `artifacts/architecture-scorecard.md`
- explicit scoring and residual risk ledger

### Compatibility / transition artifact

13. `execution/doctor.py`
- compatibility shim only if legacy entrypoints already exist

### Test artifacts

14. runtime governance tests covering:
- path authority
- generator drift detection
- launcher contract
- hook contract
- runtime coupling scan
- doctor signal quality
- live-home invariants

## Recommended Policies

### Policy 1. No handwritten live runtime config

Applies to:
- `settings.json`
- `mcp.json`

Rule:
- direct edits are forbidden
- only registries and generators may change them

### Policy 2. No absolute home paths in generated runtime entrypoints

Rule:
- live commands may resolve the home dynamically at runtime
- they may not embed the current machine's home path directly

### Policy 3. No `bash`-only hooks in a Windows-targeted runtime

Rule:
- if the runtime is intended to be healthy on Windows, live hook entrypoints must be Python, Node, or PowerShell safe
- `bash` may exist for developer utilities, not for required live hook paths

### Policy 4. No live cross-runtime references

Rule:
- no live runtime file may reference sibling systems such as Antigravity paths
- exception only through an explicit allowlist for legacy bridge surfaces

### Policy 5. Docs may explain runtime truth but may not impersonate it

Rule:
- mutable counts, inventories, and runtime state belong in generated artifacts
- docs link to them and explain their meaning

### Policy 6. Optional integrations must declare activation env

Rule:
- every optional server must state its required env vars
- the generator disables it automatically when requirements are absent

### Policy 7. Doctor must report effective runtime state, not just raw configuration

Rule:
- health checks should answer "what actually works right now?"
- not merely "which variables exist?"

### Policy 8. Every runtime-facing change needs a governance test

Rule:
- if a change affects hooks, launchers, pathing, env activation, or docs, it must come with either:
  - a new runtime governance test
  - or an updated existing one

### Policy 9. Every architectural batch must leave behind a durable score artifact

Rule:
- after meaningful runtime work, update a scorecard or equivalent artifact that records:
  - what changed
  - what was verified
  - what remains risky
  - what is inferred versus directly observed

## What Would Have Prevented Almost All of This

If I had to choose the smallest set of early decisions that would have prevented most of the remediation effort, it would be this five-item package:

1. `runtime_paths.py` on day 0
2. generated `settings.json` from a hook registry
3. generated `mcp.json` from an MCP registry
4. a strict `verify_claude_home.py` invariant gate in CI / release workflow
5. a ban on live runtime references to sibling systems

That alone would likely have prevented:
- pinned home-path debt
- `bash` hook fragility
- launcher inconsistency
- doc drift
- cross-runtime contamination
- enabled-but-broken optional MCP capability

## What Would Be Required for a True 100/100 From the Start

To reliably score `100/100` from the beginning, I would require that Claude satisfy all of the following before declaring the architecture done:

### Control plane
- all live runtime surfaces are registry-backed or otherwise canonically owned
- no duplicate canonical entrypoints
- one path authority exists and is used everywhere

### Portability
- zero absolute home-path commands in live config
- zero shell-only assumptions for required runtime flows on the target platform
- all launcher-eligible servers go through the launcher

### Truthfulness
- doctor reports effective runtime state
- docs do not restate mutable generated truth
- optional capabilities are either truly active or truly disabled

### Governance
- generated drift checks exist for all live config surfaces
- invariant scan covers cross-runtime coupling
- every major runtime claim is backed by a file or generated artifact

### Operator clarity
- one doctor command
- one invariant command
- clear generated runtime inventory
- no chronic warning noise in healthy operation

## Remaining Non-Blocking Scope Gaps

The runtime/control-plane surface is clean, but these areas remain outside the same depth of hardening in this pass:
- plugin caches
- historical backups and file-history artifacts
- cold-storage `skill-source/` documentation and examples
- deep memory / learning loop architecture

Those are not live-runtime blockers. They are simply outside the exact scope that was taken to green here.

## Final Recommendation

If you want to preserve this `100/100` runtime score over time, the operational rule should be simple:

**Do not let Claude's live runtime be edited as prose or convenience config ever again. Treat it as generated infrastructure with invariant enforcement.**

That single discipline is the difference between a runtime that stays clean and a runtime that slowly turns back into a pile of exceptions.
