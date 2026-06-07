#!/usr/bin/env bash
# ronin-daemon.sh — 24/7 autonomous daemon for the Order Samurai Dojo.
# No time or cycle cap. Runs until DOJO_STOP, daily budget exceeded, or
# MAX_CONSECUTIVE_FAILS consecutive failures.
#
# Usage:
#   DOJO_STOP file          — place in repo root to halt gracefully
#   bin/ronin promote       — approve backlog proposals so daemon can continue
#
# Env var defaults (override via dojo.env or shell export):
#   DAILY_BUDGET_USD=5.00   MAX_CONSECUTIVE_FAILS=5   CYCLE_TIMEOUT=1200
#   MAX_TURNS=30            COOLDOWN=15
set -euo pipefail

# ---------------------------------------------------------------------------
# Config & defaults
# ---------------------------------------------------------------------------
REPO_DIR="${REPO_DIR:-$(pwd)}"
DAILY_BUDGET_USD="${DAILY_BUDGET_USD:-5.00}"
MAX_CONSECUTIVE_FAILS="${MAX_CONSECUTIVE_FAILS:-5}"
CYCLE_TIMEOUT="${CYCLE_TIMEOUT:-1200}"
MAX_TURNS="${MAX_TURNS:-80}"      # 4 parallel ronins × ~15 turns each + Sensei overhead
COOLDOWN="${COOLDOWN:-15}"
CYCLE_COST_USD="${CYCLE_COST_USD:-0.08}"   # 4 ronins × ~$0.02 each per cycle
ENABLED_RONINS="${ENABLED_RONINS:-bow,sword,brush,arts}"
VALIDATE_CMD="${VALIDATE_CMD:-python execution/doctor.py && python agentica_core/aggregate.py}"
DOJO_DRYRUN="${DOJO_DRYRUN:-0}"

cd "$REPO_DIR"

# Load dojo.env if present (set -a exports every assignment; set +a restores)
[ -f dojo.env ] && set -a && . ./dojo.env && set +a

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROMPT_FILE="prompts/dojo_cycle.md"
LOGBOOK="artifacts/ronin_logs.md"
STATE_DIR="state"
BUDGET_LEDGER="${STATE_DIR}/budget_ledger.json"
WARN_BASELINE="${STATE_DIR}/daemon_warn_baseline.txt"
DOJO_STATE="${STATE_DIR}/DOJO_STATE.json"
DATE="$(date +%F)"

mkdir -p "${STATE_DIR}" "${STATE_DIR}/logs" artifacts

