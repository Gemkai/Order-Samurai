---
title: Plugpass minimal v1 CLI packaging — Phase 2 Plan
date: 2026-08-06
problem_type: feature
component: new standalone repo (packaging of ~/.claude/scripts/{skill_security_audit.py,skill_scanner_target.py})
severity: low
tags: [plugpass, product-expansion, packaging, cli]
status: PROPOSED — awaiting approval, no implementation started
---

# Phase 2 Plan — Plugpass minimal v1 CLI packaging

Follows the approved 2026-08-06 Phase 1 Discovery (inline, this session — not a separate file per
the Discovery template). Builds on the Plugpass P1-A portability slice
(`docs/plans/2026-08-06-plugpass-p1a-portability-plan.md`, shipped this session): the scanning
engine itself needs no logic changes for this plan — `--target`, `CLAUDE_RUNTIME_ROOT`,
`--output-prefix`, and the per-target allowlist are already verified working against a foreign
root. This plan is packaging only.

**Cross-repo note:** this plan creates a **new, separate git repo** at
`~/Desktop/Solutions/Plugpass/` (sibling to, not nested inside, the existing Order Samurai
productization pack at `~/Desktop/Solutions/Order Samurai(product)/` — see Discovery risk log for
why). Nothing in `~/.claude` or `AgenticaOS` is modified by this plan; the new repo's sync
mechanism only *reads* from `~/.claude/scripts/`.

## Scope

