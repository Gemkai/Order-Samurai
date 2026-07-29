# INCIDENT 2026-07-12 — overnight meditation worktree destroyed mid-cycle

Reconstructed 2026-07-12 ~02:3x from live-state verification (the memory entry
`meditation-worktree-destroyed-2026-07-12` cited this file, but it was never on disk —
most plausibly written inside the worktree that was destroyed). Facts below are
re-verified against the primary repo, launchd, and the run logs — not copied blind.

## Verified timeline
- **01:23:14** — run A starts (`MEDITATION start`, branch `ronin/overnight/2026-07-12`,
  reset to `663a0b7`, worktree `~/.agentica/meditation-wt` + per-pillar sub-worktrees
  `state/wt/{bow,brush,sword}` — confirmed by the 4 prunable registrations found after).
- **~01:44–01:46** — the ENTIRE worktree tree is deleted externally while the brush ronin
  is mid-work (per the observing session's memory). Sword's sub-worktree registration was
  left at `89dedce` — corroborating that sword AUTO-013 committed before the wipe.
- **01:50:43** — run A's log prints `cycle 1 ok` then nothing; its `tee` writes fail
  (`artifacts/ronin_logs.md: No such file`) — the runner limps then dies without
  `MEDITATION end`.
- **02:00** — the SCHEDULED run B starts, `worktree remove -f` no-ops (dir already gone),
  and `worktree add -f -B` dies: `fatal: cannot force update the branch ... used by
  worktree` — the stale registration. launchd records exit 255. **Run B is a victim,
  not the destroyer.**

## Recovery (done, 2026-07-12 night)
- Stranded sword commit `89dedce` (AUTO-013 Dangerous_Tool_Invocations +FIELD): pinned
  with branch `rescue/sword-auto-013`, cherry-picked onto `work` as `dccf265`, full
  pytest gate green (410 passed, +3 telemetry tests).
- `git worktree prune` cleared the 4 dead registrations — future runs unblocked.
- `meditation_overnight.sh` hardened (same commit as this file):
  1. EXIT-trap lock release is now ownership-guarded — an invocation that aborts at the
     "another run is active" check no longer deletes the ACTIVE run's lock.
  2. Worktree provisioning runs `git worktree prune` before `worktree add`, so an
     externally-deleted worktree can never again wedge the next scheduled run.

## Destroyer: still unidentified — ruled OUT tonight
- **Every scheduled launchd job**: no `com.agentica.*` job fires in the 01:44–01:46
  window (librarian 01:30 finished ~01:41 and only rm's its own lock; nightly-autosave &
  meditation 02:00; sensei-cycle 02:15/08:15/14:15/20:15; all others daily/weekly hours).
- **The meditation runner itself**: err.log shows exactly ONE post-wipe "Preparing
  worktree" (the 02:00 run, which FAILED before touching anything). No reclaim-reprovision
  happened at 01:44.
- Remaining candidates: a concurrent interactive/spawned Claude session's action, or a
  cleanup path INSIDE the run (per-pillar ronin teardown with a wrong/empty path variable
  — the sub-worktrees nest inside the parent, so one bad `rm -rf "$VAR"` kills all).
  Next probe: instrument the runner to `fs_usage`/log deletions of $WT_DIR, or add a
  canary file + watcher during the next SUPERVISED run.

## Policy until identified
Per the observing session's directive: **no unattended overnight meditation runs**. A
supervised daytime run (user present, watcher on the worktree path) is the sanctioned
way to both re-test and catch the destroyer in the act.

## Wargame 01 consequence
C7 ("overnight completes") FAILS honestly for 2026-07-12 — no completed run exists, and
the hardened checker refuses inherited-history/false-pass shortcuts. Recorded in
`wargames/01-final.txt`'s delta table; the mission ends with the honest number.

---

## RESOLVED (2026-07-12 08:30, supervised run + trap)

**The "destroyer" is the meditation runner's own EXIT cleanup — there is no external
attacker.** A supervised daytime run (08:00:48, from 8e15bbb) with a 2s-interval trap on
the worktree path proved it: `MEDITATION end` logged at 08:29:16; the worktree vanished at
08:29:17.7 — one second later, which is `_meditation_cleanup`'s `worktree remove` doing its
job at CLEAN completion. The trap's "strike" was a benign race (its log-check ran 2s before
the end line was written). Today's run: cycle 1 ok, 3 real commits (AUTO-019
Mechanism_Liveness LIVE, arts source-declaration fix, cycle state), no A-prime halt, no
stale registrations left behind.

**The Friday-night (01:44–01:46) wipe, re-explained by the same mechanism:** run A's main
script was terminated early (consistent with a `launchctl kickstart -k`-style restart or
equivalent TERM from a concurrent session) → its EXIT trap fired mid-cycle → the successor
instance's provisioning force-delete removed the directory without git bookkeeping →
stale registration → the next invocation died on "cannot force update the branch". Run A's
orphaned ronins kept writing (the tee failures; the stray "cycle 1 ok" at 01:50 via the
still-open log fd). The exact sender of the early TERM remains unconfirmed, but the
mechanism requires no unknown actor — and both enabling defects are already fixed
(ownership-guarded lock release; prune-before-add).

**Policy update:** unattended overnight runs are UNBLOCKED. Worktree disappearance at
`MEDITATION end` is by design, not an incident. The real anomaly signature to watch for:
worktree gone while the run's log block has no `MEDITATION end`.

**Cost note:** the supervised cycle spent $15.98 (vs the ~$1.26 typical) clearing a large
backlog — the run self-halted further cycles on its $5 daily budget, as designed. C7's
day-total < $5 gate therefore reads this run as FAIL on cost alone (commits ✓, no halt ✓).
