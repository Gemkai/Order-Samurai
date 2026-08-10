#!/usr/bin/env bash
set -euo pipefail

# Order Samurai One-Command Installer
# Usage: curl -fsSL https://www.ordersamurai.ai/install.sh | bash
# Or from cloned repo: ./install.sh

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
SAMURAI_BIN="${SCRIPT_DIR}/bin/samurai"

if [ -f "${SAMURAI_BIN}" ]; then
  echo "⚔️  Order Samurai Local Installer"
  echo "--------------------------------------------------------"
  chmod +x "${SAMURAI_BIN}"
  python3 "${SAMURAI_BIN}" install
  python3 "${SAMURAI_BIN}" doctor
  echo "--------------------------------------------------------"
  echo "✅ Order Samurai installed and verified successfully!"
else
  # Delegate to canonical web installer when fetched remotely
  exec curl -fsSL https://www.ordersamurai.ai/install.sh | bash "$@"
fi
