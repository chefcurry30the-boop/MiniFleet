"""MiniFleet worker daemon — runs on each Mac (Mini, MacBook, Studio, etc.)."""

from __future__ import annotations

import asyncio
import os
import socket
import sys
from pathlib import Path

import httpx

from minifleet.device import detect_device_type, device_label
from minifleet.loop.runner import LoopRunner
from minifleet.worker.executor import default_executor
from minifleet.worker.sync import RepoSyncer


class Worker:
    def __init__(
        self,
        *,
        coordinator_url: str,
        node_name: str,
        max_concurrent: int = 0,
        poll_interval: float = 5.0,
        heartbeat_interval: float = 30.0,
        data_dir: Path | None = None,
    ):
        self.coordinator_url = coordinator_url.rstrip("/")
        self.node_name = node_name
        self.max_concurrent = max_concurrent
        self.poll_interval = poll_interval
        self.heartbeat_interval = heartbeat_interval
        self.data_dir = data_dir or Path(os.environ.get("MINIFLEET_DATA", Path.home() / ".minifleet"))
        self.logs_dir = self.data_dir / "logs"
        self.repos_dir = self.data_dir / "repos"
        self.node_id: str | None = None
        self.executor = default_executor()
        self.loop_runner = LoopRunner(
            executor=self.executor,
            data_dir=self.data_dir,
            coordinator_url=self.coordinator_url,
        )
        self.syncer: RepoSyncer | None = None
        self._active: set[asyncio.Task] = set()

    @property
    def hostname(self) -> str:
        return socket.gethostname()

    async def register(self, client: httpx.AsyncClient) -> None:
        device_type = detect_device_type()
        resp = await client.post(
            f"{self.coordinator_url}/api/nodes/register",
            json={
                "name": self.node_name,
                "hostname": self.hostname,
                "max_concurrent": self.max_concurrent,
                "device_type": device_type,
            },
        )
        resp.raise_for_status()
        self.node_id = resp.json()["id"]
        self.syncer = RepoSyncer(
            coordinator_url=self.coordinator_url,
            node_id=self.node_id,
            repos_dir=self.repos_dir,
            sync_interval=float(os.environ.get("MINIFLEET_SYNC_INTERVAL", "300")),
        )
        ok, method = await self.syncer.verify_github()
        print(f"[minifleet-worker] registered as {self.node_name} ({device_label(device_type)}) ({self.node_id})")
        print(f"[minifleet-worker] github: {'ok' if ok else 'FAILED'} ({method})")

    async def heartbeat_loop(self, client: httpx.AsyncClient) -> None:
        while True:
            if self.node_id:
                try:
                    await client.post(f"{self.coordinator_url}/api/nodes/{self.node_id}/heartbeat")
                except httpx.HTTPError as exc:
                    print(f"[minifleet-worker] heartbeat failed: {exc}")
            await asyncio.sleep(self.heartbeat_interval)

    async def resolve_repo_path(self, client: httpx.AsyncClient, agent: dict) -> Path | None:
        repo_name = agent.get("repo")
        if repo_name and self.syncer:
            result = await self.syncer.sync_one(client, repo_name)
            if result and result.ok:
                return Path(result.path)
            if result and not result.ok:
                raise RuntimeError(f"Failed to sync repo '{repo_name}': {result.error}")
            raise RuntimeError(f"Repo '{repo_name}' not registered on coordinator")

        if agent.get("repo_path"):
            return Path(agent["repo_path"])
        return None

    async def run_agent(self, client: httpx.AsyncClient, agent: dict) -> None:
        agent_id = agent["id"]
        prompt = agent["prompt"]
        use_loop = agent.get("loop", True)
        log_path = self.logs_dir / f"{agent_id}.log"

        mode = "loop" if use_loop else "single"
        print(f"[minifleet-worker] starting {mode} agent {agent_id}: {agent.get('title', prompt[:60])}")

        try:
            repo_path = await self.resolve_repo_path(client, agent)

            if use_loop:
                result = await self.loop_runner.run(client, agent, repo_path)
                summary = (
                    f"{result.summary} ({result.iterations} iterations, "
                    f"${result.estimated_cost_usd:.2f} est., {result.stop_reason})"
                )
                await client.patch(
                    f"{self.coordinator_url}/api/agents/{agent_id}",
                    json={
                        "status": "completed" if result.success else "failed",
                        "summary": summary,
                        "error": result.error,
                        "iteration": result.iterations,
                        "estimated_cost_usd": result.estimated_cost_usd,
                        "loop_phase": "done",
                    },
                )
            else:
                result = await self.executor.run(
                    prompt=prompt,
                    repo_path=repo_path,
                    log_path=log_path,
                    title=agent.get("title"),
                    remote=bool(agent.get("remote")),
                )
                await client.patch(
                    f"{self.coordinator_url}/api/agents/{agent_id}",
                    json={
                        "status": "completed" if result.success else "failed",
                        "summary": result.summary,
                        "error": result.error,
                        "claude_session_id": result.claude_session_id,
                    },
                )

            status = "completed" if result.success else "failed"
            print(f"[minifleet-worker] agent {agent_id} {status}")
        except Exception as exc:  # noqa: BLE001
            await client.patch(
                f"{self.coordinator_url}/api/agents/{agent_id}",
                json={"status": "failed", "summary": "Worker error", "error": str(exc)},
            )
            print(f"[minifleet-worker] agent {agent_id} crashed: {exc}")

    async def poll_loop(self, client: httpx.AsyncClient) -> None:
        while True:
            self._active = {t for t in self._active if not t.done()}

            if self.node_id:
                while True:
                    if self.max_concurrent > 0 and len(self._active) >= self.max_concurrent:
                        break
                    try:
                        resp = await client.post(
                            f"{self.coordinator_url}/api/nodes/{self.node_id}/claim"
                        )
                        resp.raise_for_status()
                        agent = resp.json().get("agent")
                        if not agent:
                            break
                        task = asyncio.create_task(self.run_agent(client, agent))
                        self._active.add(task)
                    except httpx.HTTPError as exc:
                        print(f"[minifleet-worker] claim failed: {exc}")
                        break

            await asyncio.sleep(self.poll_interval)

    async def run(self) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.repos_dir.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=600.0) as client:
            await self.register(client)
            tasks = [
                self.heartbeat_loop(client),
                self.poll_loop(client),
            ]
            if self.syncer:
                tasks.append(self.syncer.sync_loop(client))
            await asyncio.gather(*tasks)


def main() -> None:
    coordinator = os.environ.get("MINIFLEET_COORDINATOR", "http://127.0.0.1:8787")
    node_name = os.environ.get("MINIFLEET_NODE_NAME")
    if not node_name:
        print("MINIFLEET_NODE_NAME is required (e.g. mac-mini-1, macbook-pro)", file=sys.stderr)
        sys.exit(1)

    max_concurrent = int(os.environ.get("MINIFLEET_MAX_CONCURRENT", "0"))
    worker = Worker(
        coordinator_url=coordinator,
        node_name=node_name,
        max_concurrent=max_concurrent,
    )

    try:
        asyncio.run(worker.run())
    except KeyboardInterrupt:
        print("\n[minifleet-worker] stopped")


if __name__ == "__main__":
    main()
