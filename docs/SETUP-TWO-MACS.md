# Connect two Macs to MiniFleet

Step-by-step guide for the most common setup: **one always-on Mac** (coordinator + worker) and **one laptop** (control surface + optional worker).

Typical layout:

```
Mac Mini (head)          MacBook (laptop)
├─ Coordinator :8787     ├─ CLI: minifleet assign
├─ Worker daemon         ├─ Browser: dashboard
└─ Claude Code agents    └─ Optional worker while docked
```

Both Macs must be on the **same network** (same Wi‑Fi or Ethernet). Bonjour `.local` hostnames must resolve between them.

---

## What you need

| Item | Mac Mini (head) | MacBook (laptop) |
|------|-----------------|------------------|
| macOS | Any recent version | Any recent version |
| Python | 3.11+ (`python3 --version`) | 3.11+ |
| Claude Code | Installed + logged in (`claude` → `/login`) | Same |
| Network | Ethernet or Wi‑Fi, always on | Same Wi‑Fi as head Mac |
| Role | Coordinator + worker | CLI + dashboard (optional worker) |

---

## Step 1 — Note hostnames

On **each** Mac, run:

```bash
hostname -s
```

Example output:

| Mac | `hostname -s` | Dashboard / coordinator URL |
|-----|---------------|-----------------------------|
| Mac Mini | `buildwrights-mac-mini` | `http://buildwrights-mac-mini.local:8787` |
| MacBook | `buildwrights-macbook` | (client only — points at head Mac) |

Write down the **head Mac's** short hostname. You'll use it everywhere as `YOUR-HEAD-MAC`.

**Test connectivity** from the MacBook:

```bash
ping -c 2 buildwrights-mac-mini.local
curl -s http://buildwrights-mac-mini.local:8787/api/health || echo "not up yet"
```

If `ping` fails, fix networking first (same subnet, firewall, etc.) before continuing.

---

## Step 2 — Set up the head Mac (Mac Mini)

SSH into the Mac Mini or sit at it directly.

### 2a. Install prerequisites

```bash
# Claude Code
# https://code.claude.com — then:
claude --version
claude   # run once, type /login if needed

# Python 3.11+
python3 --version
```

### 2b. Clone MiniFleet and install coordinator

```bash
git clone https://github.com/chefcurry30the-boop/MiniFleet.git ~/MiniFleet
cd ~/MiniFleet
pip3 install -e .
./scripts/setup-coordinator.sh
```

The script installs a **launchd** service that starts on login and keeps running.

Verify:

```bash
curl -s http://127.0.0.1:8787/api/health
open "http://$(hostname -s).local:8787"
```

You should see the MiniFleet dashboard.

### 2c. Install worker on the same Mac (recommended)

The head Mac can also run agents:

```bash
cd ~/MiniFleet
./scripts/detect-device.sh

MINIFLEET_NODE_NAME=mac-mini-1 \
MINIFLEET_COORDINATOR=http://127.0.0.1:8787 \
./scripts/setup-worker.sh
```

Using `127.0.0.1` is fine on the coordinator machine itself.

Check logs:

```bash
tail -f ~/.minifleet/worker.log
```

### 2d. (Optional) GitHub auth on the head Mac

If jobs will touch private repos:

```bash
cd ~/MiniFleet
./scripts/setup-github.sh --ssh
# Add the printed deploy key to GitHub → repo → Settings → Deploy keys
```

---

## Step 3 — Set up your MacBook

### 3a. Clone and install CLI only

You don't need the coordinator on the laptop — just the CLI to assign jobs:

```bash
git clone https://github.com/chefcurry30the-boop/MiniFleet.git ~/MiniFleet
cd ~/MiniFleet
pip3 install -e .
```

### 3b. Point CLI at the head Mac

Replace `buildwrights-mac-mini` with your head Mac's hostname:

```bash
export MINIFLEET_COORDINATOR=http://buildwrights-mac-mini.local:8787
```

Add to `~/.zshrc` so it persists:

```bash
echo 'export MINIFLEET_COORDINATOR=http://buildwrights-mac-mini.local:8787' >> ~/.zshrc
source ~/.zshrc
```

