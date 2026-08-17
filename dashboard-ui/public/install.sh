#!/usr/bin/env bash
set -euo pipefail

# Order Samurai One-Command Installer
# Usage: curl -fsSL https://www.ordersamurai.ai/install.sh | bash
# Or from cloned repo: ./install.sh

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" 2>/dev/null && pwd || echo "" )"
SAMURAI_BIN="${SCRIPT_DIR}/bin/samurai"

if [ -n "${SCRIPT_DIR}" ] && [ -f "${SAMURAI_BIN}" ]; then
  echo "⚔️  Order Samurai Local Installer"
  echo "--------------------------------------------------------"
  chmod +x "${SAMURAI_BIN}"
  python3 "${SAMURAI_BIN}" install
  python3 "${SAMURAI_BIN}" doctor
  echo "--------------------------------------------------------"
  echo "✅ Order Samurai installed and verified successfully!"
else
  echo "⚔️  Order Samurai Web Installer"
  echo "--------------------------------------------------------"
  TMP_DIR="$(mktemp -d)"
  trap 'rm -rf "${TMP_DIR}"' EXIT

  ZIP_URL="https://raw.githubusercontent.com/Gemkai/order-samurai/main/dist/order-samurai-core.zip"
  SHA_URL="https://raw.githubusercontent.com/Gemkai/order-samurai/main/dist/order-samurai-core.zip.sha256"

  echo "  [↓] Downloading order-samurai-core.zip..."
  curl -fsSL "${ZIP_URL}" -o "${TMP_DIR}/order-samurai-core.zip"
  curl -fsSL "${SHA_URL}" -o "${TMP_DIR}/order-samurai-core.zip.sha256"

  echo "  [🔒] Verifying sha256 checksum..."
  (cd "${TMP_DIR}" && shasum -a 256 -c order-samurai-core.zip.sha256)

  TARGET_DIR="${HOME}/.samurai/core"
  mkdir -p "${TARGET_DIR}"
  echo "  [📦] Extracting to ${TARGET_DIR}..."
  unzip -q -o "${TMP_DIR}/order-samurai-core.zip" -d "${TARGET_DIR}"

  chmod +x "${TARGET_DIR}/bin/samurai"
  python3 "${TARGET_DIR}/bin/samurai" install
  python3 "${TARGET_DIR}/bin/samurai" doctor
  echo "--------------------------------------------------------"
  echo "✅ Order Samurai installed and verified successfully!"
fi
