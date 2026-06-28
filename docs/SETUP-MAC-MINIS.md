# Mac Mini fleet setup (5 minis + MacBook)

Guide for **five always-on Mac Minis** running agents around the clock, with your **MacBook** as the control surface — plug in, assign work, see everything on the dashboard, unplug and go.

```
                         ┌─────────────────────┐
                         │  Your MacBook       │
                         │  CLI + dashboard    │
                         │  (shows when online)│
                         └──────────┬──────────┘
                                    │ assign / view
                                    ▼
                    ┌───────────────────────────────┐
                    │  mac-mini-1 (head)            │
                    │  Coordinator :8787 + worker   │
                    └───────────────┬───────────────┘
                                    │
    ┌───────────┬───────────┬───────┴───────┬───────────┐
    ▼           ▼           ▼               ▼           ▼
mac-mini-2  mac-mini-3  mac-mini-4    mac-mini-5   (all workers)
```

The five Mac Minis do the work. Your MacBook is how you talk to them — it appears on the dashboard when you connect (and optionally joins as a worker while docked).

---

## Hardware layout

| Machine | Role | `MINIFLEET_NODE_NAME` |
|---------|------|------------------------|
| Mac Mini #1 | Coordinator + worker (head) | `mac-mini-1` |
| Mac Mini #2 | Worker | `mac-mini-2` |
| Mac Mini #3 | Worker | `mac-mini-3` |
| Mac Mini #4 | Worker | `mac-mini-4` |
| Mac Mini #5 | Worker | `mac-mini-5` |
| Your MacBook | Control surface (CLI + browser) | `macbook` (optional worker) |

Names must be **unique**. Pick them once and keep them — `--node mac-mini-3` always means that machine.

---

## What you see on the dashboard

When all five minis are running and you open the dashboard from your MacBook:

```
Fleet: 12 running · 47 done · 2 queued

mac-mini-1  ● online  mac-mini   3 running · 12 done
mac-mini-2  ● online  mac-mini   2 running ·  8 done
mac-mini-3  ● online  mac-mini   4 running · 11 done
mac-mini-4  ● online  mac-mini   1 running ·  9 done
mac-mini-5  ● online  mac-mini   2 running ·  7 done
macbook     ● online  macbook    0 running ·  0 done   ← you, when connected
```

Unplug and leave — the five minis keep going. Your MacBook card goes offline until you're back on the network.

---

## Prerequisites (every Mac Mini)

Run on each of the five minis before setup:

```bash
# Prevent sleep — these run 24/7
sudo pmset -a sleep 0 disksleep 0

# Python + Claude Code
python3 --version   # 3.11+
claude --version
claude              # /login once

# Note hostname (for SSH and debugging)
hostname -s
# e.g. mac-mini-lab-3 → mac-mini-lab-3.local
```

Wire all five to the **same switch** or Wi‑Fi if possible. Ethernet is more reliable than Wi‑Fi for a headless fleet.

---

## Phase 1 — Head Mac Mini (`mac-mini-1`)

Pick one Mac Mini as coordinator. It also runs agents like the others.

```bash
git clone https://github.com/chefcurry30the-boop/MiniFleet.git ~/MiniFleet
cd ~/MiniFleet
pip3 install -e .
./scripts/setup-coordinator.sh
```

Save the printed dashboard URL:

```
http://YOUR-HEAD-HOSTNAME.local:8787
```

Install worker on the same machine:

```bash
MINIFLEET_NODE_NAME=mac-mini-1 \
MINIFLEET_COORDINATOR=http://127.0.0.1:8787 \
MINIFLEET_PERMISSION_MODE=auto \
./scripts/setup-worker.sh
```

**Head checklist:**

- [ ] `curl http://127.0.0.1:8787/api/health` → OK
- [ ] Dashboard loads
- [ ] `mac-mini-1` shows online
- [ ] Survives reboot (`launchctl list | grep minifleet`)

---

## Phase 2 — Four more Mac Minis (`mac-mini-2` … `mac-mini-5`)

Repeat on **each** remaining Mac Mini. SSH in or use Screen Sharing.

```bash
git clone https://github.com/chefcurry30the-boop/MiniFleet.git ~/MiniFleet
cd ~/MiniFleet
pip3 install -e .
./scripts/detect-device.sh
```

Set the head hostname once (from `hostname -s` on mac-mini-1):

```bash
HEAD=http://YOUR-HEAD-HOSTNAME.local:8787
```

Install each worker:

```bash
# Mac Mini #2
MINIFLEET_NODE_NAME=mac-mini-2 MINIFLEET_COORDINATOR=$HEAD MINIFLEET_PERMISSION_MODE=auto ./scripts/setup-worker.sh

# Mac Mini #3
MINIFLEET_NODE_NAME=mac-mini-3 MINIFLEET_COORDINATOR=$HEAD MINIFLEET_PERMISSION_MODE=auto ./scripts/setup-worker.sh

# Mac Mini #4
MINIFLEET_NODE_NAME=mac-mini-4 MINIFLEET_COORDINATOR=$HEAD MINIFLEET_PERMISSION_MODE=auto ./scripts/setup-worker.sh

# Mac Mini #5
MINIFLEET_NODE_NAME=mac-mini-5 MINIFLEET_COORDINATOR=$HEAD MINIFLEET_PERMISSION_MODE=auto ./scripts/setup-worker.sh
```

