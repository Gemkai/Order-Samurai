#!/usr/bin/env bash
# lib_pro_gate.sh — shared Order Samurai Pro entitlement gate for shell entrypoints.
#
# The Nightly Dojo and the ronin daemon are Pro features. This gate reads the SAME
# ~/.samurai/license.json that `samurai activate` writes (agentica_core/licensing.py)
# and fails CLOSED to Free: any absence/parse-error/non-active status blocks the run.
# Source this and call `require_pro "<feature name>"` at the top of a Pro entrypoint.
#
# Escape hatch for source builds / CI: SAMURAI_PRO_OVERRIDE=1 bypasses the gate (never
# document this to customers — it's for maintainers running the pack against itself).

_samurai_home() { printf '%s' "${SAMURAI_HOME:-$HOME/.samurai}"; }

# is_pro: exit 0 when a valid, active, non-refunded Pro entitlement exists; else 1.
is_pro() {
  [ "${SAMURAI_PRO_OVERRIDE:-}" = "1" ] && return 0
  local lic; lic="$(_samurai_home)/license.json"
  [ -f "$lic" ] || return 1
  # Delegate to the Python authority so the JSON contract lives in exactly one place.
  python3 - "$lic" <<'PY' 2>/dev/null
import json, sys
try:
    e = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
ok = (e.get("tier") == "pro" and e.get("valid") is True
      and e.get("status") == "active" and not e.get("refunded", False))
sys.exit(0 if ok else 1)
PY
}

# require_pro "<feature>": run the feature or print an upgrade notice and exit 2.
require_pro() {
  local feature="${1:-This feature}"
  if is_pro; then
    return 0
  fi
  cat >&2 <<EOF
⚔️  $feature is an Order Samurai Pro feature.
    Free tier includes four-pillar scoring + fail-closed CLI security hooks.
    Unlock Pro:  samurai activate <license-key>
    Buy a key:   https://ordersamurai.lemonsqueezy.com  (14-day money-back guarantee)
EOF
  exit 2
}
