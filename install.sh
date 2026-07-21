#!/usr/bin/env bash
set -euo pipefail

# Order Samurai One-Command Installer
# Usage: curl -fsSL https://raw.githubusercontent.com/order-samurai/order-samurai/main/install.sh | bash

echo "⚔️  Order Samurai One-Command Installer"
echo "--------------------------------------------------------"

# 1. Determine directory location
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
SAMURAI_BIN="${SCRIPT_DIR}/bin/samurai"

if [ ! -f "${SAMURAI_BIN}" ]; then
  echo "❌ Error: samurai binary not found at ${SAMURAI_BIN}"
  exit 1
fi

chmod +x "${SAMURAI_BIN}"

# 2. Run samurai install
python3 "${SAMURAI_BIN}" install

# 3. Run samurai doctor verification
python3 "${SAMURAI_BIN}" doctor

echo "--------------------------------------------------------"
echo "✅ Order Samurai installed and verified successfully!"
