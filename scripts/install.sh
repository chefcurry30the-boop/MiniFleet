#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# MiniFleet — one-command installer for any Mac (Mini, MacBook, Studio, iMac)
# ============================================================================
#
# Installs MiniFleet from a fresh checkout into an isolated venv and registers
# the right LaunchAgent for the machine's role (coordinator or worker), so the
# same command works across an entire fleet of Mac minis.
#
# --- Coordinator (run ONCE on the always-on head Mac) -----------------------
#
#   curl -fsSL https://raw.githubusercontent.com/chefcurry30the-boop/MiniFleet/main/scripts/install.sh \
#     | bash -s -- --role coordinator
#
# --- Worker (run on EVERY other Mac) ---------------------------------------
#
#   curl -fsSL https://raw.githubusercontent.com/chefcurry30the-boop/MiniFleet/main/scripts/install.sh \
#     | bash -s -- --role worker --name mac-mini-2 --coordinator http://head-mini.local:8787
#
# Local clone also works:  bash scripts/install.sh --role worker --name ...
#
# Flags:
#   --role coordinator|worker   (required)
#   --name <node>                worker node name (required for --role worker)
#   --coordinator <url>          coordinator URL (worker: required; coordinator: bind URL, default http://0.0.0.0:8787)
#   --branch <git-branch>        default main
#   --repo <git-url>             default chefcurry30the-boop/MiniFleet
#   --dir <path>                 install dir, default ~/MiniFleet
#   --data-dir <path>            logs + SQLite, default ~/.minifleet
#   --max-concurrent <n>         cap agents per worker (0 = unlimited)
#   --mock                       mock agents (no claude needed; testing)
#   --dry-run                    print the plan and exit
# ============================================================================

ROLE=""
NODE_NAME=""
COORDINATOR=""
BRANCH="main"
REPO="https://github.com/chefcurry30the-boop/MiniFleet.git"
DIR="$HOME/MiniFleet"
DATA_DIR="${MINIFLEET_DATA:-$HOME/.minifleet}"
MAX_CONCURRENT="${MINIFLEET_MAX_CONCURRENT:-0}"
MOCK=0
DRY_RUN=0

usage() {
  sed -n '3,/^# =\{70,\}$/p' "$0" | sed 's/^# \{0,1\}//' >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --role) ROLE="${2:-}"; shift 2;;
    --name) NODE_NAME="${2:-}"; shift 2;;
    --coordinator) COORDINATOR="${2:-}"; shift 2;;
    --branch) BRANCH="${2:-main}"; shift 2;;
    --repo) REPO="${2:-}"; shift 2;;
    --dir) DIR="${2:-}"; shift 2;;
    --data-dir) DATA_DIR="${2:-}"; shift 2;;
    --max-concurrent) MAX_CONCURRENT="${2:-0}"; shift 2;;
    --mock) MOCK=1; shift;;
    --dry-run) DRY_RUN=1; shift;;
    -h|--help) usage;;
    *) echo "install.sh: unknown argument '$1'" >&2; echo >&2; usage;;
  esac
done

[[ -n "$ROLE" ]] || { echo "install.sh: --role {coordinator|worker} is required" >&2; exit 2; }
[[ "$ROLE" == "coordinator" || "$ROLE" == "worker" ]] || {
  echo "install.sh: --role must be 'coordinator' or 'worker' (got '$ROLE')" >&2; exit 2; }

# ---- pick a Python 3.11+ -----------------------------------------------------
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [[ -z "$PY" ]]; then
  cat >&2 <<EOF
install.sh: Python 3.11+ not found.
Install it first, e.g.:
  brew install python@3.12
or from https://www.python.org/downloads/
EOF
  exit 1
fi
echo "==> Python: $($PY -V) ($PY)"

