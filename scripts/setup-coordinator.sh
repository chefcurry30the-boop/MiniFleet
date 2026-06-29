#!/usr/bin/env bash
set -euo pipefail

# Install MiniFleet coordinator on the head Mac (or your MacBook while testing).
# Usage: ./scripts/setup-coordinator.sh

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="${MINIFLEET_DATA:-$HOME/.minifleet}"

echo "==> Installing MiniFleet coordinator"
if [[ -z "${MINIFLEET_SKIP_INSTALL:-}" ]]; then
  python3 -m pip install -e "$ROOT" --quiet
fi

mkdir -p "$DATA_DIR"

COORDINATOR_URL="${MINIFLEET_COORDINATOR:-http://0.0.0.0:8787}"
HOST="$(echo "$COORDINATOR_URL" | sed -E 's|https?://([^:/]+).*|\1|')"
PORT="$(echo "$COORDINATOR_URL" | sed -E 's|.*:([0-9]+).*|\1|' || echo 8787)"
PORT="${PORT:-8787}"

PLIST="$HOME/Library/LaunchAgents/com.minifleet.coordinator.plist"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.minifleet.coordinator</string>
  <key>ProgramArguments</key>
  <array>
    <string>${MINIFLEET_PYTHON:-$(which python3)}</string>
    <string>-m</string>
    <string>minifleet.coordinator.main</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>MINIFLEET_DATA</key>
    <string>$DATA_DIR</string>
    <key>MINIFLEET_HOST</key>
    <string>0.0.0.0</string>
    <key>MINIFLEET_PORT</key>
    <string>$PORT</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$DATA_DIR/coordinator.log</string>
  <key>StandardErrorPath</key>
  <string>$DATA_DIR/coordinator.err.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/com.minifleet.coordinator" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/com.minifleet.coordinator"
launchctl kickstart -k "gui/$(id -u)/com.minifleet.coordinator"

echo ""
echo "Coordinator running at http://$(hostname -s).local:$PORT"
echo "Dashboard:       http://$(hostname -s).local:$PORT"
echo "Data dir:        $DATA_DIR"
echo ""
echo "From your laptop, set:"
echo "  export MINIFLEET_COORDINATOR=http://$(hostname -s).local:$PORT"
