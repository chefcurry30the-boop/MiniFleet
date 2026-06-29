#!/usr/bin/env python3
"""CLI for assigning work and checking fleet status from your laptop."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import webbrowser

import httpx

TERMINAL_STATUSES = ("completed", "failed", "cancelled")
STATUS_ICON = {
    "running": "▶",
    "completed": "✓",
    "failed": "✗",
    "queued": "…",
    "cancelled": "—",
}


def coordinator_url() -> str:
    return os.environ.get("MINIFLEET_COORDINATOR", "http://127.0.0.1:8787").rstrip("/")


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def _get(url: str, **kwargs) -> httpx.Response:
    resp = httpx.get(url, timeout=10.0, **kwargs)
    resp.raise_for_status()
    return resp


def _post(url: str, **kwargs) -> httpx.Response:
    resp = httpx.post(url, timeout=10.0, **kwargs)
    resp.raise_for_status()
    return resp


def _resolve_node_id(url: str, name: str) -> str | None:
    """Translate a node name into its UUID via /api/nodes."""
    if not name:
        return None
    for node in _get(f"{url}/api/nodes").json():
        if node["name"] == name:
            return node["id"]
    return None


def _exit_code_for_status(status: str) -> int:
    return 0 if status == "completed" else 1


# --------------------------------------------------------------------------- #
# Prompt collection — positional args, --from-file, or piped stdin
# --------------------------------------------------------------------------- #
def _read_prompts(args: argparse.Namespace) -> list[str]:
    prompts = list(getattr(args, "prompts", None) or [])
    if prompts:
        return prompts

    from_file = getattr(args, "from_file", None)
    if from_file:
        if from_file == "-":
            text = sys.stdin.read()
        else:
            try:
                with open(from_file, encoding="utf-8") as f:
                    text = f.read()
            except OSError as exc:
                print(f"Could not read --from-file {from_file!r}: {exc}", file=sys.stderr)
                return None
        out = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            out.append(line)
        return out

    # No positional prompts and no --from-file: accept a piped prompt on stdin.
    if not sys.stdin.isatty():
        text = sys.stdin.read().strip()
        if text:
            return [text]
    return []


def build_assign_payload(args: argparse.Namespace, prompt: str) -> dict:
    loop_config = None
    if args.loop:
        loop_config = {
            "max_iterations": args.max_iterations,
            "max_duration_seconds": args.max_duration,
            "verify_command": args.verify,
            "playbook": args.playbook,
            "multi_agent": args.multi_agent,
            "cost_cap_usd": args.cost_cap,
            "completion_threshold": args.completion_threshold,
        }
        loop_config = {k: v for k, v in loop_config.items() if v is not None}

    payload = {
        "prompt": prompt,
        "node_name": args.node,
        "repo": args.repo,
        "repo_path": args.repo_path,
        "title": args.title,
        "remote": args.remote,
        "loop": args.loop,
        "loop_config": loop_config,
    }
    return {k: v for k, v in payload.items() if v is not None}


# --------------------------------------------------------------------------- #
# Log streaming / waiting
# --------------------------------------------------------------------------- #
def _follow_logs(url: str, agent_id: str, offset: int = 0, timeout: float | None = None) -> None:
    """Stream an agent's log until it finishes, or until `timeout` seconds elapse.

    With no timeout, uses the coordinator's SSE stream (unbounded — Ctrl-C to
    detach). With a timeout, falls back to bounded polling so a stuck job
    (e.g. worker offline) can't hang the CLI forever.
    """
    if timeout is None:
        with httpx.stream(
            "GET",
            f"{url}/api/agents/{agent_id}/logs/stream",
            params={"offset": offset},
            timeout=None,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                data = json.loads(line[6:])
                if data.get("done"):
                    return
                chunk = data.get("content", "")
                if chunk:
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
        return

    deadline = time.monotonic() + timeout
    pos = offset
    while True:
        data = _get(f"{url}/api/agents/{agent_id}/logs", params={"offset": pos}).json()
        chunk = data.get("content", "")
        if chunk:
            sys.stdout.write(chunk)
            sys.stdout.flush()
        pos = data.get("offset", pos)
        agent = _get(f"{url}/api/agents/{agent_id}").json()
        if agent.get("status") in TERMINAL_STATUSES:
            return
        if time.monotonic() >= deadline:
            return
        time.sleep(1.0)


def _wait_for_terminal(
    url: str, agent_id: str, *, poll: float = 1.5, timeout: float | None = None
) -> dict:
    start = time.monotonic()
    while True:
        agent = _get(f"{url}/api/agents/{agent_id}").json()
        if agent.get("status") in TERMINAL_STATUSES:
            return agent
        if timeout is not None and time.monotonic() - start > timeout:
            return agent
        time.sleep(poll)


def _print_queued(agent: dict, target: str) -> None:
    mode = "loop" if agent.get("loop") else "single-shot"
    print(f"Queued {mode} agent on {target}")
    print(f"  id:    {agent['id']}")
    print(f"  title: {agent['title']}")


def _final_line(agent: dict) -> str:
    parts = [f"final: {agent['status']}"]
    if agent.get("summary"):
        parts.append(agent["summary"])
    return " | ".join(parts)


# --------------------------------------------------------------------------- #
# assign / run
# --------------------------------------------------------------------------- #
def cmd_assign(args: argparse.Namespace) -> int:
    return _run_assign(args, follow_default=False)


def cmd_run(args: argparse.Namespace) -> int:
    # `run` is `assign` with --follow implied.
    args.prompts = [args.prompt]
    args.from_file = None
    args.follow = True
    args.wait = False
    return _run_assign(args, follow_default=True)


def _run_assign(args: argparse.Namespace, *, follow_default: bool) -> int:
    url = coordinator_url()
    prompts = _read_prompts(args)
    if prompts is None:
        return 2
    if not prompts:
        print(
            "No prompt given. Pass a prompt, several prompts, --from-file FILE, "
            "or pipe a prompt via stdin.",
            file=sys.stderr,
        )
        return 2

    follow = getattr(args, "follow", follow_default)
    wait = getattr(args, "wait", False)
    wait_timeout = getattr(args, "wait_timeout", 0) or None
    as_json = getattr(args, "json", False)
    target = args.node or "any available mini"

    # ---- single prompt: supports --follow / --wait / --json ----
    if len(prompts) == 1:
        agent = _post(f"{url}/api/agents", json=build_assign_payload(args, prompts[0])).json()
        if as_json:
            print(json.dumps(agent, indent=2))
            return 0
        _print_queued(agent, target)

        if follow:
            print("--- logs (follow) — Ctrl-C to detach ---")
            try:
                _follow_logs(url, agent["id"], timeout=wait_timeout)
            except KeyboardInterrupt:
                print("\n[interrupted — agent keeps running on the worker]")
            final = _get(f"{url}/api/agents/{agent['id']}").json()
            print(f"\n--- {_final_line(final)} ---")
            return _exit_code_for_status(final["status"])

        if wait:
            final = _wait_for_terminal(url, agent["id"], timeout=wait_timeout)
            print(_final_line(final))
            return _exit_code_for_status(final["status"])

        return 0

    # ---- batch: multiple prompts ----
    if follow:
        print(
            f"--follow only works with a single prompt (got {len(prompts)}). "
            "Use --wait to block for the whole batch.",
            file=sys.stderr,
        )
        return 2

    created = [
        _post(f"{url}/api/agents", json=build_assign_payload(args, prompt)).json()
        for prompt in prompts
    ]
    ids = [a["id"] for a in created]

    if as_json:
        print(json.dumps(created, indent=2))
        return 0

    print(f"Queueing {len(prompts)} agents on {target}")
    for agent in created:
        print(f"  {agent['id']}  {agent['title']}")

    if wait:
        print("Waiting for all to finish...")
        results = [_wait_for_terminal(url, aid, timeout=wait_timeout) for aid in ids]
        for aid, final in zip(ids, results):
            print(f"  {aid}  {final['status']:10}  {final.get('summary') or ''}")
        return 0 if all(f["status"] == "completed" for f in results) else 1

    return 0


# --------------------------------------------------------------------------- #
# status / nodes / node / agents / show
# --------------------------------------------------------------------------- #
def cmd_status(_: argparse.Namespace) -> int:
    data = _get(f"{coordinator_url()}/api/dashboard").json()

    print("\nMiniFleet Status\n" + "=" * 40)
    totals = data["totals"]
    print(
        f"Fleet: {totals['running']} running · "
        f"{totals['completed']} done · "
        f"{totals['queued']} queued · "
        f"{totals['failed']} failed\n"
    )

    for item in data["nodes"]:
        node = item["node"]
        marker = "●" if node["status"] == "online" else "○"
        dtype = node.get("device_type", "mac")
        print(
            f"{marker} {node['name']:14} {dtype:10}  "
            f"{item['running']} running · {item['completed']} done · "
            f"{item['queued']} queued"
        )
        for agent in item["agents"][:5]:
            icon = STATUS_ICON.get(agent["status"], "?")
            summary = agent.get("summary") or agent["title"]
            loop_info = ""
            if agent.get("loop") and agent.get("iteration"):
                loop_info = f" [{agent['iteration']}/{agent.get('max_iterations', '?')}]"
            print(f"    {icon} {summary[:60]}{loop_info}")
        print()

    return 0


def cmd_nodes(_: argparse.Namespace) -> int:
    for node in _get(f"{coordinator_url()}/api/nodes").json():
        dtype = node.get("device_type", "mac")
        print(f"{node['name']:16} {dtype:12} {node['status']:8} {node['hostname']}")
    return 0


def cmd_node(args: argparse.Namespace) -> int:
    url = coordinator_url()
    node = next(
        (n for n in _get(f"{url}/api/nodes").json() if n["name"] == args.name), None
    )
    if not node:
        print(f"Node '{args.name}' not found", file=sys.stderr)
        return 1

    print(f"Node {node['name']} ({node.get('device_type', 'mac')})")
    print(f"  hostname:  {node['hostname']}")
    print(f"  status:    {node['status']}")
    print(f"  max_conc:  {node['max_concurrent'] or 'unlimited'}")
    if node.get("cpu_percent") is not None:
        print(
            f"  cpu/mem/disk: {node['cpu_percent']}% / "
            f"{node['memory_percent']}% / {node['disk_percent']}%"
        )
    if node.get("claude_version"):
        print(f"  claude:    {node['claude_version']}")
    print(f"  last_seen: {node.get('last_seen')}")

    agents = _get(
        f"{url}/api/agents", params={"node_id": node["id"], "limit": 200}
    ).json()
    print(f"  agents:    {len(agents)}")
    for agent in agents[:20]:
        icon = STATUS_ICON.get(agent["status"], "?")
        print(f"    {icon} {agent['status']:10} {agent['title'][:50]}")
    return 0


def cmd_agents(args: argparse.Namespace) -> int:
    url = coordinator_url()
    params: dict = {"limit": args.limit}
    if args.status:
        if args.status not in {"queued", "running", "completed", "failed", "cancelled"}:
            print(
                f"Invalid --status {args.status!r}. Choose from: "
                f"queued, running, completed, failed, cancelled",
                file=sys.stderr,
            )
            return 2
        params["status"] = args.status
    if args.node:
        node_id = _resolve_node_id(url, args.node)
        if not node_id:
            print(f"Node '{args.node}' not found", file=sys.stderr)
            return 1
        params["node_id"] = node_id

    agents = _get(f"{url}/api/agents", params=params).json()
    if args.json:
        print(json.dumps(agents, indent=2))
        return 0
    if not agents:
        print("No agents.")
        return 0

    print(f"{'ID':36} {'STATUS':10} {'NODE':16} {'MODE':5} {'TITLE'}")
    for a in agents:
        mode = "loop" if a.get("loop") else "one"
        print(
            f"{a['id']:36} {a['status']:10} "
            f"{(a.get('node_name') or '—'):16} {mode:5} {a['title'][:40]}"
        )
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    url = coordinator_url()
    a = _get(f"{url}/api/agents/{args.agent_id}").json()
    if args.json:
        print(json.dumps(a, indent=2))
        return 0

    print(f"Agent {a['id']}")
    print(f"  title:   {a['title']}")
    print(f"  status:  {a['status']}")
    print(f"  node:    {a.get('node_name') or '—'}")
    print(f"  repo:    {a.get('repo') or a.get('repo_path') or '—'}")
    print(
        f"  loop:    {'yes' if a.get('loop') else 'no'}  "
        f"iter {a.get('iteration')}/{a.get('max_iterations') or '—'}"
    )
    print(f"  phase:   {a.get('loop_phase') or '—'}")
    print(f"  cost:    ${a.get('estimated_cost_usd', 0):.2f}")
    print(f"  remote:  {a.get('remote')}")
    print(f"  created: {a.get('created_at')}")
    if a.get("started_at"):
        print(f"  started: {a.get('started_at')}")
    if a.get("completed_at"):
        print(f"  done:    {a.get('completed_at')}")
    if a.get("summary"):
        print(f"  summary: {a['summary']}")
    if a.get("error"):
        print(f"  error:   {a['error']}")
    return 0


# --------------------------------------------------------------------------- #
# dashboard / raw / repo / cancel / logs
# --------------------------------------------------------------------------- #
def cmd_dashboard(_: argparse.Namespace) -> int:
    url = coordinator_url()
    print(f"Opening {url}")
    webbrowser.open(url)
    return 0


def cmd_raw(_: argparse.Namespace) -> int:
    print(json.dumps(_get(f"{coordinator_url()}/api/dashboard").json(), indent=2))
    return 0


def cmd_repo_add(args: argparse.Namespace) -> int:
    repo = _post(
        f"{coordinator_url()}/api/repos",
        json={"name": args.name, "url": args.url, "branch": args.branch},
    ).json()
    print(f"Registered repo '{repo['name']}' → {repo['slug'] or repo['url']} ({repo['branch']})")
    return 0


def cmd_repo_list(_: argparse.Namespace) -> int:
    repos = _get(f"{coordinator_url()}/api/repos").json()
    if not repos:
        print("No repos registered. Add one: minifleet repo add my-app git@github.com:org/repo.git")
        return 0
    for repo in repos:
        print(f"{repo['name']:16} {repo['branch']:8} {repo['slug'] or repo['url']}")
    return 0


def cmd_repo_remove(args: argparse.Namespace) -> int:
    url = coordinator_url()
    resp = httpx.delete(f"{url}/api/repos/{args.name}", timeout=10.0)
    if resp.status_code == 404:
        print(f"Repo '{args.name}' not found", file=sys.stderr)
        return 1
    resp.raise_for_status()
    print(f"Removed repo '{args.name}'")
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    agent = _post(f"{coordinator_url()}/api/agents/{args.agent_id}/cancel").json()
    print(f"Cancelled: {agent['title']} ({agent['status']})")
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    url = coordinator_url()
    if args.follow:
        try:
            _follow_logs(url, args.agent_id, offset=args.offset)
        except KeyboardInterrupt:
            print()
        return 0
    data = _get(
        f"{url}/api/agents/{args.agent_id}/logs", params={"offset": args.offset}
    ).json()
    sys.stdout.write(data.get("content", ""))
    return 0


# --------------------------------------------------------------------------- #
# argparse wiring
# --------------------------------------------------------------------------- #
def _add_target_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--node", "-n", help="Target machine name (omit to use any idle mini)")
    p.add_argument("--repo", "-r", help="Registered private GitHub repo alias (e.g. my-app)")
    p.add_argument("--repo-path", help="Local path override (skip GitHub sync)")
    p.add_argument("--title", "-t", help="Short label for the dashboard")
    p.add_argument(
        "--remote",
        "-R",
        action="store_true",
        help="Enable Claude Remote Control (steer from claude.ai/code)",
    )


def _add_loop_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--no-loop", action="store_true", help="Single claude -p run instead of playbook loop")
    p.add_argument("--max-iterations", type=int, default=20)
    p.add_argument("--max-duration", type=int, default=7200, help="Max loop seconds")
    p.add_argument("--verify", help='Verify command, e.g. "npm test"')
    p.add_argument("--playbook", default="default", help="Playbook name")
    p.add_argument("--multi-agent", action="store_true", help="Worker + reviewer agents")
    p.add_argument("--cost-cap", type=float, help="Stop loop at estimated USD cap")
    p.add_argument("--completion-threshold", type=int, default=2)


def _add_follow_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--follow", "-f", action="store_true", help="Stream logs live until the job finishes")
    p.add_argument("--wait", "-w", action="store_true", help="Block until the job reaches a terminal status")
    p.add_argument("--wait-timeout", type=int, default=0, help="Max seconds to wait (0 = forever)")
    p.add_argument("--json", action="store_true", help="Print agent JSON instead of human text")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="minifleet",
        description="Mac fleet agent control (Minis, MacBooks, etc.)",
    )
    parser.add_argument(
        "--coordinator",
        default=None,
        help="Coordinator URL (default: MINIFLEET_COORDINATOR or http://127.0.0.1:8787)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # status
    p_status = sub.add_parser("status", help="Show fleet dashboard in terminal")
    p_status.set_defaults(func=cmd_status)

    # assign — one prompt, a batch of prompts, --from-file, or piped stdin
    p_assign = sub.add_parser("assign", help="Queue agent job(s) from prompt(s)")
    p_assign.add_argument(
        "prompts",
        nargs="*",
        help="Prompt(s) to run. Multiple prompts queue a batch. "
        "Omit to read from --from-file or piped stdin.",
    )
    p_assign.add_argument(
        "--from-file",
        "-F",
        help="File of prompts, one per line (# comments and blanks ignored; '-' for stdin)",
    )
    _add_target_args(p_assign)
    _add_loop_args(p_assign)
    _add_follow_args(p_assign)
    p_assign.set_defaults(func=cmd_assign, loop=True)

    # run — assign a single prompt and follow its logs to completion
    p_run = sub.add_parser("run", help="Assign a prompt and follow its logs until it finishes")
    p_run.add_argument("prompt", help="Prompt to run")
    _add_target_args(p_run)
    _add_loop_args(p_run)
    p_run.add_argument("--wait-timeout", type=int, default=0, help="Max seconds to wait (0 = forever)")
    p_run.add_argument("--json", action="store_true", help="Print agent JSON instead of human text")
    p_run.set_defaults(func=cmd_run, loop=True)

    # dashboard
    p_dash = sub.add_parser("dashboard", help="Open web dashboard")
    p_dash.set_defaults(func=cmd_dashboard)

    # nodes / node
    p_nodes = sub.add_parser("nodes", help="List registered Macs in the fleet")
    p_nodes.set_defaults(func=cmd_nodes)

    p_node = sub.add_parser("node", help="Show a machine and its agents")
    p_node.add_argument("name", help="Node name")
    p_node.set_defaults(func=cmd_node)

    # agents (alias: ls)
    p_agents = sub.add_parser("agents", aliases=["ls"], help="List agents across the fleet")
    p_agents.add_argument("--status", "-s", help="Filter: queued|running|completed|failed|cancelled")
    p_agents.add_argument("--node", "-n", help="Filter by node name")
    p_agents.add_argument("--limit", "-l", type=int, default=50)
    p_agents.add_argument("--json", action="store_true")
    p_agents.set_defaults(func=cmd_agents)

    # show
    p_show = sub.add_parser("show", help="Show details of one agent")
    p_show.add_argument("agent_id", help="Agent UUID")
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=cmd_show)

    # raw
    p_raw = sub.add_parser("raw", help="Dump dashboard JSON")
    p_raw.set_defaults(func=cmd_raw)

    # repo
    p_repo = sub.add_parser("repo", help="Manage private GitHub repos")
    repo_sub = p_repo.add_subparsers(dest="repo_command", required=True)

    p_repo_add = repo_sub.add_parser("add", help="Register a private GitHub repo")
    p_repo_add.add_argument("name", help="Short alias, e.g. my-app")
    p_repo_add.add_argument("url", help="git@github.com:org/repo.git")
    p_repo_add.add_argument("--branch", "-b", default="main")
    p_repo_add.set_defaults(func=cmd_repo_add)

    p_repo_list = repo_sub.add_parser("list", help="List registered repos")
    p_repo_list.set_defaults(func=cmd_repo_list)

    p_repo_remove = repo_sub.add_parser("remove", aliases=["rm"], help="Remove a registered repo")
    p_repo_remove.add_argument("name", help="Repo alias to remove")
    p_repo_remove.set_defaults(func=cmd_repo_remove)

    # cancel
    p_cancel = sub.add_parser("cancel", help="Cancel a queued or running agent")
    p_cancel.add_argument("agent_id", help="Agent UUID")
    p_cancel.set_defaults(func=cmd_cancel)

    # logs
    p_logs = sub.add_parser("logs", help="View agent logs from coordinator")
    p_logs.add_argument("agent_id", help="Agent UUID")
    p_logs.add_argument("--follow", "-f", action="store_true", help="Stream logs live")
    p_logs.add_argument("--offset", type=int, default=0, help="Byte offset into log")
    p_logs.set_defaults(func=cmd_logs)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.coordinator:
        os.environ["MINIFLEET_COORDINATOR"] = args.coordinator
    if hasattr(args, "no_loop") and args.no_loop:
        args.loop = False
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()