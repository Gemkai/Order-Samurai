#!/usr/bin/env bash
# meditation_overnight.sh — the 6-hour autonomous engine for the Order Samurai Meditation.
set -euo pipefail

# Portable timeout: GNU coreutils `timeout` when present, bash watchdog on macOS
# (where `timeout` does not exist — it silently killed every Mac meditation cycle).
tmo() {
  local secs="${1%s}"; shift
  if command -v timeout >/dev/null 2>&1; then timeout "${secs}s" "$@"; return $?; fi
  "$@" & local pid=$!
  ( sleep "$secs" && kill -TERM "$pid" 2>/dev/null ) & local wd=$!
  local rc=0; wait "$pid" || rc=$?
  kill "$wd" 2>/dev/null || true
  return "$rc"
}
REPO_DIR="${REPO_DIR:-$(pwd)}"
RUN_HOURS="${RUN_HOURS:-6}"
MAX_CYCLES="${MAX_CYCLES:-60}"
# Fallback literals MUST match meditation.env (the single source) so a missing meditation.env can
# never silently run with different limits than a present one. See meditation.env.
MAX_TURNS="${MAX_TURNS:-80}"
CYCLE_TIMEOUT="${CYCLE_TIMEOUT:-2400}"
COOLDOWN="${COOLDOWN:-15}"
ENABLED_RONINS="${ENABLED_RONINS:-bow,sword,brush,arts}"
# NB: no `&& python agentica_core/aggregate.py` here — that path only resolves from
# the Governance dir, never from REPO_DIR (Order Samurai), so the old fallback broke
# the A-prime health gate whenever meditation.env was absent.
VALIDATE_CMD="${VALIDATE_CMD:-python execution/doctor.py}"
MAX_BUDGET_USD="${MAX_BUDGET_USD:-8.00}"   # mid-cycle brake; must match meditation.env
MEDITATION_DRYRUN="${MEDITATION_DRYRUN:-0}"

cd "$REPO_DIR"
MAIN_DIR="$(pwd)"   # absolute main-tree Order Samurai dir — stable even after we cd into a worktree
REPO_ROOT="$(git -C "$MAIN_DIR" rev-parse --show-toplevel)"   # superproject root (for Data/)
[ -f meditation.env ] && set -a && . ./meditation.env && set +a

# Arts output-quality: freshen the llm-judged tool-use metrics once per night (the offline
# scout is too costly for the 15-min dashboard-refresh hot path). Invoked by ABSOLUTE main-tree
# path so it writes the canonical main-tree state/tool_quality.json the dashboard reducers read,
# never a disposable worktree copy. Non-fatal + time-boxed: a scout failure (e.g. Ollama down)
# must never abort the meditation cycle.
# Conservative bounds so the run (incl. the slower qwen faithfulness judge) finishes inside the
# 600s box; a too-large run would be killed mid-way and leave the metrics stale. Override in
# meditation.env if the box is widened.
TOOL_QUALITY_MAX_JUDGMENTS="${TOOL_QUALITY_MAX_JUDGMENTS:-15}" \
TOOL_QUALITY_MAX_TOOL_USES="${TOOL_QUALITY_MAX_TOOL_USES:-20}" \
tmo 600s python3 "$MAIN_DIR/bin/tool_quality_scout.py" >/dev/null 2>&1 \
  || echo "[meditation] tool_quality_scout skipped (nonzero exit or timeout)"

# Self-harness substrate scouts (M2/M5, Research/SELF_HARNESS_EVOLUTION_PLAN.md) — same contract
# as tool_quality_scout above: absolute main-tree path, time-boxed, non-fatal. The weakness miner
# makes ~2 local-LLM calls per failed run (bounded); the trust annotator is pure-deterministic.
tmo 600s python3 "$MAIN_DIR/bin/weakness_mining_scout.py" >/dev/null 2>&1 \
  || echo "[meditation] weakness_mining_scout skipped (nonzero exit or timeout)"
tmo 300s python3 "$MAIN_DIR/bin/tool_trust_annotator.py" >/dev/null 2>&1 \
  || echo "[meditation] tool_trust_annotator skipped (nonzero exit or timeout)"