### 3c. Verify connection

```bash
minifleet nodes
minifleet dashboard   # opens browser to head Mac dashboard
```

You should see `mac-mini-1` (or whatever you named the worker) as **online**.

### 3d. (Optional) MacBook as a worker while docked

If you want the laptop to also claim jobs:

```bash
cd ~/MiniFleet
./scripts/detect-device.sh

MINIFLEET_NODE_NAME=macbook-pro \
MINIFLEET_COORDINATOR=http://buildwrights-mac-mini.local:8787 \
./scripts/setup-worker.sh
```

When unplugged, you can leave it running if the MacBook is configured not to sleep on power. Otherwise stop the worker:

```bash
launchctl bootout "gui/$(id -u)/com.minifleet.worker"
```

---

## Step 4 — Register a repo (optional)

On the **head Mac** or from the **MacBook CLI** (with `MINIFLEET_COORDINATOR` set):

```bash
minifleet repo add my-app git@github.com:your-org/your-repo.git --branch main
minifleet repo list
```

Or use the dashboard sidebar → **Add repo**.

Every worker Mac that runs jobs against `my-app` needs GitHub auth (`./scripts/setup-github.sh`).

---

## Step 5 — Assign your first job

From the MacBook:

```bash
minifleet assign "List the top 3 files in this repo and summarize what the project does" \
  --node mac-mini-1 \
  --repo my-app \
  --title "Smoke test"
```

Watch progress:

```bash
minifleet status
```

Or open the dashboard on the head Mac — you'll see the job card with loop iteration progress.

**Unplug the laptop.** The Mac Mini keeps running the job. When you reconnect:

```bash
minifleet status
open http://buildwrights-mac-mini.local:8787
```

---

## Step 6 — Daily workflow

| Action | Command |
|--------|---------|
| Queue work | `minifleet assign "..." --node mac-mini-1 --repo my-app` |
| Check fleet | `minifleet status` or dashboard |
| See which Macs are up | `minifleet nodes` |
| Steer from phone | `minifleet assign "..." --remote` or Remote Control hub |
| View agent logs | `tail -f ~/.minifleet/logs/<agent-id>.log` (on the worker Mac) |

---

## Troubleshooting

### Worker shows offline on dashboard

On the worker Mac:

```bash
tail -50 ~/.minifleet/worker.log
launchctl kickstart -k "gui/$(id -u)/com.minifleet.worker"
```

Check `MINIFLEET_COORDINATOR` in the plist:

```bash
plutil -p ~/Library/LaunchAgents/com.minifleet.worker.plist | grep COORDINATOR
```

### Can't reach dashboard from MacBook

1. Confirm head Mac is awake and on the network
2. `ping YOUR-HEAD-MAC.local` from MacBook
3. On head Mac: System Settings → Network → Firewall — allow incoming on port 8787, or disable firewall temporarily to test
4. Coordinator logs: `tail -50 ~/.minifleet/coordinator.log`

### Claude Code not found on worker

```bash
which claude
# If missing, install from https://code.claude.com
# Then restart worker:
launchctl kickstart -k "gui/$(id -u)/com.minifleet.worker"
```

### Jobs stuck in queued

- Worker must be **online** (`minifleet nodes`)
- If you used `--node mac-mini-1`, that exact node must be registered
- Check worker isn't at concurrency cap (`MINIFLEET_MAX_CONCURRENT`)

---

## Uninstall / reset

```bash
launchctl bootout "gui/$(id -u)/com.minifleet.coordinator"
launchctl bootout "gui/$(id -u)/com.minifleet.worker"
rm ~/Library/LaunchAgents/com.minifleet.coordinator.plist
rm ~/Library/LaunchAgents/com.minifleet.worker.plist
# Data (optional): rm -rf ~/.minifleet
```

---

## Next steps

- **[SETUP-MAC-MINIS.md](SETUP-MAC-MINIS.md)** — add more Mac Minis to the fleet
- **[WORKFLOW.md](WORKFLOW.md)** — how jobs run (loop phases, guardrails, multi-agent)
