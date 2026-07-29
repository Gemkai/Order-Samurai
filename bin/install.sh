#!/usr/bin/env bash
# install.sh -- one-command install + first blood.
#
# Checks the Python version, installs the one runtime dependency (jsonschema), then runs
# first_blood.py against your existing Claude Code session logs so the first cost report
# appears in this same command -- no daemon, no account, no separate onboarding step.
#
#   ./bin/install.sh                  # scan ~/.claude/projects
#   ./bin/install.sh --logs-dir DIR   # scan a different transcript root
#
# wargames/03-order-samurai-commercialization.md Move 1 (R6): this is the timed
# clean-machine path -- `time ./bin/install.sh` measures the PRD G1 gate (<=10 min).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "install.sh: python3 not found on PATH -- install Python 3.11+ and re-run." >&2
  exit 1
fi

PY_OK="$("$PY" -c 'import sys; print(1 if sys.version_info >= (3, 11) else 0)' 2>/dev/null || echo 0)"
if [ "$PY_OK" != "1" ]; then
  echo "install.sh: Python 3.11+ required ($("$PY" --version 2>&1) found)." >&2
  exit 1
fi

if ! "$PY" -c 'import jsonschema' >/dev/null 2>&1; then
  echo "install.sh: installing runtime dependency (jsonschema)..."
  "$PY" -m pip install --quiet --user jsonschema
fi

echo "install.sh: scanning Claude Code session logs..."
exec "$PY" "$HERE/bin/first_blood.py" "$@"