if [[ $DRY_RUN -eq 1 ]]; then
  echo "[dry-run] clone/update  $REPO (branch $BRANCH) -> $DIR"
  echo "[dry-run] venv + install  $DIR/.venv  (pip install -e .)"
  echo "[dry-run] role            $ROLE"
  echo "[dry-run] data dir         $DATA_DIR"
  if [[ "$ROLE" == "coordinator" ]]; then
    echo "[dry-run] bind URL         ${COORDINATOR:-http://0.0.0.0:8787}"
  else
    echo "[dry-run] node name        ${NODE_NAME:-(missing --name)}"
    echo "[dry-run] coordinator      ${COORDINATOR:-(missing --coordinator)}"
    [[ $MOCK -eq 1 ]] && echo "[dry-run] mock agents      on"
  fi
  exit 0
fi

# ---- clone or update --------------------------------------------------------
if [[ -d "$DIR/.git" ]]; then
  echo "==> Updating existing checkout at $DIR"
  git -C "$DIR" fetch origin "$BRANCH" --quiet
  git -C "$DIR" reset --hard "origin/$BRANCH" --quiet
  git -C "$DIR" clean -fdq
else
  echo "==> Cloning $REPO (branch $BRANCH) -> $DIR"
  git clone --branch "$BRANCH" "$REPO" "$DIR" --quiet
fi

# ---- venv + editable install (robust across minis regardless of system pip) -
VENV="$DIR/.venv"
if [[ ! -x "$VENV/bin/python" ]]; then
  echo "==> Creating venv at $VENV"
  "$PY" -m venv "$VENV"
fi
VPY="$VENV/bin/python"
echo "==> Installing MiniFleet into venv"
"$VPY" -m pip install --upgrade pip --quiet
"$VPY" -m pip install -e "$DIR" --quiet

# Hand the venv python + data dir to the role setup scripts so the LaunchAgent
# runs from the venv (no dependence on the system pip policy).
export MINIFLEET_PYTHON="$VPY"
export MINIFLEET_DATA="$DATA_DIR"
export MINIFLEET_SKIP_INSTALL=1

if [[ "$ROLE" == "coordinator" ]]; then
  [[ -z "$COORDINATOR" ]] && COORDINATOR="http://0.0.0.0:8787"
  export MINIFLEET_COORDINATOR="$COORDINATOR"
  echo "==> Installing coordinator (bind $COORDINATOR)"
  bash "$DIR/scripts/setup-coordinator.sh"
  PORT="$(printf '%s' "$COORDINATOR" | sed -E 's|.*:([0-9]+).*|\1|')"
  PORT="${PORT:-8787}"
  HOST="$(hostname -s)"
  echo ""
  echo "✓ Coordinator running. Dashboard: http://$HOST.local:$PORT"
  echo "  On every other Mac, run the worker one-liner with:"
  echo "    --role worker --name <mini-name> --coordinator http://$HOST.local:$PORT"
else
  if [[ -z "$NODE_NAME" ]]; then
    echo "install.sh: --name <node> is required for --role worker" >&2
    SUGGEST="$("$VPY" -c 'import socket;print(socket.gethostname().split(".")[0])' 2>/dev/null || echo "")"
    [[ -n "$SUGGEST" ]] && echo "  (suggested from hostname: --name $SUGGEST)" >&2
    exit 2
  fi
  if [[ -z "$COORDINATOR" ]]; then
    echo "install.sh: --coordinator <url> is required for --role worker (e.g. http://head-mini.local:8787)" >&2
    exit 2
  fi
  export MINIFLEET_NODE_NAME="$NODE_NAME"
  export MINIFLEET_COORDINATOR="$COORDINATOR"
  export MINIFLEET_MAX_CONCURRENT="$MAX_CONCURRENT"
  export MINIFLEET_DEVICE_TYPE="$("$VPY" -c 'from minifleet.device import detect_device_type; print(detect_device_type())' 2>/dev/null || echo "")"
  [[ $MOCK -eq 1 ]] && export MINIFLEET_MOCK=1
  echo "==> Installing worker '$NODE_NAME' -> $COORDINATOR"
  bash "$DIR/scripts/setup-worker.sh"
  echo ""
  echo "✓ Worker '$NODE_NAME' registered with $COORDINATOR"
  echo "  Logs: $DATA_DIR/worker.log"
fi