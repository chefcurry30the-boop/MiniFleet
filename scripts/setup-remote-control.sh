#!/usr/bin/env bash
set -euo pipefail

# Install Claude Remote Control server on a Mac Mini.
# Lets you steer sessions from claude.ai/code or the Claude mobile app.
#
# Usage:
#   MINIFLEET_NODE_NAME=mac-mini-1 ./scripts/setup-remote-control.sh

DATA_DIR="${MINIFLEET_DATA:-$HOME/.minifleet}"
NODE_NAME="${MINIFLEET_NODE_NAME:-$(hostname -s)}"
CAPACITY="${MINIFLEET_RC_CAPACITY:-4}"
SPAWN="${MINIFLEET_RC_SPAWN:-worktree}"

CLAUDE_BIN="${MINIFLEET_CLAUDE:-$(command -v claude || echo "$HOME/.local/bin/claude")}"

if [[ ! -x "$CLAUDE_BIN" && ! -f "$CLAUDE_BIN" ]]; then
  echo "Claude Code CLI not found. Install from https://code.claude.com" >&2
  exit 1
fi

mkdir -p "$DATA_DIR"

PLIST="$HOME/Library/LaunchAgents/com.minifleet.remote-control.plist"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.minifleet.remote-control</string>
  <key>ProgramArguments</key>
  <array>
    <string>$CLAUDE_BIN</string>
    <string>remote-control</string>
    <string>--spawn</string>
    <string>$SPAWN</string>
    <string>--capacity</string>
    <string>$CAPACITY</string>
    <string>--remote-control-session-name-prefix</string>
    <string>$NODE_NAME</string>
    <string>--name</string>
    <string>$NODE_NAME fleet</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:$HOME/.local/bin</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$DATA_DIR/remote-control.log</string>
  <key>StandardErrorPath</key>
  <string>$DATA_DIR/remote-control.err.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/com.minifleet.remote-control" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/com.minifleet.remote-control"
launchctl kickstart -k "gui/$(id -u)/com.minifleet.remote-control"

echo ""
echo "Remote Control server running on $NODE_NAME"
echo "Connect from phone/browser: https://claude.ai/code"
echo "Look for sessions prefixed: $NODE_NAME-*"
echo "Logs: $DATA_DIR/remote-control.log"
echo ""
echo "Requires Claude Pro/Max/Team with /login on this machine."
