---
title: Plugpass (P1-A) Portability Slice — Phase 2 Plan
date: 2026-08-06
problem_type: feature
component: ~/.claude/scripts (skill_security_audit.py, skill_install_gate.py, skill_load_gate.py); Governance/Order Samurai/backlog/product_expansion_backlog.md (P1-A entry)
severity: medium
tags: [skill-security, portability, plugpass, product-expansion]
status: PROPOSED — awaiting approval, no implementation started
---

# Phase 2 Plan — Plugpass portability slice (P1-A, first shippable increment)

This is the first shippable slice of P1-A from `Governance/Order Samurai/backlog/product_expansion_backlog.md`,
scoped per the 2026-08-06 Phase 1 Discovery (four parallel investigations verified the backlog's reuse/size
claims against live code; P1-A came back sized **L**, not the backlog's "packaging, not architecture"). This
plan narrows P1-A to the slice that's actually buildable now, and writes down — rather than silently assumes —
the design decisions Discovery flagged as open.

**Cross-repo note:** this plan touches two independent repos — `~/.claude` (global Claude Code home) and
`Governance/Order Samurai/backlog/` (this AgenticaOS repo, doc-only change). They have separate git histories
and separate lifecycles; nothing here couples their deployment.

## Scope

In scope for this slice:
1. `CLAUDE_RUNTIME_ROOT` env-var support in the three scanner/gate scripts.
2. A real (v1, simple) per-target allowlist design — not the single-operator-hardcoded one.
3. An explicit written decision: hard-block stays for v1 (no new quarantine/review-queue mechanism).
4. An explicit written decision: a simple v1 numeric score added to the JSON output alongside the existing counts.
5. Characterization + new-behavior test coverage — **none exists today** for these three scripts.

Explicitly **OUT of scope** for this slice (named here so a future session doesn't assume they shipped):
- Builder-identity scoring/roll-up — no data model exists anywhere (`Finding` has no author/publisher field);
  needs its own Phase 1 Discovery before it's planned.
- MCP server config (`mcp.json`) scanning — the scanner only covers `skill-source/skills/plugins` today.
- External/customer-facing export or publish path — no packaging or scrub work in this slice.
- Wiring into the AUTO-045 metric — that `PROPOSED_BACKLOG.json` entry is a bare, unapproved stub with no
  description field; this plan notes the eventual wiring *point* only, does not build toward it.
- The "supply-chain-risk-auditor" reuse the original backlog cited — Discovery confirmed it's an unrelated
  tool (npm/pip dependency-graph auditing via `gh`, not code scanning); not referenced further here.

## Verified constraints (facts checked before writing this plan, not assumed)

- `skill_security_audit.py`, `skill_install_gate.py`, `skill_load_gate.py` are **not** in `release_gate.py`'s
  `PROTECTED_RELATIVE_FILES` or `PROTECTED_PREFIXES`, and not in `safety/hook_policies.json`'s
  `protected_files`/`blocked_paths` — confirmed by direct read 2026-08-06. Editing them does **not** require
  routing through `/control-plane-change`.
- Zero existing test coverage for any of the three scripts (confirmed: no `test_skill_security*`,
  `test_skill_install*`, or `test_skill_load*` anywhere under `~/.claude`). New tests are authored from
  scratch in this plan, not re-run from an existing suite.
- `~/.claude/tests/` is the house test location (precedent: `test_release_gate.py`).
- The proven portable-root **contract** (env var name + fallback shape) lives in
  `Governance/Order Samurai/execution/claude_runtime_target.py:70-74` — but that module is in the AgenticaOS
  repo, not `~/.claude`. This plan mirrors the contract (same env var name, same fallback semantics) via a
  small new helper inside `~/.claude/scripts/`, not a cross-repo import.

## Milestone 1 — Portable root resolution

**Goal:** all three scripts resolve their scan/gate root from `CLAUDE_RUNTIME_ROOT` when set, falling back to
`Path.home()/".claude"` when not, with **zero behavior change** for the default (unset) case.

1. Add `~/.claude/scripts/skill_scanner_target.py` — new, minimal module: `runtime_root() -> Path` reading
   `CLAUDE_RUNTIME_ROOT`, mirroring `claude_runtime_target.py`'s contract exactly (same fallback, same
   `.expanduser()` handling).
   **verify:** `python3 -c "import skill_scanner_target as t, os; os.environ['CLAUDE_RUNTIME_ROOT']='/tmp/x'; assert str(t.runtime_root())=='/tmp/x'; del os.environ['CLAUDE_RUNTIME_ROOT']; assert t.runtime_root()==t.Path.home()/'.claude'"` run from `~/.claude/scripts/`
2. Update `skill_security_audit.py:43-44` (`HOME = Path.home(); CLAUDE_DIR = HOME / ".claude"`) to call
   `skill_scanner_target.runtime_root()` instead of hardcoding.
   **verify:** `pytest ~/.claude/tests/test_skill_security_audit_portability.py -q` (new file, written in step 5)
3. Same edit to `skill_install_gate.py:26-27` and the equivalent lines in `skill_load_gate.py` (locate exact
   line numbers first — do not assume they match).
   **verify:** same test file, parametrized over all three scripts
4. Regression-check the SessionStart hook still fires correctly against this operator's real `~/.claude` with
   no env var set (the near-100% real-world case).
   **verify:** run `python3 skill_security_audit.py` with `CLAUDE_RUNTIME_ROOT` unset, diff output against the
   last known-good `skill_security_audit.json` — identical modulo timestamp/cache-hit fields
5. Write `~/.claude/tests/test_skill_security_audit_portability.py`: (a) env-var-set scans only the target dir,
   (b) env-var-unset preserves default behavior, (c) a foreign target with no allowlist file present doesn't
   crash (empty-allowlist fallback — needed before Milestone 2 exists).
   **verify:** `pytest ~/.claude/tests/test_skill_security_audit_portability.py -q` — new suite green

## Milestone 2 — Per-target allowlist v1

**Goal:** replace the single hardcoded `~/.claude/config/skill_security_allowlist.json` read with a
target-relative lookup, so scanning a foreign root doesn't inherit this operator's adjudications.

1. **Decision (recorded here, not deferred):** v1 allowlist path is `<runtime_root>/config/skill_security_allowlist.json`
   — the allowlist travels *with* the target root, not with the scanner. A foreign target with no such file
   gets an empty allowlist (every finding surfaces, nothing silently suppressed), which is the safe default —
   not this operator's file.
2. Update the allowlist-loading call site(s) in `skill_security_audit.py` to resolve from `runtime_root()`
   instead of the hardcoded `~/.claude/config/...` path.
   **verify:** test asserts a `tmp_path` target with its own allowlist file suppresses only its declared
   findings, and this operator's real allowlist entries do not leak into a foreign scan
3. Migration check: confirm this operator's existing allowlist still applies with no env var set (since
   `runtime_root()` falls back to `~/.claude`, and the new path resolves to the exact same real file).
   **verify:** `python3 skill_security_audit.py` (no env var) — 0 critical, unchanged warning count vs. today's
   baseline (`0 critical, 35 warning`, captured 2026-08-06)

