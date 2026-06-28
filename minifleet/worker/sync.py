"""Worker-side repo sync against coordinator registry."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from minifleet.github import SyncResult, check_github_auth, sync_repo


class RepoSyncer:
    def __init__(
        self,
        *,
        coordinator_url: str,
        node_id: str,
        repos_dir: Path,
        sync_interval: float = 300.0,
    ):
        self.coordinator_url = coordinator_url.rstrip("/")
        self.node_id = node_id
        self.repos_dir = repos_dir
        self.sync_interval = sync_interval
        self.last_results: list[SyncResult] = []
        self.github_ok = False
        self.github_method = ""

    async def verify_github(self) -> tuple[bool, str]:
        self.github_ok, self.github_method = await check_github_auth()
        return self.github_ok, self.github_method

    async def fetch_registry(self, client: httpx.AsyncClient) -> list[dict]:
        resp = await client.get(f"{self.coordinator_url}/api/repos")
        resp.raise_for_status()
        return resp.json()

    async def sync_all(self, client: httpx.AsyncClient) -> list[SyncResult]:
        repos = await self.fetch_registry(client)
        results: list[SyncResult] = []

        for repo in repos:
            dest = self.repos_dir / repo["name"]
            result = await sync_repo(
                name=repo["name"],
                url=repo["url"],
                branch=repo["branch"],
                dest=dest,
            )
            results.append(result)

        self.last_results = results
        await self.report_status(client, results)
        return results

    async def sync_one(self, client: httpx.AsyncClient, repo_name: str) -> SyncResult | None:
        repos = await self.fetch_registry(client)
        match = next((r for r in repos if r["name"] == repo_name), None)
        if not match:
            return None

        dest = self.repos_dir / match["name"]
        result = await sync_repo(
            name=match["name"],
            url=match["url"],
            branch=match["branch"],
            dest=dest,
        )
        self.last_results = [r for r in self.last_results if r.name != repo_name] + [result]
        await self.report_status(client, self.last_results)
        return result

    def local_path(self, repo_name: str) -> Path:
        return self.repos_dir / repo_name

    async def report_status(self, client: httpx.AsyncClient, results: list[SyncResult]) -> None:
        payload = {
            "github_ok": self.github_ok,
            "github_method": self.github_method,
            "repos": [
                {
                    "name": r.name,
                    "ok": r.ok,
                    "path": r.path,
                    "branch": r.branch,
                    "commit": r.commit,
                    "error": r.error,
                }
                for r in results
            ],
        }
        try:
            await client.post(
                f"{self.coordinator_url}/api/nodes/{self.node_id}/repos/status",
                json=payload,
            )
        except httpx.HTTPError:
            pass

    async def sync_loop(self, client: httpx.AsyncClient) -> None:
        await self.verify_github()
        while True:
            try:
                await self.sync_all(client)
            except httpx.HTTPError as exc:
                print(f"[minifleet-worker] repo sync failed: {exc}")
            await asyncio.sleep(self.sync_interval)
