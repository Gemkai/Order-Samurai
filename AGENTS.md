---
name: agents
description: Canonical always-loaded operating manual for any AI agent working in this repo. Read this first. Project identity, non-negotiables, commands, and layout. CLAUDE.md points here.
last_updated: 2026-07-21
---

# Order Samurai

## What This Is

A local-first governance and security layer for autonomous coding-agent fleets. It wraps
agent runtimes (such as Claude Code) with fail-closed security hooks — prompt-injection
interception, real-time secret scrubbing — and aggregates local telemetry into four
business-pillar metrics (SWORD / BOW / BRUSH / ARTS) served by a React dashboard. All
state lives on this machine under `~/.samurai/`; nothing is sent to any vendor.

## Tier state (Free vs Pro)

The install's tier is a runtime fact of this machine, not a build variant:

- Entitlement source of truth: `~/.samurai/license.json`, read by
  `agentica_core/licensing.py` (Python) and `api/src/licensing.ts` (dashboard API). Both
  fail closed — a missing, malformed, inactive, or refunded entitlement means Free.
- `samurai license` and `samurai doctor` report the current tier. `samurai activate <key>`
  validates a key online once (Lemon Squeezy), then the entitlement works offline
  perpetually; `samurai deactivate` reverts to Free.
- On Free: `bin/dojo_overnight.sh` and `bin/ronin-daemon.sh` exit with code 2 and an
  upgrade notice (shared gate: `bin/lib_pro_gate.sh`), and reflex remediation always
  stages patches for human review in `state/pending_remediation_*.patch`, regardless of
  env vars.
- On Pro: autonomous auto-apply requires BOTH `REFLEX_AUTO_APPLY=true` AND the valid
  license, evaluated per call (activation needs no restart).
- `SAMURAI_PRO_OVERRIDE=1` is a maintainer/CI escape hatch only.

## Non-Negotiables

- Security gates fail closed. Never flip a gate to fail-open (`BUSHIDO_FAIL_OPEN` stays
  false) and never soften a block into a warning.
- Licensing fails closed to Free. Never bypass the license readers or persist entitlement
  anywhere other than `~/.samurai/license.json`.
- Local-first: no code, prompts, or telemetry leaves the machine. The only sanctioned
  network call is the one-time license activation.
- Honesty invariant: every metric is labelled **MEASURED** or **SIMULATED**. Never present
  simulated data as measured.
- No blended pillar scores: pillar status is a worst-tier rollup — a hard FAIL is never
  averaged away.
- Never commit secrets, license keys, or anything from `~/.samurai/`.

## Commands

- Python engine tests: `python3 -m pytest` (from repo root)
- API server: `cd api && npm run dev` · tests: `npm run test:run` · typecheck: `npx tsc --noEmit`
- Dashboard UI: `cd dashboard-ui && npm run dev` (http://localhost:5173) · build:
  `npm run build` · tests: `npm run test:run` · lint: `npm run lint`
- CLI: `./bin/samurai {install,doctor,uninstall,activate,license,deactivate}`
- Shell script syntax check: `bash -n bin/*.sh`

## Layout

- `agentica_core/` — Python governance engine (metric aggregation, licensing, providers)
- `api/` — TypeScript dashboard API server (loopback-bound by default)
- `dashboard-ui/` — React + Vite dashboard and landing page
- `bin/` — CLI, security hooks, and daemons · `config/` — policy files ·
  `schema/` — payload schema · `tests/` — Python test suite
- `docs/solutions/` — documented solutions to past problems (bugs, best practices,
  workflow patterns), organized by category with YAML frontmatter (`module`, `tags`,
  `problem_type`). Relevant when implementing or debugging in documented areas.
- User-facing docs: `README.md`, `docs/ONBOARDING.md` (Free + Pro walkthrough),
  `docs/HONESTY_TABLE.md`, `SECURITY.md`, `TERMS.md` / `PRIVACY.md` / `EULA.md`.