In scope for this slice (from the approved Discovery's "Proposed scope"):
1. New packaging repo skeleton at `~/Desktop/Solutions/Plugpass/`.
2. A sync mechanism that vendors the two scanner files from `~/.claude/scripts/` into the new
   repo, keeping `~/.claude` the single source of truth for scanner logic (same pattern
   `extract_public.py` already uses for Order Samurai — a deliberate, re-run-when-needed sync,
   not a live symlink).
3. `pyproject.toml` with a `plugpass` console-script entry point.
4. Standalone-safe default-target resolution (opt-in Claude-specific defaults, not required).
5. `plugpass init <path>` — scaffolds an empty allowlist file.
6. README quickstart.
7. License file — **explicit placeholder**, not a real license (Discovery risk: undecided).
8. A packaging smoke test (fresh-venv install + `--help` + a synthetic-fixture scan).

Explicitly **OUT of scope** (per the approved Discovery — named here so a future session doesn't
assume they shipped):
- Publishing to PyPI or any public announcement.
- Resolving the trademark ("Plugpass" has had zero legal review) or license (undecided even for
  the parent Order Samurai pack) open questions.
- Builder-identity scoring/roll-up, MCP config scanning, quarantine/remediation flow.
- A Claude Code plugin/marketplace package (second distribution channel) — CLI-only.
- Hosted dashboard, multi-seat, SSO.

## Verified constraints (facts checked before writing this plan, not assumed)

- `~/.claude/scripts/skill_security_audit.py` and `skill_scanner_target.py` have zero third-party
  imports (confirmed by grep) — packaging targets pure-stdlib Python.
- `~/.claude`'s `pyproject.toml` is pytest-config-only (`[tool.pytest.ini_options]`), not a package
  definition — no collision risk with a new `pyproject.toml` in the separate Plugpass repo.
- `Governance/Order Samurai/bin/extract_public.py` is the existing scrub/sync precedent this plan's
  M1 step 2 follows (read its EXCLUDE/REGENERATE/SCRUB table approach for the pattern, don't
  duplicate its Order-Samurai-specific literals).
- The Order Samurai productization pack (`~/Desktop/Solutions/Order Samurai(product)/docs/productization/`)
  ratified a freemium CLI precedent (`pipx install order-samurai`, free auditor-core / paid
  remediation) this plan follows for consistency, without re-deciding it.
- Local Python: 3.14.5 installed; plan targets `requires-python = ">=3.10"` as a conservative floor
  (both source files use `from __future__ import annotations` + `X | Y` type hints, no 3.10+-only
  runtime syntax) — verified by grep, not by testing on an actual 3.10 interpreter (out of scope
  to provision one for this plan; flagged as a residual risk, not blocking).

## Milestone 1 — Packaging skeleton + sync mechanism

**Goal:** a new repo exists with the two scanner files vendored in, importable as a package, with
`~/.claude` remaining the single source of truth for scanner logic.

1. Create `~/Desktop/Solutions/Plugpass/` — `git init`, directory skeleton: `plugpass/` (package
   dir, empty `__init__.py`), `tests/`, `bin/`, `pyproject.toml`, `README.md`, `LICENSE`.
   **verify:** `ls ~/Desktop/Solutions/Plugpass/` shows the skeleton; `git -C ~/Desktop/Solutions/Plugpass status` confirms a fresh repo, no parent-repo entanglement (not nested under
   `~/Desktop/Solutions/Order Samurai(product)/`).
2. Write `bin/sync_from_claude.py`: copies `~/.claude/scripts/skill_security_audit.py` →
   `plugpass/scanner.py` and `~/.claude/scripts/skill_scanner_target.py` → `plugpass/target.py`,
   rewriting the single import line (`from skill_scanner_target import runtime_root` →
   `from plugpass.target import runtime_root`) — the only intentional diff between source and
   vendored copy. Script re-run any time the `~/.claude` source changes; not a build-time /
   install-time step (packages need real source present, per the Discovery's stdlib-only
   assumption).
   **verify:** run the script; `diff ~/.claude/scripts/skill_security_audit.py plugpass/scanner.py`
   shows zero differences besides the rewritten import line (and `skill_scanner_target` →
   `target` module name inside the sole `from ... import` statement).
3. `pyproject.toml`: `name = "plugpass"`, `version = "0.1.0"`, `requires-python = ">=3.10"`,
   `[project.scripts] plugpass = "plugpass.scanner:main"`.
   **verify:** in a fresh venv (`python3 -m venv /tmp/plugpass-venv && source
   /tmp/plugpass-venv/bin/activate`), `pip install -e ~/Desktop/Solutions/Plugpass` succeeds;
   `plugpass --help` runs and shows the existing argparse help text, unmodified from
   `skill_security_audit.py`'s current `--help` output.

## Milestone 2 — Standalone-safe defaults

**Goal:** `plugpass scan <path>` works for someone with no `~/.claude` tree at all; the
Claude-specific default target list becomes an opt-in convenience, never a silent no-op.

1. Modify `plugpass/scanner.py`'s default-target resolution: build the default list
   (`skill-source/`, `skills/`, `plugins/` under `runtime_root()`) but only if **at least one**
   actually exists on disk. If `--target` is omitted and none exist, `argparse.error()` with a
   clear message ("no ~/.claude-shaped tree detected; pass --target explicitly") instead of
   silently scanning zero files and reporting a false-clean 0/0 result.
   **verify:** new test — `plugpass` (no args) against a `tmp_path` `CLAUDE_RUNTIME_ROOT` with no
   `skills/`/`skill-source/`/`plugins/` subdirs exits non-zero with the clear error; the same
   `tmp_path` WITH a `skills/` subdir still uses it as the default (backward-compatible).
2. Confirm zero behavior change for the real `~/.claude` case: run `plugpass` (via the venv entry
   point) with `CLAUDE_RUNTIME_ROOT` unset, from a machine that has a real `~/.claude`.
   **verify:** critical/warning/intentional counts identical to running
   `~/.claude/scripts/skill_security_audit.py` directly, same day.

## Milestone 3 — `plugpass init` + README + license placeholder

**Goal:** close the remaining Discovery deliverables that don't touch scanning logic.

1. Add an `init` subcommand: `plugpass init <path>` writes `{"scopes": []}` to
   `<path>/config/skill_security_allowlist.json` if absent; refuses (with a clear message) to
   overwrite an existing file.
   **verify:** test asserts first-run creates the file with the right shape; second run against
   the same path (with the file hand-edited in between) leaves it untouched.
2. Write `README.md`: install (`pip install -e .` for now — no PyPI publish this slice), `plugpass
   scan <path>`, `plugpass init <path>`, how to read `score`/`score_model`. No mention of
   remediation/quarantine/builder-registry as if they already exist (out of scope, per Discovery).
   **verify:** read-through against the Discovery's out-of-scope list — zero capability claims
   this plan doesn't deliver.
3. `LICENSE`: explicit placeholder file — a single line stating the license is undecided pending a
   product-wide decision (link back to this plan + the Discovery risk log), never real license
   text chosen silently.
   **verify:** file exists; content is the pending-decision marker, not license terms.

## Milestone 4 — Packaging smoke test, regression, wrap

1. `tests/test_packaging_smoke.py`: subprocess-based fresh-venv install (`python -m venv` +
   `pip install -e .`) → `plugpass --help` exits 0 → `plugpass scan <synthetic-fixture-dir>` exits
   the expected code (0/1/2) against a known-bad fixture (reuse the pattern from
   `~/.claude/tests/test_skill_security_audit_portability.py`'s dangerous-subprocess fixture,
   built via string concatenation per the standing guardrails-self-match precedent).
   **verify:** `pytest tests/test_packaging_smoke.py -q` — green.
2. Port the 14 scanner-logic tests from `~/.claude/tests/test_skill_security_audit_portability.py`
   to import `plugpass.scanner`/`plugpass.target` instead of the flat script modules, confirming
   the sync + import-rewrite didn't silently change behavior.
   **verify:** `pytest tests/ -q` in the new repo — all green (14 ported + smoke test).
3. `/simplify` pass on the new packaging code (sync script, entry point, `init` subcommand).
   **verify:** run, output reviewed inline.
4. Security pre-done checks (global CLAUDE.md): no raw user input in subprocess/shell args (n/a —
   `sync_from_claude.py`'s file copy uses no shell invocation); no secrets in logs (n/a — no new
   logging); every new route has auth (n/a — no network route, local CLI); every new env var
   (`CLAUDE_RUNTIME_ROOT`, inherited unchanged from the portability slice) is read in code (yes,
   already verified this session); every changed line traces to the stated goal (spot-check diff).
   **verify:** checklist walked, recorded in a HANDOFF thread entry.

## Task Matrix

| # | Task | Owner | Est. effort* | Depends on | verify |
|---|------|-------|--------------|------------|--------|
| M1.1 | Repo skeleton | implementer | 0.5h | — | M1 step 1 |
| M1.2 | Sync script | implementer | 1h | M1.1 | M1 step 2 |
| M1.3 | `pyproject.toml` + entry point | implementer | 0.5h | M1.2 | M1 step 3 |
| M2 | Standalone-safe defaults (2 steps) | implementer | 1.5h | M1 | M2 |
| M3 | `init` + README + license placeholder (3 steps) | implementer | 1.5h | M1 | M3 |
| M4 | Smoke test, regression, simplify, security check | implementer | 2h | M1–3 | M4 |

*Rough single-operator estimate.

## Milestone Timeline

Sequential, single implementer. M2 and M3 both depend only on M1, are independent of each other,
and can run in either order: **M1 → {M2, M3} → M4**. No calendar deadline.

## Rollback

Trivial — this plan creates a brand-new, standalone repo that nothing else depends on yet
(pre-publish, pre-announcement). The sync mechanism only *reads* from `~/.claude/scripts/`; it
never writes back. Rollback is `rm -rf ~/Desktop/Solutions/Plugpass/` — no state-file changes, no
schema changes, nothing in `~/.claude` or `AgenticaOS` touched.