# Self-harness round (M4) — DARK BY DEFAULT. Runs only when SELF_HARNESS_ENABLED=true is set in
# meditation.env; the script additionally enforces its own budget-ledger and 20h-spacing guards,
# never applies anything (delivery = HITL entry + proposed-surface file for human review), and a
# no-op costs one eval-suite pass. Absolute main-tree path for the same reason as the scouts.
if [ "${SELF_HARNESS_ENABLED:-false}" = "true" ]; then
  tmo 900s python3 "$MAIN_DIR/bin/self_harness_cycle.py" \
    || echo "[meditation] self_harness_cycle skipped (nonzero exit or timeout)"
fi

# Worktree isolation (default ON). When MEDITATION_WORKTREE=1 the whole cycle runs in a
# dedicated git worktree instead of switching the MAIN working tree — so it can run
# concurrently with an interactive session and never clobber its uncommitted work (the
# 2026-07-09 incidents). Set MEDITATION_WORKTREE=0 to fall back to the legacy in-main-tree
# guard behavior (the 292ce81 abort floor).
MEDITATION_WORKTREE="${MEDITATION_WORKTREE:-1}"
WT_DIR="${MEDITATION_WT_DIR:-$HOME/.agentica/meditation-wt}"
_wt_active=0

# Option C — canonical state lives in the MAIN tree, always. The cycle body runs in a disposable
# worktree for git/code isolation, but state/artifacts/Data are read from the MAIN-tree path by
# external consumers (the dashboard-refresh job -> aggregate.py hero metrics, reap_stale_doing,
# the governance scanners — all resolve ORDER_SAMURAI_ROOT to the main tree). If the cycle wrote
# state inside the worktree, those consumers would read a frozen snapshot and every backlog
# completion would be discarded when the branch is force-reset next run. These are absolute
# main-tree paths; in legacy (MEDITATION_WORKTREE=0) mode MAIN_DIR == cwd so they resolve exactly
# as the old relative paths did — behavior byte-for-byte unchanged. Exported so the helper scripts
# and the headless orchestrator write to the same canonical location.
STATE_DIR="${MEDITATION_STATE_DIR:-$MAIN_DIR/state}"
ARTIFACTS_DIR="${MEDITATION_ARTIFACTS_DIR:-$MAIN_DIR/artifacts}"
DATA_DIR="${MEDITATION_DATA_DIR:-$REPO_ROOT/Data}"
export MEDITATION_STATE_DIR="$STATE_DIR" MEDITATION_ARTIFACTS_DIR="$ARTIFACTS_DIR" MEDITATION_DATA_DIR="$DATA_DIR"
export ORDER_SAMURAI_ROOT="$MAIN_DIR"   # env-honoring consumers (aggregate.py, refresh) target the main tree

DATE="$(date +%F)"
BRANCH="ronin/overnight/${DATE}"
DEADLINE=$(( $(date +%s) + RUN_HOURS*3600 ))
PROMPT_FILE="prompts/meditation_cycle.md"
LOGBOOK="$ARTIFACTS_DIR/ronin_logs.md"
mkdir -p "$STATE_DIR" "$STATE_DIR/charters" "$STATE_DIR/logs" "$ARTIFACTS_DIR"

log(){ printf '%s | %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOGBOOK"; }

command -v claude >/dev/null || { echo "claude not found in PATH"; exit 1; }
[ -f "$PROMPT_FILE" ] || { echo "missing $PROMPT_FILE"; exit 1; }
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "not a git repo"; exit 1; }

# Single-instance guard. Two concurrent runs share one branch and the same state/
# files with no coordination — on 2026-07-09 duplicate processes (no lock) raced
# and every SENSEI cycle self-halted before dispatch. macOS /bin/bash 3.2 has no
# flock(1); use an atomic mkdir lock carrying the owner PID plus a liveness check,
# so a crash-leftover lock is reclaimed instead of wedging all future runs.
LOCK_DIR="$STATE_DIR/.meditation.lock"   # absolute (main tree): cleanup must work after we cd into a worktree
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  _lock_pid="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
  if [ -n "${_lock_pid:-}" ] && kill -0 "$_lock_pid" 2>/dev/null; then
    log "ABORT: another meditation run is active (pid $_lock_pid) — exiting to avoid a concurrent-cycle collision."
    exit 1
  fi
  log "Reclaiming stale meditation lock (pid ${_lock_pid:-none} not running)."
  rm -rf "$LOCK_DIR" && mkdir "$LOCK_DIR"
