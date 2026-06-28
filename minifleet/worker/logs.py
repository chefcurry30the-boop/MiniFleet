"""Sync agent logs from worker to coordinator."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx


class LogSyncer:
    """Periodically upload log tail to coordinator for remote viewing."""

    def __init__(
        self,
        *,
        coordinator_url: str,
        agent_id: str,
        log_path: Path,
        interval: float = 2.0,
    ):
        self.coordinator_url = coordinator_url.rstrip("/")
        self.agent_id = agent_id
        self.log_path = log_path
        self.interval = interval
        self._offset = 0
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self, client: httpx.AsyncClient) -> None:
        while not self._stop.is_set():
            await self._push_once(client)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass
        await self._push_once(client)

    async def _push_once(self, client: httpx.AsyncClient) -> None:
        if not self.log_path.exists():
            return
        try:
            data = self.log_path.read_bytes()
        except OSError:
            return
        if len(data) <= self._offset:
            return
        chunk = data[self._offset :]
        self._offset = len(data)
        try:
            await client.post(
                f"{self.coordinator_url}/api/agents/{self.agent_id}/logs",
                json={"chunk": chunk.decode("utf-8", errors="replace"), "offset": self._offset},
                timeout=10.0,
            )
        except httpx.HTTPError:
            self._offset -= len(chunk)

    @staticmethod
    def append_loop_log(main_log: Path, iteration_log: Path, label: str) -> None:
        """Merge loop iteration logs into the main agent log."""
        main_log.parent.mkdir(parents=True, exist_ok=True)
        with main_log.open("a", encoding="utf-8") as out:
            out.write(f"\n--- {label} ---\n")
            if iteration_log.exists():
                out.write(iteration_log.read_text(encoding="utf-8", errors="replace"))
            out.write("\n")
