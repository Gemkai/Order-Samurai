# Order Samurai

## What This Is
Deterministic governance engine for Agentica-OS — the security-focused counterpart to Chaos Monkey.
Four pillars (Bow=operations, Sword=security, Brush=architecture, Arts=quality) enforced by
executable policy in `config/`, verifiers in `execution/`, and operational scripts in `bin/`.
The reflex engine (`Governance/api/src/reflex-engine.ts`; `agentica_core/reflexes.py` is only
the dashboard alert layer) fires remediation skills based on pillar metrics and logs outcomes
to `state/exec_log.jsonl`. Vision docs: `PROJECT.md`, `RONIN_SPEC.md`.

## Non-Negotiables
- `state/` is machine-written (reflex engine, meditation cycles, triage). Read freely; never
  hand-edit a `.jsonl` event log — append via `bin/emit_event.py` or the owning script.
- `.tmp/` is gitignored scratch. `.tmp/worktrees/` may hold a LIVE worktree from an overnight
  remediation or meditation run — check `git worktree list` before deleting anything there.
- Concurrent sessions commit to `work`. Stage explicit paths only; audit `git show --stat HEAD`.
- Local LLM calls go through `agentica_core/llm/local_guards.py` — never re-implement the
  Ollama malformed-output guards inline.

## Commands
- Tests: `python3 -m pytest tests/ -q` (fast, ~2s; run from this directory — `pyproject.toml` is the rootdir)
- Health: `python3 execution/doctor.py` (daemon/LLM/telemetry checks)
- Architecture score: `python3 execution/score_architecture.py`

## Navigation
- `config/` — executable policy contracts. `X.json` governs the repo; `claude_X.json` governs the
  `~/.claude` runtime — the pairs are intentionally separate surfaces, not duplicates.
- `execution/verify_*.py` — one verifier per policy contract; `doctor.py` aggregates.
- `bin/` — operational scripts (triage, audits, scrubbing, meditation runners).
- `docs/CYCLE-TERMINOLOGY.md` — canon: meditation vs sensei vs keiko cycles + the skill-improvement loop.
- `docs/solutions/` — documented solutions to past problems (YAML frontmatter, searchable via
  `ce-learnings-researcher`); domain-wide ones live in `Governance/docs/solutions/`.
- `state/` — runtime truth: `exec_log.jsonl` (remediation outcomes), `error_triage.json`
  (calibrated error rate), `kill_chain_*.jsonl` (detection events), `reflex_engine_state.json`.
