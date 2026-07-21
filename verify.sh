#!/usr/bin/env bash
# Quality gate for Agentica Governance.
#   ./verify.sh         full gate (pytest + tsc + eslint + optional vitest)
#   ./verify.sh --fast  fast subset (pytest + tsc)
set -u

GOV="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UI="$GOV/dashboard-ui"
FAIL=0
FAST=0
[ "${1:-}" = "--fast" ] && FAST=1

step() {
  echo
  echo "=== $1 ==="
  shift
  "$@" && echo "ok" || { echo "FAIL"; FAIL=1; }
}

step "pytest (kernel)" bash -c "cd '$GOV' && python -m pytest agentica_core/tests -q"
step "tsc (dashboard)" bash -c "cd '$UI' && npx tsc --noEmit"

if [ "$FAST" -eq 0 ]; then
  step "eslint" bash -c "cd '$UI' && npm run lint"
  if [ -f "$UI/vitest.config.ts" ]; then
    step "vitest (frontend)" bash -c "cd '$UI' && npm run test:run"
  else
    echo
    echo "(skip vitest - not configured yet)"
  fi
fi

[ "$FAIL" -ne 0 ] && { echo; echo "VERIFY FAILED"; exit 1; }
echo
echo "VERIFY PASSED"
exit 0
