#!/usr/bin/env bash
set -euo pipefail

# Order Samurai One-Command Installer
# Usage: curl -fsSL https://www.ordersamurai.ai/install.sh | bash
# Or from cloned repo: ./install.sh

if ! command -v python3 >/dev/null 2>&1 || \
   ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  echo "❌ Order Samurai needs Python 3.11 or newer." >&2
  echo "   Install it from https://www.python.org/downloads/ (macOS: the 'macOS 64-bit universal2 installer')," >&2
  echo "   then open a NEW Terminal window and run this command again." >&2
  exit 1
fi

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" 2>/dev/null && pwd || echo "" )"
SAMURAI_BIN="${SCRIPT_DIR}/bin/samurai"

if [ -n "${SCRIPT_DIR}" ] && [ -f "${SAMURAI_BIN}" ]; then
  echo "⚔️  Order Samurai Local Installer"
  echo "--------------------------------------------------------"
  chmod +x "${SAMURAI_BIN}"
  python3 "${SAMURAI_BIN}" install </dev/null
  python3 "${SAMURAI_BIN}" doctor </dev/null
  echo "--------------------------------------------------------"
  echo "✅ Order Samurai installed and verified successfully!"
  echo "   Feedback or problems: support@agentica-llc.biz"
else
  echo "⚔️  Order Samurai Web Installer"
  echo "--------------------------------------------------------"
  TMP_DIR="$(mktemp -d)"
  trap 'rm -rf "${TMP_DIR}"' EXIT

  BASE_URL="${OS_CORE_BASE_URL:-https://raw.githubusercontent.com/Gemkai/order-samurai/main/dist}"
  ZIP_URL="${BASE_URL}/order-samurai-core.zip"
  SHA_URL="${BASE_URL}/order-samurai-core.zip.sha256"

  download_pair() {
    curl -fsSL "${ZIP_URL}$1" -o "${TMP_DIR}/order-samurai-core.zip"
    curl -fsSL "${SHA_URL}$1" -o "${TMP_DIR}/order-samurai-core.zip.sha256"
  }
  checksum_ok() {
    (cd "${TMP_DIR}" && shasum -a 256 -c order-samurai-core.zip.sha256 >/dev/null 2>&1)
  }

  echo "  [↓] Downloading order-samurai-core.zip..."
  download_pair ""

  echo "  [🔒] Verifying sha256 checksum..."
  # The CDN caches the zip and its .sha256 separately, so right after a release one can
  # be stale. Re-download both, bypassing the cache, before giving up. A zip that never
  # matches its published checksum is still refused.
  ATTEMPT=1
  while ! checksum_ok && [ "${ATTEMPT}" -lt 3 ]; do
    ATTEMPT=$((ATTEMPT + 1))
    echo "  [!] Checksum did not match; downloading again (attempt ${ATTEMPT} of 3)..."
    sleep "${OS_SHA_RETRY_DELAY:-3}"
    case "${BASE_URL}" in
      http://*|https://*) download_pair "?v=$(date +%s)-${ATTEMPT}" ;;
      *) download_pair "" ;;
    esac
  done
  if ! checksum_ok; then
    echo "" >&2
    echo "  [✗] CHECKSUM MISMATCH -- ABORTING INSTALL." >&2
    echo "  The downloaded archive does not match its published SHA-256." >&2
    echo "  This could mean a corrupted download or a tampered file." >&2
    echo "  Nothing has been extracted or executed." >&2
    exit 1
  fi
  echo "  [✓] Checksum verified"

  TARGET_DIR="${HOME}/.samurai/core"
  mkdir -p "${TARGET_DIR}"
  echo "  [📦] Extracting to ${TARGET_DIR}..."
  unzip -q -o "${TMP_DIR}/order-samurai-core.zip" -d "${TARGET_DIR}"

  chmod +x "${TARGET_DIR}/bin/samurai"
  # </dev/null: under curl | bash stdin is this script; the key prompt reads /dev/tty.
  python3 "${TARGET_DIR}/bin/samurai" install </dev/null
  python3 "${TARGET_DIR}/bin/samurai" doctor </dev/null
  echo "--------------------------------------------------------"
  echo "✅ Order Samurai installed and verified successfully!"
  echo "   Feedback or problems: support@agentica-llc.biz"
fi
