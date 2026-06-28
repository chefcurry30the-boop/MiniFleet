# Mac Mini fleet setup

Guide for running MiniFleet across **multiple always-on Mac Minis** — one head node plus N workers.

```
                    ┌─────────────────────┐
                    │  mac-mini-head      │
                    │  Coordinator :8787  │
                    │  + optional worker  │
                    └──────────┬──────────┘
                               │
         ┌─────────────────────┼─────────────────────┐
         ▼                     ▼                     ▼
   mac-mini-1            mac-mini-2            mac-mini-3
   (worker)              (worker)              (worker)
```

Your laptop (or any Mac) is only the **control plane** — assign jobs via CLI or dashboard, then disconnect.

---

## Recommended hardware layout

| Machine | Suggested role | `MINIFLEET_NODE_NAME` |
|---------|----------------|------------------------|
| Mac Mini M2/M4 (always on) | Coordinator + worker | `mac-mini-head` |
| Mac Mini #2 | Worker | `mac-mini-1` |
| Mac Mini #3 | Worker | `mac-mini-2` |
| Mac Mini #4 | Worker | `mac-mini-3` |
| MacBook | CLI only (no worker) | — |

Naming is arbitrary but **must be unique** across the fleet. Use stable names — jobs pinned with `--node mac-mini-2` expect that name forever.

---

## Prerequisites (every Mac Mini)

Run on each machine before setup:

```bash
# 1. macOS updated, logged into an admin user
# 2. Prevent sleep (head node especially)
#    System Settings → Energy → "Prevent automatic sleeping when display is off" (on power)
#    Or: sudo pmset -a sleep 0 disksleep 0

# 3. Python 3.11+
python3 --version

# 4. Claude Code installed and authenticated
claude --version
claude   # /login with Pro, Max, Team, or Enterprise

# 5. Same local network — note hostname
hostname -s
# e.g. mac-mini-lab-1 → reachable as mac-mini-lab-1.local
```

---

## Phase 1 — Head Mac Mini (coordinator)

Pick **one** Mac Mini that stays on 24/7. This runs the API, SQLite database, and dashboard.

```bash
git clone https://github.com/chefcurry30the-boop/MiniFleet.git ~/MiniFleet
cd ~/MiniFleet
pip3 install -e .
./scripts/setup-coordinator.sh
```

Note the printed URL:

```
Dashboard: http://YOUR-HEAD-HOSTNAME.local:8787
```

**Also run a worker on the head** (so it can take jobs too):

```bash
MINIFLEET_NODE_NAME=mac-mini-head \
MINIFLEET_COORDINATOR=http://127.0.0.1:8787 \
./scripts/setup-worker.sh
```

### Head Mac checklist

- [ ] `curl http://127.0.0.1:8787/api/health` returns OK
- [ ] Dashboard loads in browser
- [ ] `mac-mini-head` shows online in dashboard
- [ ] Coordinator survives reboot (`launchctl list | grep minifleet`)

---

## Phase 2 — Worker Mac Minis

Repeat on **each additional** Mac Mini.

### 2a. Clone and detect

```bash
git clone https://github.com/chefcurry30the-boop/MiniFleet.git ~/MiniFleet
cd ~/MiniFleet
pip3 install -e .
./scripts/detect-device.sh
```

### 2b. Install worker pointing at head

Replace `YOUR-HEAD-HOSTNAME` with the coordinator Mac's `hostname -s` output:

```bash
HEAD=http://YOUR-HEAD-HOSTNAME.local:8787

# Mac Mini #2
MINIFLEET_NODE_NAME=mac-mini-1 \
MINIFLEET_COORDINATOR=$HEAD \
./scripts/setup-worker.sh

# Mac Mini #3
MINIFLEET_NODE_NAME=mac-mini-2 \
MINIFLEET_COORDINATOR=$HEAD \
./scripts/setup-worker.sh

# Mac Mini #4
MINIFLEET_NODE_NAME=mac-mini-3 \
MINIFLEET_COORDINATOR=$HEAD \
./scripts/setup-worker.sh
```

### 2c. Verify from head Mac dashboard

Open `http://YOUR-HEAD-HOSTNAME.local:8787`. Each worker should appear as a card:

```
mac-mini-1  ● online  mac-mini
mac-mini-2  ● online  mac-mini
mac-mini-3  ● online  mac-mini
```

Or from any machine with the CLI:

```bash
export MINIFLEET_COORDINATOR=http://YOUR-HEAD-HOSTNAME.local:8787
minifleet nodes
```

---

## Phase 3 — GitHub (private repos)

Every Mac Mini that runs repo jobs needs GitHub access.

### Option A — SSH deploy key per repo (recommended)

On **each** Mac Mini:

```bash
cd ~/MiniFleet
./scripts/setup-github.sh --ssh
```

Add each machine's public key to GitHub → **repo → Settings → Deploy keys** (read-only is enough for pull).

### Option B — HTTPS token

```bash
GITHUB_TOKEN=ghp_xxx ./scripts/setup-github.sh --token
```

### Register repos on coordinator (once)

From laptop or head Mac:

```bash
export MINIFLEET_COORDINATOR=http://YOUR-HEAD-HOSTNAME.local:8787

minifleet repo add backend git@github.com:your-org/backend.git --branch main
minifleet repo add frontend git@github.com:your-org/frontend.git --branch main
minifleet repo list
```

