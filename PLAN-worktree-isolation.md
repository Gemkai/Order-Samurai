# PLAN — Worktree isolation for the overnight meditation pipeline

**Status:** DONE — Option C landed, `MEDITATION_WORKTREE=1` default ON · **Created:** 2026-07-09

## DONE (2026-07-09) — Option C completed on top of the worktree scaffolding
The earlier `5460abc` shipped the worktree *scaffolding* (branch-travel) behind a default-OFF flag,
but state written inside the worktree never reached the main tree — so the dashboard-refresh job,
the `Estimated_Agent/Human_Time_Saved` heroes, `reap_stale_doing.py`, and the governance scanners
(all of which read `state/` from the **main-tree** path) would have seen a frozen snapshot, and each
run's completions would have been discarded when the branch is force-reset. That gap is why the flag
was OFF.

**Option C (this change) closes it:** state/artifacts/`Data` are now CANONICAL in the main tree via
absolute `MEDITATION_STATE_DIR` / `MEDITATION_ARTIFACTS_DIR` / `MEDITATION_DATA_DIR` (+ `ORDER_SAMURAI_ROOT`)
exported by `meditation_overnight.sh`. The cycle body still runs in the disposable worktree for
git/code isolation, but **every state writer targets the main tree**:
- `meditation_overnight.sh` — `LOGBOOK`, `mkdir`, seed, `CYCLE_LOG`, `LOCK_DIR`, `MEDITATION_STOP` all re-rooted;
- `bin/reap_stale_doing.py`, `bin/keiko_improvement.py`, `bin/stamp_meditation_timestamps.py` — honor `MEDITATION_STATE_DIR` (env override, falls back to script-relative for manual use);
- `prompts/meditation_cycle.md` — a runtime-path header (built + prepended by the shell with resolved absolute dirs) plus re-rooted STEP D/E `state/wt` + `state/ronin_results` paths.

Consumers needed **zero** changes — `aggregate.py` and `refresh_dashboard.py` already resolve
`ORDER_SAMURAI_ROOT` to the main tree. In legacy `MEDITATION_WORKTREE=0` mode `MAIN_DIR == cwd`, so the
absolute paths resolve exactly as the old relative ones (behavior byte-for-byte unchanged). The
interactive-session **abort** was downgraded to a **warn** in worktree mode (concurrency is now safe);
the merge/rebase/cherry-pick guard and the single-instance lock are unchanged. Default flipped ON in
the script and `meditation.env`.

**Proven by** `tests/test_meditation_overnight_worktree.py` (isolation + **state lands in the main
tree**, tracked edit survives, branch never switches) and `tests/test_meditation_overnight_merge_guard.py`
(merge guard still fires in worktree mode; abort→warn downgrade), 17 tests green; plus an end-to-end
stubbed cycle against scratch dirs (worktree created+cleaned, canonical state written, real tree
untouched, no stash). **Rollback:** `MEDITATION_WORKTREE=0` (instant) or revert the commit → back to
the `292ce81` guard floor (safe, just non-concurrent).

Superseded the earlier branch-travel decision below (it was simpler but left the state-read gap open).

---

_Original plan (options considered) below._


**Prereq shipped:** interactive-session guard in `meditation_overnight.sh` (commit `292ce81`) —
the stopgap that stops the *harm*; this plan removes the *cause*.

---

## Problem

`meditation_overnight.sh` runs its 6-hour cycle **in the main working tree** (`REPO_DIR`), and
switches branches there: `git switch -C ronin/overnight/<DATE>` (line ~117). While an
interactive session is working, that branch-switch + the `MEDITATION_AUTO_STASH=1`
`git stash push -u` collide with the session's uncommitted files. On 2026-07-09 this twice
wiped a live session's brand-new untracked files (`SHARED_NOTES.md` et al.) and reverted a
tracked edit; commits also silently landed on the disposable ronin branch because the job
switched the current branch mid-session. See memory `overnight-job-clobbers-uncommitted-work`.

The guard (`292ce81`) makes the job **abort** when real work is uncommitted — safe, but it means
the job and an interactive session **can't run concurrently**. The real fix is to run the
overnight cycle in its **own git worktree** so it never touches the checkout a human/agent is using.

## Goal

The overnight pipeline runs entirely in a dedicated worktree on `ronin/overnight/<DATE>`, so:
- it never switches branches or stashes in the main working tree, and
- it can run **concurrently** with an interactive session with zero interference,
- while every downstream consumer that reads `state/` still sees the right data.

Non-goal: changing what the cycle *does* (ronin scouts, sensei, keiko, budget) — only *where* it runs.

## The core constraint (why this isn't a one-liner)

