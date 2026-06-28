#!/usr/bin/env bash
set -euo pipefail

# Install MiniFleet worker on any Mac (Mini, MacBook, Studio, iMac, etc.)
# Usage: MINIFLEET_NODE_NAME=macbook-pro MINIFLEET_COORDINATOR=http://YOUR-HEAD-MAC.local:8787 ./scripts/setup-worker.sh

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="${MINIFLEET_DATA:-$HOME/.minifleet}"
NODE_NAME="${MINIFLEET_NODE_NAME:-}"
COORDINATOR="${MINIFLEET_COORDINATOR:-http://127.0.0.1:8787}"
MAX_CONCURRENT="${MINIFLEET_MAX_CONCURRENT:-0}"

if [[ -z "$NODE_NAME" ]]; then
  echo "Set MINIFLEET_NODE_NAME (examples: mac-mini-1, macbook-pro, macbook-air-kitchen)" >&2
  echo "" >&2
  "$ROOT/scripts/detect-device.sh" 2>/dev/null || true
  exit 1
fi

DEVICE_TYPE="${MINIFLEET_DEVICE_TYPE:-$(python3 -c 'from minifleet.device import detect_device_type; print(detect_device_type())')}"

echo "==> Installing MiniFleet worker: $NODE_NAME ($DEVICE_TYPE)"
python3 -m pip install -e "$ROOT" --quiet
mkdir -p "$DATA_DIR"

PLIST="$HOME/Library/LaunchAgents/com.minifleet.worker.plist"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.minifleet.worker</string>
  <key>ProgramArguments</key>
  <array>
    <string>$(which python3)</string>
    <string>-m</string>
    <string>minifleet.worker.main</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>MINIFLEET_DATA</key>
    <string>$DATA_DIR</string>
    <key>MINIFLEET_NODE_NAME</key>
    <string>$NODE_NAME</string>
    <key>MINIFLEET_COORDINATOR</key>
    <string>$COORDINATOR</string>
    <key>MINIFLEET_MAX_CONCURRENT</key>
    <string>$MAX_CONCURRENT</string>
    <key>MINIFLEET_DEVICE_TYPE</key>
    <string>$DEVICE_TYPE</string>
    <key>MINIFLEET_PERMISSION_MODE</key>
    <string>${MINIFLEET_PERMISSION_MODE:-auto}</string>
    <key>GITHUB_TOKEN</key>
    <string>${GITHUB_TOKEN:-}</string>
    <key>PATH</key>
    <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:$HOME/.local/bin</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$DATA_DIR/worker.log</string>
  <key>StandardErrorPath</key>
  <string>$DATA_DIR/worker.err.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/com.minifleet.worker" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/com.minifleet.worker"
launchctl kickstart -k "gui/$(id -u)/com.minifleet.worker"

echo ""
echo "Worker '$NODE_NAME' ($DEVICE_TYPE) registered with $COORDINATOR"
echo "Logs: $DATA_DIR/worker.log"
echo ""
echo "Optional: Remote Control on this Mac:"
echo "  MINIFLEET_NODE_NAME=$NODE_NAME ./scripts/setup-remote-control.sh"
