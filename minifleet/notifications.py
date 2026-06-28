"""Completion notifications — macOS, webhook, or log file."""

from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

DATA_DIR = Path(os.environ.get("MINIFLEET_DATA", Path.home() / ".minifleet"))


def _notify_macos(title: str, message: str) -> None:
    if os.environ.get("MINIFLEET_NOTIFY_MACOS", "1").lower() in ("0", "false", "no"):
        return
    script = f'display notification {json.dumps(message)} with title {json.dumps(title)}'
    try:
        subprocess.run(["osascript", "-e", script], check=False, capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _notify_webhook(payload: dict[str, Any]) -> None:
    url = os.environ.get("MINIFLEET_WEBHOOK_URL", "").strip()
    if not url:
        return
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)  # noqa: S310
    except Exception:  # noqa: BLE001
        pass


def _notify_log(payload: dict[str, Any]) -> None:
    log_path = DATA_DIR / "notifications.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")


def notify_agent_event(
    *,
    event: str,
    agent: dict[str, Any],
    node_name: str | None = None,
) -> None:
    """Fire notifications for agent lifecycle events."""
    events = os.environ.get("MINIFLEET_NOTIFY_ON", "completed,failed,cancelled")
    if event not in {e.strip() for e in events.split(",")}:
        return

    status = agent.get("status", event)
    title = agent.get("title", "MiniFleet agent")
    node = node_name or agent.get("node_name") or "fleet"
    summary = agent.get("summary") or agent.get("error") or status

    icons = {"completed": "✓", "failed": "✗", "cancelled": "—", "running": "▶"}
    icon = icons.get(status, "•")
    message = f"{icon} {node} · {summary[:120]}"

    payload = {
        "event": event,
        "agent_id": agent.get("id"),
        "status": status,
        "node": node,
        "title": title,
        "summary": summary,
    }

    _notify_macos(f"MiniFleet · {title[:40]}", message)
    _notify_webhook(payload)
    _notify_log(payload)
