# Connect two MacBooks to MiniFleet

Step-by-step guide for **two MacBooks** on the same network — one runs the coordinator, both can run agents.

Typical layout:

```
MacBook A (home / docked)       MacBook B (portable)
├─ Coordinator :8787            ├─ Worker daemon
├─ Worker daemon                └─ Claude Code agents
├─ Claude Code agents
└─ Dashboard always reachable
```

Use this when you don't have Mac Minis yet — two laptops is enough to test the full flow before scaling up.

Both MacBooks must be on the **same network** (same Wi‑Fi). Bonjour `.local` hostnames must resolve between them.

---

## What you need

| Item | MacBook A (head) | MacBook B |
|------|------------------|-----------|
| macOS | Any recent version | Any recent version |
| Python | 3.11+ | 3.11+ |
| Claude Code | Installed + `/login` | Same |
| Network | Same Wi‑Fi as MacBook B | Same Wi‑Fi as MacBook A |
| Role | Coordinator + worker | Worker |
| Power | Plugged in, lid open or "don't sleep on power" | Plugged in while running jobs |

Pick **MacBook A** as the head — usually the one that stays home, on a desk, or plugged in most often.

---

## Step 1 — Note hostnames

On **each** MacBook, run:

```bash
hostname -s
```

Example:

| MacBook | `hostname -s` | URL |
|---------|---------------|-----|
| MacBook A (head) | `macbook-head` | `http://macbook-head.local:8787` |
| MacBook B | `macbook-b` | (worker — points at MacBook A) |

Write down MacBook A's hostname as `YOUR-HEAD-MAC`.

**Test from MacBook B:**

```bash
ping -c 2 macbook-head.local
curl -s http://macbook-head.local:8787/api/health || echo "not up yet"
```

If `ping` fails, fix Wi‑Fi / firewall before continuing.

---

## Step 2 — Set up MacBook A (coordinator)

On the MacBook that will be the head node.

### 2a. Prerequisites

```bash
claude --version
claude   # run once, type /login if needed
python3 --version
```

### 2b. Install coordinator

```bash
git clone https://github.com/chefcurry30the-boop/MiniFleet.git ~/MiniFleet
cd ~/MiniFleet
pip3 install -e .
./scripts/setup-coordinator.sh
```

Verify:

```bash
curl -s http://127.0.0.1:8787/api/health
open "http://$(hostname -s).local:8787"
```

### 2c. Worker on MacBook A

```bash
cd ~/MiniFleet
./scripts/detect-device.sh

MINIFLEET_NODE_NAME=macbook-a \
MINIFLEET_COORDINATOR=http://127.0.0.1:8787 \
./scripts/setup-worker.sh
```

### 2d. Prevent sleep (important for a laptop head node)

System Settings → Battery → **Prevent automatic sleeping when display is off** (when on power adapter).

Or from terminal:

```bash
sudo pmset -c sleep 0 disksleep 0
```

---

## Step 3 — Set up MacBook B (worker)

On the second MacBook.

```bash
git clone https://github.com/chefcurry30the-boop/MiniFleet.git ~/MiniFleet
cd ~/MiniFleet
pip3 install -e .
./scripts/detect-device.sh

# Replace with MacBook A's hostname
MINIFLEET_NODE_NAME=macbook-b \
MINIFLEET_COORDINATOR=http://macbook-head.local:8787 \
./scripts/setup-worker.sh
```

Verify from MacBook B:

```bash
export MINIFLEET_COORDINATOR=http://macbook-head.local:8787
minifleet nodes
```

You should see both `macbook-a` and `macbook-b` online.

---

## Step 4 — CLI on both MacBooks (optional but handy)

On **either** MacBook, set the coordinator URL permanently:

```bash
echo 'export MINIFLEET_COORDINATOR=http://macbook-head.local:8787' >> ~/.zshrc
source ~/.zshrc
```

Now both laptops can assign jobs and open the dashboard:

```bash
minifleet dashboard
minifleet nodes
```

---

## Step 5 — Register a repo (optional)

From either MacBook (with `MINIFLEET_COORDINATOR` set):

```bash
minifleet repo add my-app git@github.com:your-org/your-repo.git --branch main
```

Run GitHub auth on **both** MacBooks if they'll pull private repos:

```bash
cd ~/MiniFleet
./scripts/setup-github.sh --ssh
```

---

## Step 6 — Assign your first job

From MacBook B while both are on the same Wi‑Fi:

```bash
minifleet assign "Summarize this repo in 3 bullet points" \
  --node macbook-a \
  --repo my-app \
  --title "Smoke test"
```

Watch on the dashboard (`minifleet dashboard`) — you'll see the job on `macbook-a`.

Try pinning to the other laptop:

```bash
minifleet assign "Run the test suite and report failures" \
  --node macbook-b \
  --repo my-app \
  --title "Test run"
```

**Close MacBook B's lid and walk away** — as long as MacBook A is awake and on power, the coordinator and any jobs on MacBook A keep running. Jobs on MacBook B stop if that machine sleeps.

---

## Step 7 — Daily workflow

| Action | Command |
|--------|---------|
| Queue work on MacBook A | `minifleet assign "..." --node macbook-a --repo my-app` |
| Queue work on MacBook B | `minifleet assign "..." --node macbook-b --repo my-app` |
| Let either pick it up | `minifleet assign "..." --repo my-app` (omit `--node`) |
| Check fleet | `minifleet status` or dashboard |
| Steer from phone | `minifleet assign "..." --remote` |

---

## Troubleshooting

### MacBook B can't reach coordinator

- MacBook A must be awake, on Wi‑Fi, and not sleeping
- `ping YOUR-HEAD-MAC.local` from MacBook B
- Check firewall on MacBook A (allow port 8787)

### Worker offline after closing lid

Laptops sleep when closed unless configured otherwise. For reliable workers:

- Keep plugged in with "prevent sleep on power"
- Or use MacBook A as the only worker and MacBook B purely for CLI

### Coordinator dies when MacBook A sleeps

The head node must stay awake. A Mac Mini fleet is better for 24/7 — see **[SETUP-MAC-MINIS.md](SETUP-MAC-MINIS.md)**.

---

## Uninstall

On each MacBook:

```bash
launchctl bootout "gui/$(id -u)/com.minifleet.coordinator" 2>/dev/null || true
launchctl bootout "gui/$(id -u)/com.minifleet.worker" 2>/dev/null || true
rm -f ~/Library/LaunchAgents/com.minifleet.coordinator.plist
rm -f ~/Library/LaunchAgents/com.minifleet.worker.plist
```

---

## Next steps

- **[SETUP-MAC-MINIS.md](SETUP-MAC-MINIS.md)** — graduate to 5 always-on Mac Minis + your MacBook as control surface
- **[WORKFLOW.md](WORKFLOW.md)** — how jobs run (loops, guardrails, multi-agent)
