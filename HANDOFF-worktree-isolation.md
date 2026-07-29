# HANDOFF — Meditation worktree isolation (Option C)

**Date:** 2026-07-09 · **Branch:** `feat/meditation-worktree-isolation` · **Status:** implemented + verified, ready to commit/merge

## Goal (achieved)
The overnight meditation cycle runs entirely in a dedicated git worktree on `ronin/overnight/<DATE>`,
so it NEVER switches branches or stashes the main working tree, can run concurrently with an
interactive session, AND every consumer that reads `state/` from the main-tree path still sees live
data. Default flipped **ON** (`MEDITATION_WORKTREE=1`).

## What changed
Built on the earlier worktree scaffolding (`5460abc`), which ran the cycle in a worktree but left
state stranded there (dashboard/heroes/reaper read a frozen main-tree snapshot; each run's
completions discarded on branch reset — the reason the flag was OFF). **Option C** makes state
canonical in the main tree:

- **`bin/meditation_overnight.sh`**
  - Captures `REPO_ROOT`; exports absolute `MEDITATION_STATE_DIR` / `MEDITATION_ARTIFACTS_DIR` /
    `MEDITATION_DATA_DIR` (+ `ORDER_SAMURAI_ROOT`) → the MAIN tree. In legacy mode `MAIN_DIR == cwd`
    so these equal the old relative paths (byte-identical behavior).
  - Re-rooted `LOGBOOK`, `mkdir`, seed copy, `CYCLE_LOG`, `LOCK_DIR`, `MEDITATION_STOP` to the canonical dirs.
  - Interactive-session **abort → warn** in worktree mode (concurrency is now safe); merge/rebase/
    cherry-pick guard + single-instance lock unchanged; auto-stash path gated to legacy mode only.
  - Builds a **runtime-path header** (resolved absolute dirs) and prepends it to the orchestrator prompt.
  - Default flipped: `MEDITATION_WORKTREE="${MEDITATION_WORKTREE:-1}"`.
- **`bin/reap_stale_doing.py`, `bin/keiko_improvement.py`, `bin/stamp_meditation_timestamps.py`** —
  resolve state via `MEDITATION_STATE_DIR` env override (falls back to script-relative for manual runs).
- **`prompts/meditation_cycle.md`** — top pointer to the RUNTIME PATHS header; STEP D/E `state/wt` +
  `state/ronin_results` re-rooted to `$STATE_DIR` (main tree).
- **`meditation.env`** — `MEDITATION_WORKTREE=1`; note that `MEDITATION_AUTO_STASH` is now moot in worktree mode.
- **Tests** — `tests/test_meditation_overnight_merge_guard.py` pins legacy tests to `MEDITATION_WORKTREE=0`
  and adds: merge guard still fires in worktree mode + abort→warn downgrade. `tests/test_meditation_overnight_worktree.py`
  adds the Option-C proof (state lands in the MAIN tree; tracked edit survives; branch never switches).

Consumers (`aggregate.py`, `refresh_dashboard.py`) needed **no changes** — they already resolve
`ORDER_SAMURAI_ROOT` to the main tree.

## Verification (done)
- `pytest tests/test_meditation_overnight_worktree.py tests/test_meditation_overnight_merge_guard.py
  tests/test_stamp_meditation_timestamps.py` → **17 passed**.
- Helper scripts honor `MEDITATION_STATE_DIR` under system python `/usr/bin/python3`; repo state untouched.
- End-to-end stubbed cycle (scratch state, stub `claude`, worktree mode): worktree created +
  submodule-init'd + removed on exit; canonical state written to the main-tree dir; abort→warn fired
  on real uncommitted work; reap/keiko ran; real `state/` + branch + stash all untouched.
- `bash -n` clean under macOS `/bin/bash` (3.2); `py_compile` clean.

## Not done / follow-ups
- No **real** budgeted `claude -p` cycle was run (prior session spent ~$7; the clean 2 AM scheduled
  run in a quiet env is the real end-to-end test). All cheap checks pass.
- Code commits still land on the disposable `ronin/overnight/<DATE>` branch and don't auto-merge to
  `main` — a **pre-existing** gap identical in both modes, explicitly out of scope (only runtime STATE
  is made canonical here). Worktree mode is strictly better than legacy (legacy also stranded commits
  AND left the main checkout on the ronin branch).
- The launchd plist needs **no** change — it just invokes the script, which now creates its own worktree.

## Rollback plan
1. **Instant:** set `MEDITATION_WORKTREE=0` (env or `meditation.env`) → falls back to the legacy
   in-main-tree guard behavior (the `292ce81` abort floor: safe, aborts on dirty tree, just non-concurrent).
2. **Full:** `git revert` this branch's commit(s) → back to the `5460abc`/`292ce81` state.
Either way the pipeline stays safe; the only loss is concurrency.