## Milestone 3 — v1 numeric score + explicit scope decisions in the output

**Goal:** close the "counts, not a score" gap Discovery flagged, without inventing the larger builder-roll-up
model.

1. **Decision (recorded here):** v1 score formula is `max(0, 100 - 25*critical - 5*warning)`. Simple,
   monotonic, explicitly marked provisional in the output (`"score_model": "v1-linear-penalty"` field) so a
   future real rubric is never confused with this placeholder.
2. Add `score` and `score_model` fields to the JSON output, alongside the existing counts (unchanged, for
   backward compat with the SessionStart report format).
   **verify:** test asserts score=100 for zero findings, score=75 for exactly 1 warning, score=0 for 4+
   criticals (formula boundary cases)
3. **Decision (recorded here, not built):** quarantine/review-queue stays out of scope for this slice —
   hard-block (exit 2 on critical) remains the only enforcement mechanism, matching current behavior.
   **verify:** n/a (decision record) — confirmed by review that this plan introduces no quarantine-related code

## Milestone 4 — Regression, doc update, wrap

1. Run the full new + existing skill-security-adjacent test surface together.
   **verify:** `pytest ~/.claude/tests/ -k "skill_security or skill_install or skill_load" -q` — all green
2. Update the P1-A entry in `Governance/Order Samurai/backlog/product_expansion_backlog.md` to reflect what
   shipped vs. what's still open, so the doc stays honest for the next reader.
   **verify:** entry's acceptance criteria split into "Shipped (this slice)" / "Follow-up phases (unscoped)"
3. `/simplify` pass on the three touched scripts plus the new helper module.
   **verify:** `/simplify` run, output reviewed inline
4. Security pre-done checks (global CLAUDE.md): no raw user input in subprocess/shell args (n/a — no new
   subprocess calls this plan); no secrets in logs (n/a — no new logging); every new route has auth (n/a — no
   network route, this is a local scanner); every new env var (`CLAUDE_RUNTIME_ROOT`) is read in code (yes,
   M1.2–M1.3); every changed line traces to the stated goal (spot-check diff).
   **verify:** checklist walked, recorded in `HANDOFF.md`

## Task Matrix

| # | Task | Owner | Est. effort* | Depends on | verify |
|---|------|-------|--------------|------------|--------|
| M1.1 | New `skill_scanner_target.py` helper | implementer | 0.5h | — | M1 step 1 |
| M1.2 | Wire root into `skill_security_audit.py` | implementer | 1h | M1.1 | M1 step 2 |
| M1.3 | Wire root into gate scripts | implementer | 1h | M1.1 | M1 step 3 |
| M1.4 | Default-path regression check | implementer | 0.5h | M1.2–3 | M1 step 4 |
| M1.5 | Portability test suite | implementer | 2h | M1.2–4 | M1 step 5 |
| M2 | Per-target allowlist (3 steps) | implementer | 2.5h | M1 | M2 |
| M3 | v1 score + scope decisions (3 steps) | implementer | 2h | M1 | M3 |
| M4 | Regression, doc update, simplify, security check | implementer | 2h | M1–3 | M4 |

*Rough single-operator estimate — this is a one-person project, "owner" is whichever session/implementer picks
this plan up, not a team allocation.

## Milestone Timeline

Sequential, single implementer. M2 and M3 both depend only on M1's root-resolution helper, are independent of
each other, and can run in either order: **M1 → {M2, M3} → M4**. No calendar deadline; the gate is Phase 3's
security pre-done checks, not a date.

## Rollback

Entirely additive when `CLAUDE_RUNTIME_ROOT` is unset (the default, real-world case) — every verify step in
M1–M3 includes an explicit "unset env var reproduces current output" check. Rollback is `git revert` on the
touched-file commits; no state-file changes, no schema changes to existing output fields (only new fields
added, none removed or renamed).