`state/` under `Governance/Order Samurai/` is **not private to the job**. It is read from the
**main-tree path** by other mechanisms — the dashboard refresh (`Data/wid_payload.json` pipeline),
`reap_stale_doing.py`, kill-chain state, `MEDITATION_STATE.json`, the governance scanners. If the
job writes `state/` inside a worktree, those consumers read a stale main-tree copy. So the plan
must decide **what is canonical state** and make the worktree and main tree agree on it.

Inventory to produce first (verify step): every reader/writer of `Governance/Order Samurai/state/**`
and `Data/**`, with its path assumption (main-tree-relative vs repo-relative vs absolute).

## Design options

**A. Worktree + canonical state stays in main tree (symlink/bind).** Job runs in
`state/wt/meditation` (or a sibling `../AgenticaOS-overnight`), but `state/` and `Data/` resolve to
the main tree's copies (symlink the worktree's `state`→ main `state`, or pass absolute `STATE_DIR`).
- ✅ Consumers unchanged; canonical state has one home.
- ⚠️ Concurrent writes to `state/` from job + interactive session need the existing `mkdir` lock to
  cover state mutation, not just run-instance. Symlinks inside a worktree are fiddly with git.

**B. Worktree owns state; results (incl. state) merge to main at cycle end.** Job is fully isolated;
a commit/cherry-pick step publishes state deltas back to main after each cycle.
- ✅ Cleanest isolation; matches the existing "results cherry-picked to main" model.
- ⚠️ Dashboard/consumers see state only after the merge step (latency); merge conflicts on
  append-only `*.jsonl` state need an ours/theirs or append-merge strategy.

**C. Parameterize `STATE_DIR`/`DATA_DIR` as absolute main-tree paths; run cycle in worktree.**
Smallest code delta: keep state canonical in main tree via env vars, run everything else in the
worktree. Essentially A without symlinks.
- ✅ Minimal, explicit, no symlink games.
- ⚠️ Requires auditing every `state/`-relative path in the script + the ronin sub-scripts and the
  headless `claude -p` prompt (`prompts/meditation_cycle.md`) to honor the env var.

**Recommended: C** (explicit absolute `STATE_DIR`/`DATA_DIR` into the main tree, cycle body in a
dedicated worktree), falling back to **A** if too many sub-scripts hardcode `state/`.

## Sketch (option C)

1. `WORKTREE="$REPO_ROOT/.worktrees/meditation-$DATE"`; `git worktree add -B ronin/overnight/$DATE
   "$WORKTREE" HEAD` — creates/repoints the branch **without** touching the main checkout.
   *verify:* main tree's `git branch --show-current` is unchanged during a run.
2. Export `STATE_DIR="$REPO_ROOT/Governance/Order Samurai/state"` and
   `DATA_DIR="$REPO_ROOT/Data"` (absolute, main-tree). `cd "$WORKTREE/Governance/Order Samurai"`.
   *verify:* dashboard refresh mid-run reads live cycle state, not a stale snapshot.
3. Replace every `state/…` / `artifacts/…` write in the script + `reap_stale_doing.py` +
   `keiko_improvement.py` + the ronin worktrees (Step D already uses `state/wt/*` — re-root under
   `$STATE_DIR`) + the meditation prompt with `$STATE_DIR`/`$DATA_DIR`.
   *verify:* `grep -rn "state/" bin/ prompts/` returns only env-var-based paths.
4. `git worktree remove --force "$WORKTREE"` in the EXIT trap (alongside the lock release).
   *verify:* no orphaned worktrees accumulate (`git worktree list` clean after a run).
5. Delete/relax the interactive-session **abort** guard → downgrade to a *warn* (concurrency is now
   safe), but KEEP the merge/rebase-in-progress guard.
   *verify:* run the job while the main tree has uncommitted work → job completes, main tree untouched.

## Verification (goal-backward)

- Start a cycle, then in the main tree create an untracked file + edit a tracked file. After the
  cycle: both survive byte-identical; main tree still on its original branch. (Reproduces the
  2026-07-09 incident and proves it's fixed.)
- Dashboard (`Data/wid_payload.json`) updates during the run.
- `git worktree list` and `git stash list` are clean afterward.
- Existing tests pass: `pytest tests/test_meditation_overnight_merge_guard.py` (update the
  abort→warn assertion) + `test_stamp_meditation_timestamps.py`.

## Risks & rollback

- **macOS bash 3.2 / launchd system-python** footguns (see the script's scar comments) — test the
  worktree path under `/bin/bash` and `/usr/bin/python3`, not just mise python.
- **Worktree + submodules**: `git worktree add` on a superproject with submodules needs
  `git submodule update` in the new worktree, or the ronin scouts see empty sub-bundles.
- **Rollback:** revert to the `292ce81` guard behavior (abort on dirty) — the pipeline is still
  safe, just non-concurrent. Keep the guard commit as the known-good floor.

## Out of scope

- The separate memory-consolidation overnight loop (promote `Daily/` → `me/`/`MEMORY.md`) — its own
  plan; it should reuse whatever worktree pattern this establishes.