Workers clone to `~/.minifleet/repos/<alias>` and `git pull` before each job.

---

## Phase 4 — Control from your laptop

One-time setup on MacBook:

```bash
git clone https://github.com/chefcurry30the-boop/MiniFleet.git ~/MiniFleet
cd ~/MiniFleet
pip3 install -e .

echo 'export MINIFLEET_COORDINATOR=http://YOUR-HEAD-HOSTNAME.local:8787' >> ~/.zshrc
source ~/.zshrc
```

### Assign to a specific mini

```bash
minifleet assign "Fix auth middleware and run tests" \
  --node mac-mini-2 \
  --repo backend \
  --title "Auth refactor"
```

### Let any mini pick it up

Omit `--node` — first available worker claims the job:

```bash
minifleet assign "Update dependencies and fix breaking changes" \
  --repo backend \
  --title "Dep bump"
```

### Pin heavy work to faster minis

```bash
# Studio-class or M4 Pro mini for big refactors
minifleet assign "Migrate database schema" --node mac-mini-3 --repo backend

# Lighter tasks on older minis
minifleet assign "Fix typo in README" --node mac-mini-1 --repo frontend --no-loop
```

---

## Phase 5 — Remote Control (optional)

Run a standing Remote Control hub on each mini so you can steer from [claude.ai/code](https://claude.ai/code) on your phone:

```bash
# On each Mac Mini:
MINIFLEET_NODE_NAME=mac-mini-1 ./scripts/setup-remote-control.sh
MINIFLEET_NODE_NAME=mac-mini-2 ./scripts/setup-remote-control.sh
```

Or per-job from laptop:

```bash
minifleet assign "Debug flaky test" --node mac-mini-1 --repo backend --remote
```

Requires Claude Code v2.1.51+.

---

## Concurrency and capacity

By default each mini runs **unlimited** parallel agents (`MINIFLEET_MAX_CONCURRENT=0`).

To cap a machine (e.g. 8 GB RAM mini):

```bash
MINIFLEET_NODE_NAME=mac-mini-1 \
MINIFLEET_COORDINATOR=$HEAD \
MINIFLEET_MAX_CONCURRENT=2 \
./scripts/setup-worker.sh
```

Rule of thumb:

| RAM | Suggested `MAX_CONCURRENT` |
|-----|---------------------------|
| 8 GB | 1–2 |
| 16 GB | 3–4 |
| 24 GB+ | 0 (unlimited) or 6–8 |

---

## Network and security

### Same LAN required

All minis must resolve `YOUR-HEAD-HOSTNAME.local` via Bonjour/mDNS. Wired Ethernet to the same switch is most reliable.

### Firewall on head Mac

Allow inbound TCP **8787** on the coordinator:

- System Settings → Network → Firewall → Options → allow `python3` or disable for local testing

### SSH access (optional)

For remote administration:

```bash
# On each mini — enable Remote Login in System Settings → General → Sharing
ssh user@mac-mini-1.local
tail -f ~/.minifleet/worker.log
```

### Unattended permission mode

Mac Minis run headless. Set permissive Claude mode so jobs don't block on prompts:

```bash
MINIFLEET_PERMISSION_MODE=auto ./scripts/setup-worker.sh
# or for trusted homelab:
MINIFLEET_PERMISSION_MODE=bypassPermissions ./scripts/setup-worker.sh
```

---

## Monitoring the fleet

| What | Where |
|------|-------|
| Live dashboard | `http://YOUR-HEAD-HOSTNAME.local:8787` |
| CLI status | `minifleet status` |
| Coordinator log | `~/.minifleet/coordinator.log` (head Mac) |
| Worker log | `~/.minifleet/worker.log` (each mini) |
| Agent output | `~/.minifleet/logs/<agent-id>.log` |

### After a reboot

launchd services auto-start. Verify:

```bash
launchctl list | grep minifleet
curl http://YOUR-HEAD-HOSTNAME.local:8787/api/health
minifleet nodes
```

---

## Adding a new Mac Mini later

1. `git clone` + `pip3 install -e .` on the new machine
2. Pick a new unique name: `mac-mini-4`
3. `MINIFLEET_NODE_NAME=mac-mini-4 MINIFLEET_COORDINATOR=http://HEAD.local:8787 ./scripts/setup-worker.sh`
4. Run `./scripts/setup-github.sh` on the new mini
5. Confirm it appears on dashboard

No coordinator restart needed.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Worker offline | `launchctl kickstart -k gui/$(id -u)/com.minifleet.worker` |
| Repo pull fails | Re-run `setup-github.sh`; check deploy key on GitHub |
| Job never starts | `minifleet nodes` — target node must be online |
| Coordinator down after reboot | `launchctl kickstart -k gui/$(id -u)/com.minifleet.coordinator` |
| `.local` hostname not resolving | Use IP instead: `MINIFLEET_COORDINATOR=http://192.168.1.50:8787` |

---

## Related docs

- **[SETUP-TWO-MACS.md](SETUP-TWO-MACS.md)** — simpler two-Mac walkthrough (Mac Mini + MacBook)
- **[WORKFLOW.md](WORKFLOW.md)** — job lifecycle, loop architecture, guardrails
