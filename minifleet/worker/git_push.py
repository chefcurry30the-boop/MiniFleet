"""Auto branch, commit, and push after successful agent jobs."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path


def _safe_branch_name(prefix: str, agent_id: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._/-]", "-", f"{prefix}/{agent_id[:8]}")
    return slug[:120]


async def _run_git(args: list[str], cwd: Path) -> tuple[bool, str]:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    output = stdout.decode("utf-8", errors="replace")
    return proc.returncode == 0, output


async def push_job_changes(
    repo_path: Path,
    *,
    agent_id: str,
    title: str,
    summary: str,
    branch_prefix: str = "minifleet",
) -> tuple[bool, str | None, str]:
    """Create branch, commit changes, push to origin. Returns (ok, branch, message)."""
    if not repo_path.exists() or not (repo_path / ".git").exists():
        return False, None, "Not a git repository"

    branch = _safe_branch_name(branch_prefix, agent_id)
    commit_msg = f"minifleet: {title}\n\n{summary[:500]}"

    ok, out = await _run_git(["status", "--porcelain"], repo_path)
    if not ok:
        return False, None, out.strip() or "git status failed"

    if not out.strip():
        return True, branch, "No changes to commit"

    steps = [
        (["checkout", "-B", branch], "checkout branch"),
        (["add", "-A"], "stage"),
        (["commit", "-m", commit_msg], "commit"),
        (["push", "-u", "origin", branch], "push"),
    ]
    for args, label in steps:
        ok, step_out = await _run_git(args, repo_path)
        if not ok:
            if label == "commit" and "nothing to commit" in step_out.lower():
                return True, branch, "Nothing to commit"
            return False, branch, f"{label} failed: {step_out.strip()[-300:]}"

    return True, branch, f"Pushed to {branch}"
