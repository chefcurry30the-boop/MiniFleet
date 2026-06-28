"""Clone and sync private GitHub repos on fleet workers."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass
class SyncResult:
    name: str
    ok: bool
    path: str
    branch: str
    commit: str | None = None
    error: str | None = None


def github_token() -> str | None:
    return os.environ.get("MINIFLEET_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")


def auth_url(url: str) -> str:
    """Inject token into HTTPS GitHub URLs for private repo access."""
    token = github_token()
    if not token or not url.startswith("https://"):
        return url
    parsed = urlparse(url)
    if parsed.hostname not in ("github.com", "www.github.com"):
        return url
    return f"https://{token}@{parsed.hostname}{parsed.path}"


def normalize_github_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if url.endswith(".git"):
        return url
    if url.startswith("git@"):
        return url if url.endswith(".git") else f"{url}.git"
    if "github.com" in url:
        return url if url.endswith(".git") else f"{url}.git"
    return url


def parse_repo_slug(url: str) -> str | None:
    """Extract org/repo from a GitHub URL."""
    if url.startswith("git@"):
        match = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
        return match.group(1) if match else None
    parsed = urlparse(url)
    if parsed.hostname and "github.com" in parsed.hostname:
        parts = parsed.path.strip("/").removesuffix(".git")
        return parts if "/" in parts else None
    return None


async def run_git(*args: str, cwd: Path | None = None, env: dict | None = None) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env or os.environ.copy(),
    )
    stdout, _ = await proc.communicate()
    return proc.returncode or 0, stdout.decode("utf-8", errors="replace")


async def current_commit(path: Path) -> str | None:
    code, out = await run_git("-C", str(path), "rev-parse", "--short", "HEAD")
    return out.strip() if code == 0 else None


async def sync_repo(
    *,
    name: str,
    url: str,
    branch: str,
    dest: Path,
) -> SyncResult:
    dest.parent.mkdir(parents=True, exist_ok=True)
    clone_url = auth_url(normalize_github_url(url))

    if dest.exists() and (dest / ".git").exists():
        code, out = await run_git("-C", str(dest), "fetch", "origin", branch)
        if code != 0:
            return SyncResult(name=name, ok=False, path=str(dest), branch=branch, error=out[-500:])
        code, out = await run_git("-C", str(dest), "checkout", branch)
        if code != 0:
            return SyncResult(name=name, ok=False, path=str(dest), branch=branch, error=out[-500:])
        code, out = await run_git("-C", str(dest), "pull", "--ff-only", "origin", branch)
        if code != 0:
            return SyncResult(name=name, ok=False, path=str(dest), branch=branch, error=out[-500:])
        commit = await current_commit(dest)
        return SyncResult(name=name, ok=True, path=str(dest), branch=branch, commit=commit)

    if dest.exists():
        shutil.rmtree(dest)

    code, out = await run_git(
        "clone",
        "--branch",
        branch,
        "--single-branch",
        clone_url,
        str(dest),
    )
    if code != 0:
        return SyncResult(name=name, ok=False, path=str(dest), branch=branch, error=out[-500:])

    commit = await current_commit(dest)
    return SyncResult(name=name, ok=True, path=str(dest), branch=branch, commit=commit)


async def check_github_auth() -> tuple[bool, str]:
    """Verify GitHub access via SSH or HTTPS token."""
    if github_token():
        code, out = await run_git("ls-remote", auth_url("https://github.com/octocat/Hello-World.git"))
        if code == 0:
            return True, "HTTPS token"
        return False, out[-200:] or "HTTPS token rejected"

    if not shutil.which("ssh"):
        return False, "No GITHUB_TOKEN and ssh not found"

    code, out = await run_git(
        "-c",
        "StrictHostKeyChecking=accept-new",
        "ls-remote",
        "git@github.com:octocat/Hello-World.git",
    )
    if code == 0:
        return True, "SSH key"
    return False, out[-200:] or "SSH auth failed (add deploy key to your private repos)"
