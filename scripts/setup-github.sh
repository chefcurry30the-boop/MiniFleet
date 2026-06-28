#!/usr/bin/env bash
set -euo pipefail

# Set up GitHub access for private repos on a Mac Mini worker.
#
# Option A — SSH deploy key (recommended):
#   ./scripts/setup-github.sh --ssh
#
# Option B — HTTPS token:
#   GITHUB_TOKEN=ghp_xxx ./scripts/setup-github.sh --token

DATA_DIR="${MINIFLEET_DATA:-$HOME/.minifleet}"
MODE="${1:-}"

echo "==> MiniFleet GitHub setup"
mkdir -p "$DATA_DIR"

setup_ssh() {
  KEY="$DATA_DIR/github_deploy_key"
  if [[ ! -f "$KEY" ]]; then
    echo "Generating deploy key at $KEY"
    ssh-keygen -t ed25519 -f "$KEY" -N "" -C "minifleet-$(hostname -s)"
  fi

  echo ""
  echo "Add this deploy key to your private GitHub repo(s):"
  echo "  GitHub → Repo → Settings → Deploy keys → Add deploy key"
  echo "  (read-only is fine for agents; write if agents should push)"
  echo ""
  cat "${KEY}.pub"
  echo ""

  SSH_CONFIG="$HOME/.ssh/config"
  MARKER="# minifleet-github"
  if ! grep -q "$MARKER" "$SSH_CONFIG" 2>/dev/null; then
    mkdir -p "$HOME/.ssh"
    chmod 700 "$HOME/.ssh"
    cat >> "$SSH_CONFIG" <<EOF

$MARKER
Host github.com
  IdentityFile $KEY
  IdentitiesOnly yes
EOF
    chmod 600 "$SSH_CONFIG"
    echo "Updated ~/.ssh/config to use deploy key for github.com"
  fi

  echo "Test: ssh -T git@github.com"
}

setup_token() {
  TOKEN="${GITHUB_TOKEN:-${MINIFLEET_GITHUB_TOKEN:-}}"
  if [[ -z "$TOKEN" ]]; then
    echo "Set GITHUB_TOKEN or MINIFLEET_GITHUB_TOKEN" >&2
    exit 1
  fi

  PLIST="$HOME/Library/LaunchAgents/com.minifleet.worker.plist"
  if [[ -f "$PLIST" ]]; then
    echo "Add to worker launchd plist EnvironmentVariables:"
    echo "  <key>GITHUB_TOKEN</key>"
    echo "  <string>ghp_...</string>"
    echo ""
    echo "Then: launchctl kickstart -k gui/\$(id -u)/com.minifleet.worker"
  fi

  echo "Token configured for HTTPS clone (do not commit this token)."
}

case "$MODE" in
  --ssh) setup_ssh ;;
  --token) setup_token ;;
  *)
    echo "Usage:"
    echo "  ./scripts/setup-github.sh --ssh     # deploy key (recommended)"
    echo "  GITHUB_TOKEN=ghp_xxx ./scripts/setup-github.sh --token"
    exit 1
    ;;
esac
