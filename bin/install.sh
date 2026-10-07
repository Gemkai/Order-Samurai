#!/usr/bin/env bash
# install.sh -- one-command install + first blood.
#
# Checks the Python version, installs the runtime dependencies (into a private
# ~/.samurai/venv when the Python is PEP 668 externally managed), then runs
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

# Where the dependencies go. An interpreter that is already a virtualenv (e.g.
# PYTHON=.venv/bin/python) installs into itself. One that is "externally managed"
# (PEP 668: Homebrew, Debian/Ubuntu) refuses pip installs, --user included, so it gets
# a private venv under ~/.samurai. Any other Python keeps the --user install that the
# dashboard API and Dojo rely on when they run plain python3. The hooks never need
# these packages: they are stdlib-only and run on python3, so a venv broken by a
# Python upgrade cannot make Claude Code block tool calls.
PIP_INSTALL_ARGS=(--quiet)
if ! "$PY" -c 'import sys; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)' \
    >/dev/null 2>&1; then
  if "$PY" -c 'import os, sysconfig
marker = os.path.join(sysconfig.get_path("stdlib"), "EXTERNALLY-MANAGED")
raise SystemExit(0 if os.path.isfile(marker) else 1)' >/dev/null 2>&1; then
    VENV="${SAMURAI_HOME:-${HOME:?install.sh: HOME is not set}/.samurai}/venv"
    # Rebuild a venv that no longer runs (e.g. after `brew upgrade python`) or has no
    # pip (a venv creation that failed at ensurepip).
    if ! "$VENV/bin/python" -c 'import sys, pip; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
        >/dev/null 2>&1; then
      echo "install.sh: $PY is externally managed; creating the private environment ${VENV}..."
      if ! "$PY" -m venv --clear "$VENV"; then
        echo "install.sh: could not create ${VENV} with $PY -m venv" \
          "(Debian/Ubuntu: install the python3-venv package and re-run)." >&2
        exit 1
      fi
    fi
    PY="$VENV/bin/python"
  else
    PIP_INSTALL_ARGS+=(--user)
  fi
fi

ensure_dependency() {
  local module="$1"
  local requirement="$2"

  if "$PY" -c "import ${module}" >/dev/null 2>&1; then
    return 0
  fi

  echo "install.sh: installing runtime dependency (${requirement})..."
  "$PY" -m pip install "${PIP_INSTALL_ARGS[@]}" "$requirement"

  # A zero pip exit is not enough: user-site loading may be disabled or pip may
  # target a different environment. Verify the exact interpreter this installer
  # and the scheduled scanner use can import the dependency before continuing.
  if ! "$PY" -c "import ${module}" >/dev/null 2>&1; then
    echo "install.sh: ${requirement} is still unavailable to $PY after installation." >&2
    exit 1
  fi
}

ensure_dependency jsonschema 'jsonschema>=4.0'
ensure_dependency requests 'requests>=2.31'
ensure_dependency pip_audit 'pip-audit>=2.7'

echo "install.sh: scanning Claude Code session logs..."
PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}" \
  exec "$PY" "$HERE/bin/first_blood.py" "$@"
