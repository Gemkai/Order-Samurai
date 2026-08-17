---
title: Four HITL queue rows (incl. hitl-3e05b554) were destroyed by a routine pre-merge `git stash`, not by a code defect
date: 2026-08-16
category: docs/solutions/infrastructure/
module: Governance/Order Samurai/state/hitl_queue.json (git-tracked machine-written state)
problem_type: operational_data_loss
component: repo state hygiene / git working-tree discipline
severity: high
applies_when:
  - A row that was demonstrably raised into hitl_queue.json is absent later with no resolution record
  - Machine-written state under a git-tracked `state/` directory disagrees with what the runtime believes it wrote
  - Reaching for a whole-tree `git stash` while long-lived uncommitted machine state is in the working tree
tags: [hitl-queue, git-stash, data-loss, state-hygiene, bushido-engine, false-attribution]
---

## Summary

`hitl-3e05b554` was raised, did reach `state/hitl_queue.json`, and then vanished from the
git-tracked file with no resolution record. **It was not a code defect.** A whole-dirty-tree
`git stash push` run at `2026-08-14T01:14:39Z` as pre-merge hygiene captured the dirty queue
into `stash@{0}` and then reset the working tree to `HEAD`, reverting the file to a blob from
**2026-08-02**. The stash was never popped or applied, so the rows never came back.

Three further findings that change the shape of the open question:

1. **Four rows died, not one.** `hitl-28969ed5` (reflex), `hitl-sh-961b1ee9` (self_harness),
   `hitl-3c28fbb0` (factory) and `hitl-3e05b554` (factory) — three different sources spanning
   11 days. Any single-escalation bug is ruled out by the blast radius alone.
2. **The `2026-08-14T01:14:50Z` "unexplained write" is a witness, not the culprit.** It is
   `reconcile_stale_approved()` doing a benign, non-destructive stamp — it read the
   *already-reverted* file 10 seconds after the reset. It dropped nothing.
3. **This is NOT the bug fixed in `ae459a5e`.** See "Relationship to ae459a5e" below.

## Verified timeline

All timestamps UTC. Host is `-0400`, so `21:14:39 EDT Aug 13` = `2026-08-14T01:14:39Z`.

| When (UTC) | What | Evidence |
|---|---|---|
| 2026-08-02T17:32:52Z | Last queue content that ever reached a commit before the loss: 16 rows. Blob `856731ea`, committed at `a12a792a`. | `git show a12a792a:"Governance/Order Samurai/state/hitl_queue.json"` |
| 2026-08-02 → 08-13 | Four rows are enqueued into the **working-tree file only**. The file sits dirty and uncommitted for 11 days. | ids diff `dd9e0e56` vs `a12a792a` |
| 2026-08-13T13:03:10Z | `hitl-3c28fbb0` still present — `reconcile_stale_approved` re-surfaces it. | `state/autonomic_events.jsonl` |
| 2026-08-13T20:57:26Z | `hitl-3e05b554` enqueued (`source: factory`, `status: pending`, task `idea-gallery:regen-shadow-ai-detection`). Queue now 20 rows; file `updated_at` = this instant. | `git show dd9e0e56:…/hitl_queue.json` |
| **2026-08-14T01:14:22Z** | `git stash push` #1 — `stash@{1}`, *"protect uncommitted canary fix before origin/work merge"*. Path-limited; the queue survives it. | `git reflog stash --date=iso` |
| **2026-08-14T01:14:39Z** | `git stash push -u` #2 — `stash@{0}` = `dd9e0e56`, *"On work: full dirty-tree stash before origin/work merge 2026-08-13"*. Three parents (base/index/untracked) ⇒ `-u`. **Captures all 20 rows.** | `git log -1 --format=%P dd9e0e56` |
| **2026-08-14T01:14:40Z** | **THE DROP.** The stash's working-tree reset. Reverts `hitl_queue.json` to HEAD's 2026-08-02 16-row blob. | reflog: `a12a792a HEAD@{2026-08-13 21:14:40 -0400}: reset: moving to HEAD` |
| 2026-08-14T01:14:50.250742Z | `reconcile_stale_approved()` reads the reverted file, re-surfaces **only** `hitl-c998eca2`, stamps `approval_renotify_count: 0→1`, writes. This is the timestamp both handoffs called "the unexplained write". | `e9ef85a6` blob `updated_at`; `autonomic_events.jsonl` line 431 |
| 2026-08-14T01:20:25Z | `merge origin/work` — the operation the stash was taken for. | reflog |
| 2026-08-14T01:45:57Z | `e9ef85a6` *"chore(governance): land generated audit/report state"* **commits the reverted 16-row queue**, cementing the loss into history. | `git show e9ef85a6:…/hitl_queue.json` |
| ever after | `stash@{0}` is still in `git stash list` — never popped, never applied. | `git stash list` |

## Why the reset is the culprit and the 01:14:50Z write is not

- The reset is recorded verbatim in the reflog, 10 seconds *before* the write. The rows were
  already gone when `reconcile_stale_approved` opened the file.
- `updated_at` moved **backwards**, from `2026-08-13T20:57:26Z` to the 2026-08-02 blob's value.
  No writer in this system can do that — every load-mutate-write stamps `updated_at = now()`.
  Only a VCS restoring an old blob produces a backwards `updated_at`.