fi
printf '%s\n' "$$" > "$LOCK_DIR/pid"

# Combined EXIT cleanup: free the lock we own AND restore any auto-stash. Set now
# (not after the stash block) so an early abort still releases the lock.
_meditation_cleanup(){
  if [ "${_meditation_stashed:-0}" = "1" ]; then
    if git stash pop >/dev/null 2>&1; then
      log "auto-stash restored."
    else
      log "WARN: auto-stash pop failed (conflict?) — recover with: git stash list / git stash pop"
    fi
  fi
  if [ "${_wt_active:-0}" = "1" ]; then
    git -C "$MAIN_DIR" worktree remove -f "$WT_DIR" 2>/dev/null || true
  fi
  # Only release the lock we OWN. 2026-07-12: an invocation that aborted at the
  # "another run is active" check still fired this trap and deleted the ACTIVE
  # run's lock unconditionally — leaving the live run unprotected against the
  # next invocation reprovisioning over it.
  if [ "$(cat "$LOCK_DIR/pid" 2>/dev/null || true)" = "$$" ]; then
    rm -rf "$LOCK_DIR" 2>/dev/null || true
  fi
}
trap _meditation_cleanup EXIT

# Dirty-tree handling. The old behaviour hard-aborted with a bare `echo` to a log
# nobody watches, so the nightly run silently no-op'd whenever runtime state
# (state/*.json) was dirty — i.e. most nights. Now: abort LOUDLY via log(), and
# offer an opt-in auto-stash/restore so unattended runs can proceed. Default
# (MEDITATION_AUTO_STASH=0) keeps the original safe fail-closed control flow.
MEDITATION_AUTO_STASH="${MEDITATION_AUTO_STASH:-0}"
_meditation_stashed=0
# Never auto-stash into an in-progress merge/rebase/cherry-pick — this exact
# hazard ate a live merge-conflict-resolution's uncommitted work on 2026-07-09
# (a dry-run smoke test hit a conflicted tree, auto-stashed it, and the
# resolution had to be recovered from the stash). A dirty tree from an
# in-progress git operation is a human/agent working on the repo, not routine
# state churn — always fail loudly and let them finish first.
if [ -e .git/MERGE_HEAD ] || [ -e .git/rebase-merge ] || [ -e .git/rebase-apply ] || [ -e .git/CHERRY_PICK_HEAD ]; then
  log "ABORT: a git merge/rebase/cherry-pick is in progress — never auto-stash into it. Finish or abort that operation first."
  exit 1
fi
# Interactive-session guard. Routine overnight churn lives under state/ and artifacts/;
# anything else dirty in the superproject is a live session's uncommitted work. Pathspec
# excludes (not path parsing) keep this quoting-safe despite the space in "Order Samurai".
_top="$(git rev-parse --show-toplevel)"
_rel="$(git rev-parse --show-prefix)"   # e.g. "Governance/Order Samurai/" ("" when REPO_DIR is the repo root)
_significant="$(git -C "$_top" status --porcelain --untracked-files=all --ignore-submodules=all \
  -- . ":(exclude)${_rel}state" ":(exclude)${_rel}artifacts" 2>/dev/null || true)"
if [ "$MEDITATION_WORKTREE" = "1" ]; then
  # Isolation mode: the cycle runs in its own worktree and never switches/stashes the main tree,
  # so a live session's uncommitted work is safe to run alongside. Just note it — do NOT abort or
  # stash (that was the whole point of the worktree). The merge/rebase guard above still fires.
  if [ -n "$_significant" ]; then
    log "NOTE: main tree has uncommitted work outside state/artifacts — running in an ISOLATED worktree, so it is untouched (concurrency-safe). Informational paths:"
    printf '%s\n' "$_significant" | tee -a "$LOGBOOK"
  fi
