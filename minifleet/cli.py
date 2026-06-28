#!/usr/bin/env python3
"""CLI for assigning work and checking fleet status from your laptop."""

from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser

import httpx


def coordinator_url() -> str:
    return os.environ.get("MINIFLEET_COORDINATOR", "http://127.0.0.1:8787").rstrip("/")


def cmd_status(_: argparse.Namespace) -> int:
    resp = httpx.get(f"{coordinator_url()}/api/dashboard", timeout=10.0)
    resp.raise_for_status()
    data = resp.json()

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
        status = "●" if node["status"] == "online" else "○"
        print(
            f"{status} {node['name']:12}  "
            f"{item['running']} running · {item['completed']} done · "
            f"{item['queued']} queued"
        )
        for agent in item["agents"][:5]:
            icon = {"running": "▶", "completed": "✓", "failed": "✗", "queued": "…"}.get(
                agent["status"], "?"
            )
            summary = agent.get("summary") or agent["title"]
            loop_info = ""
            if agent.get("loop") and agent.get("iteration"):
                loop_info = f" [{agent['iteration']}/{agent.get('max_iterations','?')}]"
            print(f"    {icon} {summary[:60]}{loop_info}")
        print()

    return 0


def cmd_assign(args: argparse.Namespace) -> int:
    loop_config = None
    if args.loop:
        loop_config = {
            "enabled": True,
            "max_iterations": args.max_iterations,
            "max_duration_seconds": args.max_duration,
            "verify_command": args.verify,
            "playbook": args.playbook,
            "multi_agent": args.multi_agent,
            "cost_cap_usd": args.cost_cap,
            "completion_threshold": args.completion_threshold,
        }
        if args.git_push:
            loop_config["git_push"] = True
            loop_config["git_branch_prefix"] = args.git_branch_prefix
        loop_config = {k: v for k, v in loop_config.items() if v is not None}

    payload = {
        "prompt": args.prompt,
        "node_name": args.node,
        "repo": args.repo,
        "repo_path": args.repo_path,
        "title": args.title,
        "remote": args.remote,
        "loop": args.loop,
        "loop_config": loop_config,
    }
    payload = {k: v for k, v in payload.items() if v is not None}

    resp = httpx.post(f"{coordinator_url()}/api/agents", json=payload, timeout=10.0)
    resp.raise_for_status()
    agent = resp.json()
    target = args.node or "any available mini"
    mode = "loop" if args.loop else "single-shot"
    print(f"Queued {mode} agent on {target}")
    print(f"  id:    {agent['id']}")
    print(f"  title: {agent['title']}")
    return 0


def cmd_dashboard(_: argparse.Namespace) -> int:
    url = coordinator_url()
    print(f"Opening {url}")
    webbrowser.open(url)
    return 0


def cmd_nodes(_: argparse.Namespace) -> int:
    resp = httpx.get(f"{coordinator_url()}/api/nodes", timeout=10.0)
    resp.raise_for_status()
    for node in resp.json():
        dtype = node.get("device_type", "mac")
        print(f"{node['name']:16} {dtype:12} {node['status']:8} {node['hostname']}")
    return 0


def cmd_raw(args: argparse.Namespace) -> int:
    resp = httpx.get(f"{coordinator_url()}/api/dashboard", timeout=10.0)
    resp.raise_for_status()
    print(json.dumps(resp.json(), indent=2))
    return 0


def cmd_repo_add(args: argparse.Namespace) -> int:
    resp = httpx.post(
        f"{coordinator_url()}/api/repos",
        json={"name": args.name, "url": args.url, "branch": args.branch},
        timeout=10.0,
    )
    resp.raise_for_status()
    repo = resp.json()
    print(f"Registered repo '{repo['name']}' → {repo['slug'] or repo['url']} ({repo['branch']})")
    return 0