# ---------------------------------------------------------------------------
# Logging helper
# ---------------------------------------------------------------------------
log() { printf '%s | %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOGBOOK"; }

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
command -v claude >/dev/null 2>&1 || { echo "claude not found in PATH"; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 not found in PATH"; exit 1; }
[ -f "$PROMPT_FILE" ] || { echo "missing $PROMPT_FILE"; exit 1; }
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "not a git repo"; exit 1; }
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "Working tree is dirty. Commit or stash first."; exit 1
fi
[ -f "$DOJO_STATE" ] || cp "${STATE_DIR}/DOJO_STATE.seed.json" "$DOJO_STATE" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Budget ledger helpers
# ---------------------------------------------------------------------------

# Initialise ledger for today if missing or stale
init_budget_ledger() {
    local today; today="$(date +%F)"
    # If file exists and today's entry is already there, do nothing
    if [ -f "$BUDGET_LEDGER" ]; then
        local stored_date
        stored_date="$(python3 -c "
import json, sys
try:
    d = json.load(open('${BUDGET_LEDGER}'))
    print(d.get('date',''))
except Exception:
    print('')
" 2>/dev/null || echo '')"
        [ "$stored_date" = "$today" ] && return 0
    fi
    # Create/reset for today
    python3 - <<PYEOF
import json
ledger = {"date": "${today}", "spent_usd": 0.0, "daily_limit_usd": ${DAILY_BUDGET_USD}, "cycles": 0}
with open("${BUDGET_LEDGER}", "w") as f:
    json.dump(ledger, f, indent=2)
PYEOF
    log "Budget ledger reset for ${today} (limit: \$${DAILY_BUDGET_USD})"
}

# Returns 0 if under budget, 1 if at/over
check_budget() {
    python3 - <<PYEOF
import json, sys
try:
    d = json.load(open("${BUDGET_LEDGER}"))
    spent = float(d.get("spent_usd", 0))
    limit = float(d.get("daily_limit_usd", ${DAILY_BUDGET_USD}))
    sys.exit(0 if spent < limit else 1)
except Exception:
    sys.exit(0)
PYEOF
}

# Add cost of one cycle to the ledger
record_cycle_cost() {
    python3 - <<PYEOF
import json
try:
    with open("${BUDGET_LEDGER}") as f:
        d = json.load(f)
    d["spent_usd"] = round(float(d.get("spent_usd", 0)) + ${CYCLE_COST_USD}, 4)
    d["cycles"] = int(d.get("cycles", 0)) + 1
    with open("${BUDGET_LEDGER}", "w") as f:
        json.dump(d, f, indent=2)
except Exception as e:
    print(f"budget ledger update failed: {e}")
PYEOF
}

budget_spent() {
    python3 -c "
import json
try:
    d = json.load(open('${BUDGET_LEDGER}'))
    print(round(float(d.get('spent_usd',0)),4))
except Exception:
    print('?')
" 2>/dev/null || echo '?'
}

# ---------------------------------------------------------------------------
# WARN baseline helpers
# ---------------------------------------------------------------------------

# Read current WARN count from doctor.py output (returns integer)
count_warns() {
    python3 -c "
import subprocess, re, sys
try:
    out = subprocess.run('${VALIDATE_CMD}', shell=True,
                         capture_output=True, text=True, timeout=120).stdout
    warns = len(re.findall(r'\\bWARN\\b', out))
    print(warns)
except Exception:
    print(-1)
" 2>/dev/null || echo -1
}

# Read the saved WARN baseline (returns integer, 0 if missing)
read_warn_baseline() {
    if [ -f "$WARN_BASELINE" ]; then
        cat "$WARN_BASELINE" 2>/dev/null || echo 0
    else
        echo 0
    fi
}

# Write a new WARN baseline
write_warn_baseline() {
    printf '%s\n' "$1" > "$WARN_BASELINE"
}

# ---------------------------------------------------------------------------
# Backlog gate helpers
# ---------------------------------------------------------------------------

# Returns 0 if at least one approved item exists, 1 otherwise
has_approved_backlog() {
    python3 - <<PYEOF
import json, sys
try:
    with open("${DOJO_STATE}") as f:
        state = json.load(f)
    items = state.get("backlog", [])
    approved = [i for i in items if str(i.get("status","")).lower() == "approved"]
    sys.exit(0 if approved else 1)
except Exception:
    # If we can't read state, don't block the daemon
    sys.exit(0)
PYEOF
}

# ---------------------------------------------------------------------------
# Tool allowlist (matches dojo_overnight.sh)
# ---------------------------------------------------------------------------
ALLOWED='Read,Edit,Write,Grep,Glob,Task,Bash(git add:*),Bash(git commit:*),Bash(git status:*),Bash(git diff:*),Bash(git checkout -- :*),Bash(./bin/ronin-local:*),Bash(python:*),Bash(python3:*),Bash(pytest:*),Bash(node:*),Bash(jq:*)'

export DOJO_ENABLED_RONINS="$ENABLED_RONINS" DOJO_VALIDATE_CMD="$VALIDATE_CMD"

# ---------------------------------------------------------------------------
# Daemon startup
# ---------------------------------------------------------------------------
log "RONIN DAEMON start: daily_budget=\$${DAILY_BUDGET_USD} max_fails=${MAX_CONSECUTIVE_FAILS} timeout=${CYCLE_TIMEOUT}s"

init_budget_ledger

# Capture pre-run WARN baseline (before first cycle)
PRE_WARNS="$(count_warns)"
write_warn_baseline "$PRE_WARNS"
log "Initial WARN baseline: ${PRE_WARNS}"

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
cycle=0
consecutive_fails=0

while :; do

    # ── 1. Stop-file gate ────────────────────────────────────────────────────
    if [ -f DOJO_STOP ]; then
        log "DOJO_STOP present — halting daemon."
        break
    fi

    # ── 2. Daily budget gate (reset ledger if day rolled over) ───────────────
    init_budget_ledger   # no-op unless date changed
    if ! check_budget; then
        SPENT="$(budget_spent)"
        log "Daily budget \$${DAILY_BUDGET_USD} reached (spent \$${SPENT}) — halting until tomorrow."
        break
    fi

    # ── 3. Consecutive failure gate ──────────────────────────────────────────
    if [ "$consecutive_fails" -ge "$MAX_CONSECUTIVE_FAILS" ]; then
        log "MAX_CONSECUTIVE_FAILS (${MAX_CONSECUTIVE_FAILS}) reached — halting."
        break
    fi

    # ── 4. Backlog gate ──────────────────────────────────────────────────────
    if ! has_approved_backlog; then
        log "Backlog empty — running replenish_backlog.py ..."
        set +e
        python3 bin/replenish_backlog.py >> "$LOGBOOK" 2>&1
        set -e
        log "Backlog empty, proposals written, awaiting human: bin/ronin promote"
        break
    fi

    # ── 5. Capture WARN count BEFORE cycle ───────────────────────────────────
    PREV_WARNS="$(read_warn_baseline)"

    # ── 6. Run cycle ─────────────────────────────────────────────────────────
    cycle=$((cycle+1))
    log "── cycle ${cycle} ── (spent \$$(budget_spent) / \$${DAILY_BUDGET_USD}, fails=${consecutive_fails}/${MAX_CONSECUTIVE_FAILS})"

    CYCLE_LOG="${STATE_DIR}/logs/cycle_${DATE}_$(printf '%03d' "$cycle")"

    set +e
    timeout "${CYCLE_TIMEOUT}s" claude -p "$(cat "$PROMPT_FILE")" \
        --allowedTools "$ALLOWED" \
        --permission-mode acceptEdits \
        --max-turns "$MAX_TURNS" \
        --output-format stream-json --verbose \
        > "${CYCLE_LOG}.json" 2> "${CYCLE_LOG}.err"
    rc=$?
    set -e

    # Extract result summary from last JSON line
    CYCLE_RESULT="$(tail -n1 "${CYCLE_LOG}.json" 2>/dev/null \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('result','(no result)'))" \
        2>/dev/null || echo '(parse error)')"

    # ── 7. WARN ratchet ──────────────────────────────────────────────────────
    POST_WARNS="$(count_warns)"

    if [ "$POST_WARNS" -gt "$PREV_WARNS" ] 2>/dev/null; then
        log "WARN ratchet triggered: ${PREV_WARNS} -> ${POST_WARNS} WARNs — discarding cycle (git checkout -- .)"
        git checkout -- . 2>/dev/null || true
        consecutive_fails=$((consecutive_fails+1))
        printf '%s | cycle %d | WARN_RATCHET warns=%d->%d rc=%d\n' \
            "$(date '+%F %T')" "$cycle" "$PREV_WARNS" "$POST_WARNS" "$rc" >> "$LOGBOOK"
        sleep "$COOLDOWN"
        continue
    fi

    # WARN baseline stays at prev value (or improves)
    write_warn_baseline "$POST_WARNS"

    # ── 8. Cost accounting ───────────────────────────────────────────────────
    record_cycle_cost

    # ── 9. Result classification ─────────────────────────────────────────────
    case $rc in
        0)
            log "cycle ${cycle} ok — ${CYCLE_RESULT}"
            printf '%s | cycle %d | OK rc=0 result=%s\n' \
                "$(date '+%F %T')" "$cycle" "$CYCLE_RESULT" >> "$LOGBOOK"
            consecutive_fails=0
            ;;
        124)
            log "cycle ${cycle} TIMED OUT after ${CYCLE_TIMEOUT}s"
            printf '%s | cycle %d | TIMEOUT rc=124\n' \
                "$(date '+%F %T')" "$cycle" >> "$LOGBOOK"
            consecutive_fails=$((consecutive_fails+1))
            ;;
        *)
            log "cycle ${cycle} FAILED rc=${rc} — backing off 30s"
            printf '%s | cycle %d | FAIL rc=%d result=%s\n' \
                "$(date '+%F %T')" "$cycle" "$rc" "$CYCLE_RESULT" >> "$LOGBOOK"
            consecutive_fails=$((consecutive_fails+1))
            sleep 30
            ;;
    esac

    # ── 10. Dry-run early exit ────────────────────────────────────────────────
    if [ "$DOJO_DRYRUN" = "1" ]; then
        log "DRYRUN — one cycle done, stopping."
        break
    fi

    sleep "$COOLDOWN"
done

log "RONIN DAEMON end: ${cycle} cycles run, \$$(budget_spent) spent. consecutive_fails=${consecutive_fails}"
