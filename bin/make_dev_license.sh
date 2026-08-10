#!/usr/bin/env bash
# make_dev_license.sh — write a simulated Pro license.json for maintainer/CI use.
#
# Replaces the old undocumented env-var gate bypass. Instead of skipping the
# entitlement check, this writes a real ~/.samurai/license.json (or
# $SAMURAI_HOME/license.json) that the SAME fail-closed reader
# (agentica_core/licensing.py, bin/lib_pro_gate.sh) accepts as valid — but it is
# explicitly marked "simulated": true so it can never be confused with a real
# customer entitlement.
set -euo pipefail

samurai_home="${SAMURAI_HOME:-$HOME/.samurai}"
mkdir -p "$samurai_home"
lic="$samurai_home/license.json"

cat > "$lic" <<'JSON'
{
  "tier": "pro",
  "valid": true,
  "status": "active",
  "simulated": true,
  "provider": "dev"
}
JSON
chmod 600 "$lic"

echo "Wrote simulated dev Pro license: $lic" >&2
