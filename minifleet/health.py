"""Lightweight node health metrics for fleet dashboard."""

from __future__ import annotations

import shutil
import subprocess
from typing import Any

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore[assignment]


def claude_version() -> str | None:
    try:
        proc = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            return proc.stdout.strip() or proc.stderr.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def collect_health() -> dict[str, Any]:
    """Return CPU, memory, disk, and Claude version for heartbeat."""
    health: dict[str, Any] = {
        "cpu_percent": None,
        "memory_percent": None,
        "disk_percent": None,
        "claude_version": claude_version(),
    }
    if psutil is None:
        return health

    try:
        health["cpu_percent"] = round(psutil.cpu_percent(interval=0.1), 1)
        health["memory_percent"] = round(psutil.virtual_memory().percent, 1)
        health["disk_percent"] = round(psutil.disk_usage("/").percent, 1)
    except Exception:  # noqa: BLE001
        pass

    return health