else
  # Legacy in-main-tree mode: the -u auto-stash swept a session's brand-new untracked files
  # (SHARED_NOTES.md et al.) into a stash on 2026-07-09 and they vanished mid-session. Never
  # auto-stash real work — abort loudly like the merge-guard, regardless of MEDITATION_AUTO_STASH.
  if [ -n "$_significant" ]; then
    log "ABORT: uncommitted work outside state/artifacts (not routine churn) — refusing to auto-stash a live session's work (2026-07-09 incident). Commit or stash it first, or run with MEDITATION_WORKTREE=1. Offending paths:"
    printf '%s\n' "$_significant" | tee -a "$LOGBOOK"
    exit 1
  fi
  if ! git diff --quiet || ! git diff --cached --quiet; then
    if [ "$MEDITATION_AUTO_STASH" = "1" ]; then
      if git stash push -u -m "meditation-overnight auto-stash ${DATE}" >/dev/null 2>&1; then
        _meditation_stashed=1
        log "Working tree was dirty — auto-stashed (MEDITATION_AUTO_STASH=1); restores on exit."
      else
        log "ABORT: working tree dirty and auto-stash FAILED — resolve manually (git status)."; exit 1
      fi
    else
      log "ABORT: working tree is dirty — commit/stash first, or set MEDITATION_AUTO_STASH=1 to auto-stash and proceed."
      exit 1
    fi
  fi
fi
# auto-stash restore is handled by _meditation_cleanup (set above with the lock).
[ -f "$STATE_DIR/MEDITATION_STATE.json" ] || cp "$STATE_DIR/MEDITATION_STATE.seed.json" "$STATE_DIR/MEDITATION_STATE.json" 2>/dev/null || true

# -C (force-create/reset to current HEAD), NOT "-c || switch": the same-day branch
# ronin/overnight/<DATE> persists across runs, so a plain reuse would run on a STALE
# branch from an earlier failed run — with an out-of-date prompt/script/state that
# predates same-day fixes (this silently ran the pre-fix prompt on 2026-07-09). Results
# are cherry-picked to main, so the branch is disposable; always reset it to current HEAD.
if [ "$MEDITATION_WORKTREE" = "1" ]; then
  # Isolation: run the cycle in a dedicated worktree on $BRANCH; the MAIN working tree is
  # never switched or stashed, so an interactive session can run concurrently untouched.
  _prefix="$(git rev-parse --show-prefix)"   # main-tree path from repo root to REPO_DIR
  git -C "$MAIN_DIR" worktree remove -f "$WT_DIR" 2>/dev/null || true
  rm -rf "$WT_DIR" 2>/dev/null || true
  # 2026-07-12: if the worktree DIR was already deleted externally, `worktree remove`
  # no-ops but git's registration still binds $BRANCH to the dead path — the next
  # `worktree add -f -B` dies with "cannot force update the branch ... used by
  # worktree" (this killed the 02:00 scheduled run). Prune reaps dead registrations
  # only; a live worktree is untouched.
  git -C "$MAIN_DIR" worktree prune 2>/dev/null || true
  git -C "$MAIN_DIR" worktree add -f -B "$BRANCH" "$WT_DIR" HEAD
  git -C "$WT_DIR" submodule update --init --recursive 2>/dev/null || true
  _wt_active=1
  cd "$WT_DIR/$_prefix"
  # NB: no `mkdir -p state ...` here — state/artifacts are CANONICAL in the main tree ($STATE_DIR /
  # $ARTIFACTS_DIR, created above) and every writer targets them via env. The worktree's own state/
  # (checked out from HEAD) is left pristine so `git add -A` here only ever stages real code changes.
  log "MEDITATION start (WORKTREE $WT_DIR): branch=$BRANCH (reset to $(git rev-parse --short HEAD)) state->$STATE_DIR enabled=${ENABLED_RONINS}"
else
  # -C (force-create/reset to current HEAD): the same-day branch persists across runs, so a
  # plain reuse would run on a STALE branch. Results are cherry-picked to main; branch is
  # disposable; always reset it to current HEAD.
  git switch -C "$BRANCH"
  log "MEDITATION start: branch=$BRANCH (reset to $(git rev-parse --short HEAD)) enabled=${ENABLED_RONINS}"
fi

# Boundary assertion: every run prints the limits it operates under, so a log line
# claiming "budget reached" (or a $16 cycle) is always auditable against the caps
# that were actually in force — not reconstructed from whichever env was loaded.
log "budgets: daily=\$${DAILY_BUDGET_USD:-UNSET(daily check disabled)} per-cycle-max=\$${MAX_BUDGET_USD} (mid-cycle brake via --max-budget-usd)"