Verify all five on the dashboard — open `http://YOUR-HEAD-HOSTNAME.local:8787` from any machine on the LAN.

---

## Phase 3 — GitHub on all five minis

Every mini that pulls private repos needs auth:

```bash
cd ~/MiniFleet
./scripts/setup-github.sh --ssh
# Add deploy key to each repo on GitHub
```

Register repos once (from MacBook or head mini):

```bash
export MINIFLEET_COORDINATOR=http://YOUR-HEAD-HOSTNAME.local:8787

minifleet repo add backend git@github.com:your-org/backend.git --branch main
minifleet repo add frontend git@github.com:your-org/frontend.git --branch main
minifleet repo list
```

---

## Phase 4 — Connect your MacBook

This is your daily driver. No coordinator needed — just point at the fleet.

### 4a. One-time setup

```bash
git clone https://github.com/chefcurry30the-boop/MiniFleet.git ~/MiniFleet
cd ~/MiniFleet
pip3 install -e .

echo 'export MINIFLEET_COORDINATOR=http://YOUR-HEAD-HOSTNAME.local:8787' >> ~/.zshrc
source ~/.zshrc
```

### 4b. Open the dashboard

```bash
minifleet dashboard
# or: open http://YOUR-HEAD-HOSTNAME.local:8787
```

You'll see all five minis. Assign from the browser sidebar or CLI.

### 4c. (Optional) Show your MacBook on the dashboard

If you want your laptop to appear as a node (and take jobs while docked):

```bash
MINIFLEET_NODE_NAME=macbook \
MINIFLEET_COORDINATOR=http://YOUR-HEAD-HOSTNAME.local:8787 \
./scripts/setup-worker.sh
```

When you leave, the `macbook` card goes offline. The five minis are unaffected.

To stop the MacBook worker without uninstalling:

```bash
launchctl bootout "gui/$(id -u)/com.minifleet.worker"
```

---

## Phase 5 — Assign work across the fleet

From your MacBook:

```bash
# Pin to a specific mini
minifleet assign "Fix auth middleware and run tests" \
  --node mac-mini-3 \
  --repo backend \
  --title "Auth refactor"

# Spread load — any mini claims it
minifleet assign "Update deps and fix breakages" \
  --repo backend \
  --title "Dep bump"

# Heavy job on one machine, light on another
minifleet assign "Migrate database schema" --node mac-mini-5 --repo backend
minifleet assign "Fix README typo" --node mac-mini-2 --repo frontend --no-loop
```

**Unplug your MacBook.** All five minis keep running. When you're back:

```bash
minifleet status
minifleet dashboard
```

---

## Phase 6 — Remote Control (optional)

Standing hub on each mini — steer from your phone at [claude.ai/code](https://claude.ai/code):

```bash
# On each Mac Mini:
MINIFLEET_NODE_NAME=mac-mini-2 ./scripts/setup-remote-control.sh
MINIFLEET_NODE_NAME=mac-mini-3 ./scripts/setup-remote-control.sh
# ... etc
```

Or per-job from MacBook:

```bash
minifleet assign "Debug flaky test" --node mac-mini-4 --repo backend --remote
```

---

## Concurrency per mini

Default is unlimited agents (`MINIFLEET_MAX_CONCURRENT=0`). Cap weaker minis:

| RAM | Suggested cap |
|-----|---------------|
| 8 GB | `MINIFLEET_MAX_CONCURRENT=2` |
| 16 GB | `MINIFLEET_MAX_CONCURRENT=4` |
| 24 GB+ | `0` (unlimited) |

```bash
MINIFLEET_NODE_NAME=mac-mini-2 MINIFLEET_MAX_CONCURRENT=2 MINIFLEET_COORDINATOR=$HEAD ./scripts/setup-worker.sh
```

---

## Monitoring

| What | Where |
|------|-------|
| Live dashboard | `http://YOUR-HEAD-HOSTNAME.local:8787` (from MacBook) |
| Fleet status | `minifleet status` |
| Which nodes are up | `minifleet nodes` |
| Coordinator log | `~/.minifleet/coordinator.log` on mac-mini-1 |
| Worker logs | `~/.minifleet/worker.log` on each mini |
| Agent output | `~/.minifleet/logs/<agent-id>.log` |

After a power outage, launchd auto-restarts everything. Verify:

```bash
minifleet nodes   # should list mac-mini-1 … mac-mini-5
```

---

## Adding a 6th Mac Mini later

```bash
MINIFLEET_NODE_NAME=mac-mini-6 MINIFLEET_COORDINATOR=http://HEAD.local:8787 ./scripts/setup-worker.sh
```

No coordinator restart needed.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| MacBook can't reach dashboard | Same Wi‑Fi/LAN as minis; `ping YOUR-HEAD-HOSTNAME.local` |
| One mini offline | `ssh mini@mac-mini-3.local` → `launchctl kickstart -k gui/$(id -u)/com.minifleet.worker` |
| Job stuck queued | `minifleet nodes` — target `--node` must be online |
| Repo pull fails | Re-run `setup-github.sh` on that mini |
| MacBook not showing | Only appears if worker is installed (`setup-worker.sh`) |

---

## Related docs

- **[SETUP-TWO-MACS.md](SETUP-TWO-MACS.md)** — start with two MacBooks before buying minis
- **[WORKFLOW.md](WORKFLOW.md)** — job lifecycle, loop architecture, guardrails
