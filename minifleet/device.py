"""Detect Mac hardware type for fleet registration."""

from __future__ import annotations

import os
import re
import subprocess


def detect_device_type() -> str:
    """Return fleet device type: macbook, mac-mini, mac-studio, imac, mac-pro, or mac."""
    override = os.environ.get("MINIFLEET_DEVICE_TYPE", "").strip().lower()
    if override:
        return override

    try:
        out = subprocess.check_output(
            ["system_profiler", "SPHardwareDataType"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=8,
        )
        match = re.search(r"Model Name:\s*(.+)", out)
        if match:
            name = match.group(1).strip().lower()
            if "macbook" in name:
                return "macbook"
            if "mac mini" in name:
                return "mac-mini"
            if "mac studio" in name:
                return "mac-studio"
            if "imac" in name:
                return "imac"
            if "mac pro" in name:
                return "mac-pro"
    except (subprocess.SubprocessError, OSError):
        pass

    try:
        model = subprocess.check_output(["sysctl", "-n", "hw.model"], text=True, timeout=3).strip().lower()
        if "macbook" in model:
            return "macbook"
        if "macmini" in model:
            return "mac-mini"
        if "macstudio" in model:
            return "mac-studio"
    except (subprocess.SubprocessError, OSError):
        pass

    return "mac"


def device_label(device_type: str) -> str:
    labels = {
        "macbook": "MacBook",
        "mac-mini": "Mac Mini",
        "mac-studio": "Mac Studio",
        "imac": "iMac",
        "mac-pro": "Mac Pro",
        "mac": "Mac",
    }
    return labels.get(device_type, device_type)


def suggest_node_name(device_type: str) -> str:
    """Suggest a default node name based on hardware."""
    import socket

    host = socket.gethostname().split(".")[0].lower().replace(" ", "-")[:20]
    prefix = {
        "macbook": "macbook",
        "mac-mini": "mac-mini",
        "mac-studio": "mac-studio",
        "imac": "imac",
        "mac-pro": "mac-pro",
    }.get(device_type, "mac")
    if host and host not in ("localhost", "mac"):
        return f"{prefix}-{host}"
    return prefix