- Independent, non-git corroboration from `autonomic_events.jsonl`: the `hitl_approval_stale`
  pass on 2026-08-13 named `c998eca2` **and** `sh-961b1ee9` (and `3c28fbb0` hours later); the
  01:14:50Z pass names **only** `c998eca2`. The runtime itself observed the rows missing at the
  moment of that write.
- The 01:14:50Z write's *entire* effect is two fields on one row (`approval_renotified_at`,
  `approval_renotify_count`), diffed field-by-field against the 2026-08-02 blob. It removed
  nothing.

## The prime suspect that was ruled out: unguarded read-modify-write

Checked and **clean**. Every load-mutate-write of `hitl_queue.json` is serialised:

- `Governance/agentica_core/bushido_engine.py` — all 8 writers carry `@_under_queue_lock`
  (`enqueue_hitl` 569, `_consume_approval` 620, `mark_complete` 656, `reconcile_stale_executing`
  685, `reconcile_stale_approved` 810, `_settle_push_claim` 984, `_release_push_claim` 1020,
  `_review_hitl_locked` 1137). The decorator wraps the **whole** load→mutate→write cycle, and
  the lock lives on a `.lock` sidecar precisely because `atomic_json_write` renames a fresh
  inode over the destination.
- `Order Samurai/bin/self_harness_cycle.py:398`, `bin/heldout_rotation.py:178`,
  `bin/surface_proposal_review.py:115` — explicit `with file_write_lock(queue_path):` around
  read + `atomic_json_write`.
- `bin/review_pending_patch.py` delegates to `bushido_engine.mark_complete` / `review_hitl`
  rather than hand-rolling a mutator.
- `Governance/api/src/reflex-engine.ts` and `sensei-orchestrator.ts` never write the file
  directly; they shell into the Python writer.
- Read-only consumers (no write path): `Apps/morning-joe/build_payload.py`, `fetch_widgets.py`,
  `bin/hitl_alerts.py`, `bin/bushido_check.py`. `Apps/morning-joe/os_hitl_bridge.py` writes only
  its own bridge state file, never the queue.

No writer whole-file-rewrites from a stale snapshot. **No code fix is warranted.**

The second suspect — a concurrent scheduled writer at 01:14 — is also ruled out: no launchd job
in `Governance/automation/launchd/` has a `StartCalendarInterval` at 01:14/01:15 in either UTC or
local time. The interval jobs that *could* land there (`factory-dispatcher` 900s,
`os-hitl-bridge` / `morning-joe-refresh` 300s) go through the locked path and cannot move
`updated_at` backwards.

## Relationship to `ae459a5e` — different root cause

`ae459a5e` ("stop silently swallowing the 2nd HITL escalation") fixed a defect where both
`raise_hitl` callers shared one R4 approval key, so a **second escalation never landed in the
queue** while reporting "human notified". That is a *raise* being swallowed **upstream** of the
file.

This incident is downstream and unrelated: `hitl-3e05b554` **did** land, with a complete record
(`enqueued_at`, `context`, worker log paths) — it is fully preserved in `dd9e0e56`. It was then
destroyed by a VCS operation, together with three rows from two other sources. Same file, same
row id, two independent causes.

**Correction required:** `ae459a5e` asserts, in a code comment in `Execution/factory/dispatcher.py`
and in `.planning/TRUST_POLICY.md`, that *"hitl-3e05b554 is recorded in the ledger and exists in no
version of the queue."* That is **false** — it exists in `stash@{0}` (`dd9e0e56`). The claim was
used as evidence for the escalation-drop diagnosis; the fix itself is still correct on its own
merits (4 regression tests), but this row is not evidence for it.

## Recovery

The four rows are intact and recoverable read-only:

```
git show dd9e0e56:"Governance/Order Samurai/state/hitl_queue.json"
```

`hitl-3e05b554` is `status: pending` — an unanswered escalation for
`idea-gallery:regen-shadow-ai-detection` (worker exited 0 with zero commits). Re-raising it
belongs to the factory, **not** to a hand-edit: `state/` is machine-written and must never be
edited by hand. Deciding whether to re-raise is a human call, deliberately left open here.

## Root cause and standing lesson

`Governance/Order Samurai/state/hitl_queue.json` is **git-tracked** (`git check-ignore` → rc 1)
but **machine-written and never committed by the machine that writes it**. That combination is
the whole bug: uncommitted runtime writes accumulate in the working tree indefinitely — here for
11 days — and any operator or agent reaching for a routine whole-tree `git stash` before a merge
silently deletes state the system believes is durable. The `state/` write path is exemplary
(flock + tmp/rename, whole-cycle locks, documented); none of that survives a VCS-level revert of
the file, because a lock only binds participants and `git` is not a participant.

- **Never `git stash` a whole dirty tree that contains git-tracked machine-written state.**
  Stash by path, or commit the state first. `stash@{0}` here also swallowed 5,835+ files.
- **A silent gap between a runtime's ledger and its git-tracked state file is a VCS event until
  proven otherwise.** Check `git reflog` and `git stash list` *before* auditing the write path.
- **`updated_at` going backwards is a signature.** No application writer can produce it; it means
  a blob was restored. It is the cheapest discriminator between "a program dropped this" and
  "git dropped this".
- **The hazard is still armed.** The file is dirty again as of this writing
  (`updated_at 2026-08-16T01:24:39Z` vs HEAD's `2026-08-14T14:19:17Z`). Current exposure is only
  the `c998eca2` renotify stamp — no whole rows are working-tree-only right now — but the same
  `git stash` would still discard it.