# Requeue items stranded in "doing" by a prior halted run (STEP C skips both "done"
# and "doing", so an interrupted item is invisible to every future cycle otherwise).
# Runs before the loop, on the post-stash committed state, so tonight's cycle can
# re-scan work that crashed/blocked out on earlier nights.
python3 bin/reap_stale_doing.py 2>&1 | tee -a "$LOGBOOK" || true

# Bound the kill-chain event logs (rotates only past 10k lines / 90 days; the
# unmatched log once hit 12,884 rows with no rotation — see
# docs/handoffs/samurai-uplift-2026-07-13.md). Trimmed rows land in
# state/logs/rotated/, which extract_public.py already excludes.
python3 bin/rotate_kill_chain_logs.py 2>&1 | tee -a "$LOGBOOK" || true

# git worktree is REQUIRED: SENSEI Step D dispatches the 4 ronin agents in isolated
# worktrees (state/wt/{bow,sword,brush,arts}). Without it on the allowlist, the
# unattended --permission-mode acceptEdits run cannot approve `git worktree add`
# interactively, so every cycle halted before dispatch and completed zero backlog
# work (2026-07-08/09) — starving both the Agent- and Human-hours heroes.
ALLOWED='Read,Edit,Write,Grep,Glob,Task,Bash(git add:*),Bash(git commit:*),Bash(git status:*),Bash(git diff:*),Bash(git checkout -- :*),Bash(git worktree:*),Bash(./bin/ronin-local:*),Bash(python:*),Bash(python3:*),Bash(pytest:*),Bash(node:*),Bash(jq:*)'

# Empty-array expansion note: expanded as ${BUDGET_FLAG[@]+"..."} below — plain
# "${BUDGET_FLAG[@]}" under `set -u` on macOS /bin/bash 3.2 is a FATAL "unbound
# variable" when the array is empty (killed the 2026-07-08 02:13 overnight run
# at cycle 1 before any work happened).
BUDGET_FLAG=()
[ -n "$MAX_BUDGET_USD" ] && BUDGET_FLAG=(--max-budget-usd "$MAX_BUDGET_USD")
export MEDITATION_ENABLED_RONINS="$ENABLED_RONINS" MEDITATION_VALIDATE_CMD="$VALIDATE_CMD"

# Runtime-path header prepended to the orchestrator prompt. The cycle's cwd is a disposable
# worktree in isolation mode, so the orchestrator MUST write canonical state to the main tree
# (where the dashboard/hero-metric consumers read it) — not under its cwd. The resolved absolute
# dirs are injected here; the prompt body references these names. Built once (paths are constant).
PATH_HEADER="$(cat <<EOF
RUNTIME PATHS (resolved for this invocation — AUTHORITATIVE; they OVERRIDE any relative path in the steps below):
  STATE_DIR                      = $STATE_DIR
  ARTIFACTS_DIR                  = $ARTIFACTS_DIR
  DATA_DIR                       = $DATA_DIR
  ORDER_SAMURAI_ROOT (main tree) = $MAIN_DIR