def cmd_repo_list(_: argparse.Namespace) -> int:
    resp = httpx.get(f"{coordinator_url()}/api/repos", timeout=10.0)
    resp.raise_for_status()
    repos = resp.json()
    if not repos:
        print("No repos registered. Add one: minifleet repo add my-app git@github.com:org/repo.git")
        return 0
    for repo in repos:
        print(f"{repo['name']:16} {repo['branch']:8} {repo['slug'] or repo['url']}")
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    resp = httpx.post(f"{coordinator_url()}/api/agents/{args.agent_id}/cancel", timeout=10.0)
    resp.raise_for_status()
    agent = resp.json()
    print(f"Cancelled: {agent['title']} ({agent['status']})")
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    offset = args.offset
    url = coordinator_url()
    if args.follow:
        import sys

        try:
            with httpx.stream(
                "GET",
                f"{url}/api/agents/{args.agent_id}/logs/stream",
                params={"offset": offset},
                timeout=None,
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = json.loads(line[6:])
                    if data.get("done"):
                        break
                    chunk = data.get("content", "")
                    if chunk:
                        sys.stdout.write(chunk)
                        sys.stdout.flush()
        except KeyboardInterrupt:
            print()
        return 0

    resp = httpx.get(
        f"{url}/api/agents/{args.agent_id}/logs",
        params={"offset": offset},
        timeout=10.0,
    )
    resp.raise_for_status()
    data = resp.json()
    print(data.get("content", ""), end="")
    if args.follow is False and data.get("size", 0) > data.get("offset", 0):
        pass
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="minifleet", description="Mac fleet agent control (Minis, MacBooks, etc.)")
    parser.add_argument(
        "--coordinator",
        default=None,
        help="Coordinator URL (default: MINIFLEET_COORDINATOR or http://127.0.0.1:8787)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="Show fleet dashboard in terminal")
    p_status.set_defaults(func=cmd_status)

    p_assign = sub.add_parser("assign", help="Queue an agent job")
    p_assign.add_argument("prompt", help="What the agent should do")
    p_assign.add_argument("--node", "-n", help="Target machine name (e.g. mac-mini-1, macbook-pro)")
    p_assign.add_argument(
        "--repo",
        "-r",
        help="Registered private GitHub repo alias (e.g. my-app)",
    )
    p_assign.add_argument(
        "--repo-path",
        help="Local path override (skip GitHub sync)",
    )
    p_assign.add_argument("--title", "-t", help="Short label for the dashboard")
    p_assign.add_argument(
        "--remote",
        "-R",
        action="store_true",
        help="Enable Claude Remote Control (steer from claude.ai/code or Claude app)",
    )
    p_assign.add_argument(
        "--no-loop",
        action="store_true",
        help="Single claude -p run instead of playbook loop",
    )
    p_assign.add_argument("--max-iterations", type=int, default=20)
    p_assign.add_argument("--max-duration", type=int, default=7200, help="Max loop seconds")
    p_assign.add_argument("--verify", help='Verify command, e.g. "npm test"')
    p_assign.add_argument("--playbook", default="default", help="Playbook name")
    p_assign.add_argument("--multi-agent", action="store_true", help="Worker + reviewer agents")
    p_assign.add_argument("--cost-cap", type=float, help="Stop loop at estimated USD cap")
    p_assign.add_argument("--completion-threshold", type=int, default=2)
    p_assign.add_argument(
        "--git-push",
        action="store_true",
        help="Auto branch, commit, and push on success",
    )
    p_assign.add_argument("--git-branch-prefix", default="minifleet")
    p_assign.set_defaults(func=cmd_assign, loop=True)

    p_dash = sub.add_parser("dashboard", help="Open web dashboard")
    p_dash.set_defaults(func=cmd_dashboard)

    p_nodes = sub.add_parser("nodes", help="List registered Macs in the fleet")
    p_nodes.set_defaults(func=cmd_nodes)

    p_raw = sub.add_parser("raw", help="Dump dashboard JSON")
    p_raw.set_defaults(func=cmd_raw)

    p_repo = sub.add_parser("repo", help="Manage private GitHub repos")
    repo_sub = p_repo.add_subparsers(dest="repo_command", required=True)

    p_repo_add = repo_sub.add_parser("add", help="Register a private GitHub repo")
    p_repo_add.add_argument("name", help="Short alias, e.g. my-app")
    p_repo_add.add_argument("url", help="git@github.com:org/repo.git")
    p_repo_add.add_argument("--branch", "-b", default="main")
    p_repo_add.set_defaults(func=cmd_repo_add)

    p_repo_list = repo_sub.add_parser("list", help="List registered repos")
    p_repo_list.set_defaults(func=cmd_repo_list)

    p_cancel = sub.add_parser("cancel", help="Cancel a queued or running agent")
    p_cancel.add_argument("agent_id", help="Agent UUID")
    p_cancel.set_defaults(func=cmd_cancel)

    p_logs = sub.add_parser("logs", help="View agent logs from coordinator")
    p_logs.add_argument("agent_id", help="Agent UUID")
    p_logs.add_argument("--follow", "-f", action="store_true", help="Stream logs live")
    p_logs.add_argument("--offset", type=int, default=0, help="Byte offset into log")
    p_logs.set_defaults(func=cmd_logs)

    args = parser.parse_args()
    if args.coordinator:
        os.environ["MINIFLEET_COORDINATOR"] = args.coordinator
    if hasattr(args, "no_loop") and args.no_loop:
        args.loop = False
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
