#!/bin/bash
# Register the macOS launchd job that runs the overnight meditation engine.
# Fires nightly at 02:00 and runs meditation_overnight.sh (6-hour cycle loop working the
# approved backlog: reflex remediation, backlog items, calibration samples). This is
# the scheduler counterpart to armed ronin mode — ronin only auto-fires on score
# REGRESSION; this job works pending backlog while the operator sleeps.
#
#   ./register_meditation_overnight.sh            install (or reinstall)
#   ./register_meditation_overnight.sh --status   show job state
#   ./register_meditation_overnight.sh --remove   unload + delete the plist
#
# Mac counterpart of the Windows-era register_sensei_task.ps1.
set -euo pipefail

LABEL="com.agentica.meditation-overnight"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
# Always the LIVE repo, never a worktree copy.
OS_ROOT="${SAMURAI_HOME:-$(cd "$(dirname "$0")/.." && pwd)}"
[ -f "$OS_ROOT/bin/meditation_overnight.sh" ] || { echo "missing $OS_ROOT/bin/meditation_overnight.sh" >&2; exit 1; }
LOG_DIR="$HOME/Library/Logs/agentica"

# launchd has no login-shell PATH — claude/python3/node live under mise.
MISE_NODE="$HOME/.local/share/mise/installs/node/latest/bin"
MISE_PY="$HOME/.local/share/mise/installs/python/latest/bin"
JOB_PATH="$MISE_NODE:$MISE_PY:/usr/bin:/bin:/usr/sbin:/sbin"

case "${1:-}" in
  --status)
    launchctl list "$LABEL" 2>/dev/null || echo "$LABEL: not loaded"
    exit 0 ;;
  --remove)
    launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo "$LABEL removed"
    exit 0 ;;
esac

mkdir -p "$LOG_DIR" "$(dirname "$PLIST")"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string>
    <string>$OS_ROOT/bin/meditation_overnight.sh</string>
  </array>
  <key>WorkingDirectory</key><string>$OS_ROOT</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$JOB_PATH</string>
    <key>REPO_DIR</key><string>$OS_ROOT</string>
    <!-- unattended runs: auto-stash the ever-churning meditation state files (restored on
         exit) instead of refusing on a dirty tree - manual runs stay fail-closed -->
    <key>MEDITATION_AUTO_STASH</key><string>1</string>
  </dict>
  <key>StartCalendarInterval</key><dict>
    <key>Hour</key><integer>2</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
  <key>StandardOutPath</key><string>$LOG_DIR/meditation-overnight.out.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/meditation-overnight.err.log</string>
</dict></plist>
EOF

launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "$LABEL registered — nightly 02:00, 6h loop -> $OS_ROOT/bin/meditation_overnight.sh"
echo "smoke test now: launchctl kickstart gui/$(id -u)/$LABEL   (runs a full 6h loop!)"
