"""Machine-verifiable completion checks."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path


async def run_verify(command: str | None, repo_path: Path | None) -> tuple[bool, str]:
    if not command:
        return True, "No verify command configured — skipped"

    cwd = str(repo_path) if repo_path and repo_path.exists() else None
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
        env=os.environ.copy(),
    )
    stdout, _ = await proc.communicate()
    output = stdout.decode("utf-8", errors="replace")
    ok = proc.returncode == 0
    return ok, output