Your current working directory MAY be a disposable git worktree that is force-reset every run.
For EVERY path the steps below write as \`state/...\`, \`artifacts/...\`, or \`Data/...\`, use the
absolute dir above instead — e.g. \`state/MEDITATION_STATE.json\` => $STATE_DIR/MEDITATION_STATE.json,
\`artifacts/ronin_logs.md\` => $ARTIFACTS_DIR/ronin_logs.md. NEVER write these under your cwd: a
worktree write is silently discarded next run, and the hero metrics read every backlog completion
from $STATE_DIR/MEDITATION_STATE.json in the MAIN tree. The ronin result files and the ronin
sub-worktrees also live under $STATE_DIR (ronin_results/, wt/). The helper scripts
bin/reap_stale_doing.py, bin/keiko_improvement.py, and bin/stamp_meditation_timestamps.py already
honor STATE_DIR via the environment — run them as-is; they write to the main tree automatically.
Code edits and git commits/cherry-picks stay in your worktree cwd (that is the isolation) — only
state/artifacts/Data are redirected to the main tree.
────────────────────────────────────────────────────────────────────────────────────────────────
EOF
)"
CYCLE_PROMPT="$PATH_HEADER

$(cat "$PROMPT_FILE")"

cycle=0
spend_total=0   # running daily spend (USD) for DAILY_BUDGET_USD enforcement
while :; do
  [ -f "$MAIN_DIR/MEDITATION_STOP" ] && { log "MEDITATION_STOP present — halting."; break; }
  now=$(date +%s)
  [ "$now" -ge "$DEADLINE" ] && { log "Deadline reached — halting."; break; }
  cycle=$((cycle+1))
  [ "$cycle" -gt "$MAX_CYCLES" ] && { log "Max cycles reached — halting."; break; }

  log "── cycle $cycle ── ($(( (DEADLINE-now)/60 )) min left)"
  CYCLE_LOG="$STATE_DIR/logs/cycle_${DATE}_$(printf '%03d' "$cycle")"

  set +e
  tmo "${CYCLE_TIMEOUT}s" claude -p "$CYCLE_PROMPT" \
      --allowedTools "$ALLOWED" \
      --permission-mode acceptEdits \
      --max-turns "$MAX_TURNS" \
      ${BUDGET_FLAG[@]+"${BUDGET_FLAG[@]}"} \
      --output-format stream-json --verbose \
      > "${CYCLE_LOG}.json" 2> "${CYCLE_LOG}.err"
  rc=$?
  set -e

  tail -n1 "${CYCLE_LOG}.json" 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print('  result:', d.get('result',''))" \
    2>/dev/null | tee -a "$LOGBOOK" || true

  case $rc in
    0)   log "cycle $cycle ok" ;;
    124) log "cycle $cycle TIMED OUT after ${CYCLE_TIMEOUT}s" ;;
    *)   log "cycle $cycle exited rc=$rc — backing off"; sleep 30 ;;
  esac

  # Daily budget enforcement: DAILY_BUDGET_USD (from meditation.env) was previously DEAD — nothing
  # consulted it (spend was gated only by the optional per-invocation MAX_BUDGET_USD). Now we
  # accumulate each cycle's REAL cost from the stream-json result and halt the keiko once the
  # day's spend reaches the cap. Fails open (cost=0) if the cost field is absent — never blocks blindly.
  cycle_cost=$(tail -n1 "${CYCLE_LOG}.json" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('total_cost_usd') or d.get('cost_usd') or 0)" 2>/dev/null || echo 0)
  spend_total=$(python3 -c "print(${spend_total:-0} + ${cycle_cost:-0})" 2>/dev/null || echo "${spend_total:-0}")
  log "cycle $cycle cost \$${cycle_cost} · day total \$${spend_total}"
  if [ -n "${DAILY_BUDGET_USD:-}" ] && python3 -c "import sys; sys.exit(0 if ${spend_total:-0} >= ${DAILY_BUDGET_USD:-0} else 1)" 2>/dev/null; then
    log "Daily budget reached: \$${spend_total} >= \$${DAILY_BUDGET_USD} — halting keiko."
    break
  fi

  # Loop-until-dry: halt once KEIKO_FLAT_LIMIT consecutive cycles show no metric
  # improvement (exit 3). Call ONCE (it mutates flat_cycle_count); `|| keiko_rc=$?`
  # keeps set -e from aborting on the non-zero halt signal.
  keiko_rc=0
  keiko_out="$(python3 bin/keiko_improvement.py --k "${KEIKO_FLAT_LIMIT:-5}")" || keiko_rc=$?
  printf '%s\n' "$keiko_out" | tee -a "$LOGBOOK"
  [ "$keiko_rc" -eq 3 ] && { log "Early stop: no improvement for ${KEIKO_FLAT_LIMIT:-5} cycles — halting."; break; }

  [ "$MEDITATION_DRYRUN" = "1" ] && { log "DRYRUN — one cycle done, stopping."; break; }
  sleep "$COOLDOWN"
done

# Keiko skill-improvement scan (front half): analyse the eval corpus accumulated by the reflex
# engine and write state/skill_improvement_candidates.json — skills that keep failing even when
# handed a deterministic diagnosis. Read-only, no-op on an empty corpus; the LLM revise-and-ship
# back half (HITL-gated) consumes the candidates file. See docs/CYCLE-TERMINOLOGY.md.
log "keiko: scanning eval corpus for skill-improvement candidates"
python3 bin/skill_improvement_scan.py 2>&1 | tee -a "$LOGBOOK" || true

log "MEDITATION end: $cycle cycles. Review: git log --oneline $BRANCH"
