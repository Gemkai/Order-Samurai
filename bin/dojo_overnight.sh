#!/usr/bin/env bash
# dojo_overnight.sh — the 6-hour autonomous engine for the Order Samurai Dojo.
set -euo pipefail
REPO_DIR="${REPO_DIR:-$(pwd)}"
RUN_HOURS="${RUN_HOURS:-6}"
MAX_CYCLES="${MAX_CYCLES:-60}"
# Fallback literals MUST match dojo.env (the single source) so a missing dojo.env can
# never silently run with different limits than a present one. See dojo.env.
MAX_TURNS="${MAX_TURNS:-80}"
CYCLE_TIMEOUT="${CYCLE_TIMEOUT:-2400}"
COOLDOWN="${COOLDOWN:-15}"
ENABLED_RONINS="${ENABLED_RONINS:-bow,sword,brush,arts}"
VALIDATE_CMD="${VALIDATE_CMD:-python execution/doctor.py && python agentica_core/aggregate.py}"
MAX_BUDGET_USD="${MAX_BUDGET_USD:-}"
DOJO_DRYRUN="${DOJO_DRYRUN:-0}"

cd "$REPO_DIR"
[ -f dojo.env ] && set -a && . ./dojo.env && set +a

DATE="$(date +%F)"
BRANCH="ronin/overnight/${DATE}"
DEADLINE=$(( $(date +%s) + RUN_HOURS*3600 ))
PROMPT_FILE="prompts/dojo_cycle.md"
LOGBOOK="artifacts/ronin_logs.md"
mkdir -p state state/charters state/logs artifacts

log(){ printf '%s | %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOGBOOK"; }

command -v claude >/dev/null || { echo "claude not found in PATH"; exit 1; }
[ -f "$PROMPT_FILE" ] || { echo "missing $PROMPT_FILE"; exit 1; }
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "not a git repo"; exit 1; }
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Working tree is dirty. Commit or stash first."; exit 1
fi
[ -f state/DOJO_STATE.json ] || cp state/DOJO_STATE.seed.json state/DOJO_STATE.json 2>/dev/null || true

git switch -c "$BRANCH" 2>/dev/null || git switch "$BRANCH"
log "DOJO start: branch=$BRANCH enabled=${ENABLED_RONINS}"

ALLOWED='Read,Edit,Write,Grep,Glob,Task,Bash(git add:*),Bash(git commit:*),Bash(git status:*),Bash(git diff:*),Bash(git checkout -- :*),Bash(./bin/ronin-local:*),Bash(python:*),Bash(python3:*),Bash(pytest:*),Bash(node:*),Bash(jq:*)'

BUDGET_FLAG=()
[ -n "$MAX_BUDGET_USD" ] && BUDGET_FLAG=(--max-budget-usd "$MAX_BUDGET_USD")
export DOJO_ENABLED_RONINS="$ENABLED_RONINS" DOJO_VALIDATE_CMD="$VALIDATE_CMD"

cycle=0
spend_total=0   # running daily spend (USD) for DAILY_BUDGET_USD enforcement
while :; do
  [ -f DOJO_STOP ] && { log "DOJO_STOP present — halting."; break; }
  now=$(date +%s)
  [ "$now" -ge "$DEADLINE" ] && { log "Deadline reached — halting."; break; }
  cycle=$((cycle+1))
  [ "$cycle" -gt "$MAX_CYCLES" ] && { log "Max cycles reached — halting."; break; }

  log "── cycle $cycle ── ($(( (DEADLINE-now)/60 )) min left)"
  CYCLE_LOG="state/logs/cycle_${DATE}_$(printf '%03d' "$cycle")"

  set +e
  timeout "${CYCLE_TIMEOUT}s" claude -p "$(cat "$PROMPT_FILE")" \
      --allowedTools "$ALLOWED" \
      --permission-mode acceptEdits \
      --max-turns "$MAX_TURNS" \
      "${BUDGET_FLAG[@]}" \
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

  # Daily budget enforcement: DAILY_BUDGET_USD (from dojo.env) was previously DEAD — nothing
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

  [ "$DOJO_DRYRUN" = "1" ] && { log "DRYRUN — one cycle done, stopping."; break; }
  sleep "$COOLDOWN"
done

log "DOJO end: $cycle cycles. Review: git log --oneline $BRANCH"
