"""Claude Code executors for MiniFleet workers."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from minifleet.loop.config import COMPLETION_SIGNAL_DEFAULT


@dataclass
class ExecutionResult:
    summary: str
    output: str
    success: bool
    error: str | None = None
    claude_session_id: str | None = None


class AgentExecutor(ABC):
    @abstractmethod
    async def run(
        self,
        *,
        prompt: str,
        repo_path: Path | None,
        log_path: Path,
        title: str | None = None,
        remote: bool = False,
    ) -> ExecutionResult:
        raise NotImplementedError


def find_claude() -> str | None:
    env = os.environ.get("MINIFLEET_CLAUDE")
    if env and Path(env).exists():
        return env
    return shutil.which("claude")


def permission_mode() -> str:
    return os.environ.get("MINIFLEET_PERMISSION_MODE", "auto")


def extract_summary(output: str, prompt: str) -> str:
    text = output.strip()
    if not text:
        return prompt[:120]

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if paragraphs:
        last = paragraphs[-1].replace("\n", " ")
        if len(last) > 200:
            return last[:197] + "…"
        return last

    one_line = text.replace("\n", " ")
    return one_line[:197] + "…" if len(one_line) > 200 else one_line


async def list_claude_agents(*, cwd: str | None = None) -> list[dict]:
    claude_bin = find_claude()
    if not claude_bin:
        return []

    proc = await asyncio.create_subprocess_exec(
        claude_bin,
        "agents",
        "--json",
        "--all",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return []

    try:
        agents = json.loads(stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return []

    if cwd:
        agents = [a for a in agents if a.get("cwd") == cwd]
    return agents


class ClaudeCodeExecutor(AgentExecutor):
    """Run jobs via Claude Code CLI (`claude -p` or `claude --background`)."""

    def __init__(self, timeout_seconds: int = 7200):
        self.claude_bin = find_claude()
        self.timeout_seconds = timeout_seconds
        self.use_background = os.environ.get("MINIFLEET_BACKGROUND", "").lower() in (
            "1",
            "true",
            "yes",
        )

    async def run(
        self,
        *,
        prompt: str,
        repo_path: Path | None,
        log_path: Path,
        title: str | None = None,
        remote: bool = False,
    ) -> ExecutionResult:
        if not self.claude_bin:
            return ExecutionResult(
                summary="Claude Code CLI not found",
                output="",
                success=False,
                error="Install Claude Code: https://code.claude.com",
            )

        if self.use_background:
            return await self._run_background(
                prompt=prompt,
                repo_path=repo_path,
                log_path=log_path,
                title=title,
                remote=remote,
            )
        return await self._run_print(
            prompt=prompt,
            repo_path=repo_path,
            log_path=log_path,
            title=title,
            remote=remote,
        )

    async def _run_print(
        self,
        *,
        prompt: str,
        repo_path: Path | None,
        log_path: Path,
        title: str | None,
        remote: bool,
    ) -> ExecutionResult:
        cwd = str(repo_path) if repo_path and repo_path.exists() else None
        cmd = [
            self.claude_bin,
            "-p",
            "--permission-mode",
            permission_mode(),
            "--output-format",
            "text",
        ]
        if remote and title:
            cmd.extend(["--remote-control", title])
        cmd.append(prompt)

        return await self._exec_and_wait(cmd, cwd=cwd, log_path=log_path, prompt=prompt)

    async def _run_background(
        self,
        *,
        prompt: str,
        repo_path: Path | None,
        log_path: Path,
        title: str | None,
        remote: bool,
    ) -> ExecutionResult:
        cwd = str(repo_path) if repo_path and repo_path.exists() else None
        before = {a.get("sessionId") for a in await list_claude_agents(cwd=cwd)}

        cmd = [
            self.claude_bin,
            "--background",
            "--permission-mode",
            permission_mode(),
        ]
        if remote and title:
            cmd.extend(["--remote-control", title])
        cmd.append(prompt)

        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(f"$ {' '.join(cmd)}\n\n", encoding="utf-8")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
            env=os.environ.copy(),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        dispatch_out = stdout.decode("utf-8", errors="replace")
        with log_path.open("a", encoding="utf-8") as f:
            f.write(dispatch_out)

        if proc.returncode != 0:
            return ExecutionResult(
                summary="Failed to start Claude background agent",
                output=dispatch_out,
                success=False,
                error=dispatch_out[-500:] or f"exit code {proc.returncode}",
            )

        session_id = self._find_new_session(before, await list_claude_agents(cwd=cwd))
        if not session_id:
            return ExecutionResult(
                summary="Background agent started but session not found",
                output=dispatch_out,
                success=False,
                error="Could not locate session via `claude agents --json`",
            )

        remote_hint = ""
        if remote:
            remote_hint = f" · steer at claude.ai/code (session {session_id[:8]}…)"

        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            agents = await list_claude_agents()
            session = next((a for a in agents if a.get("sessionId") == session_id), None)

            if session is None:
                summary = f"Completed{remote_hint}" if remote else "Completed"
                return ExecutionResult(
                    summary=summary,
                    output=dispatch_out,
                    success=True,
                    claude_session_id=session_id,
                )

            status = (session.get("status") or "").lower()
            if status in {"completed", "done", "failed", "error"}:
                success = status not in {"failed", "error"}
                return ExecutionResult(
                    summary=f"{'Done' if success else 'Failed'}{remote_hint}",
                    output=dispatch_out,
                    success=success,
                    error=None if success else f"Claude session status: {status}",
                    claude_session_id=session_id,
                )

            await asyncio.sleep(10)

        return ExecutionResult(
            summary="Claude agent timed out",
            output=dispatch_out,
            success=False,
            error=f"Exceeded {self.timeout_seconds}s timeout",
            claude_session_id=session_id,
        )

    async def _exec_and_wait(
        self,
        cmd: list[str],
        *,
        cwd: str | None,
        log_path: Path,
        prompt: str,
    ) -> ExecutionResult:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(f"$ {' '.join(cmd)}\n\n", encoding="utf-8")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
            env=os.environ.copy(),
        )

        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return ExecutionResult(
                summary="Claude agent timed out",
                output="",
                success=False,
                error=f"Exceeded {self.timeout_seconds}s timeout",
            )

        output = stdout.decode("utf-8", errors="replace")
        with log_path.open("a", encoding="utf-8") as f:
            f.write(output)

        success = proc.returncode == 0
        summary = extract_summary(output, prompt) if success else "Claude agent failed"
        error = None if success else (output[-500:] or f"exit code {proc.returncode}")

        return ExecutionResult(
            summary=summary,
            output=output,
            success=success,
            error=error,
        )

    @staticmethod
    def _find_new_session(before: set[str | None], after: list[dict]) -> str | None:
        new_sessions = [a for a in after if a.get("sessionId") not in before]
        if not new_sessions:
            return None
        new_sessions.sort(key=lambda a: a.get("startedAt", 0), reverse=True)
        return new_sessions[0].get("sessionId")


class MockAgentExecutor(AgentExecutor):
    async def run(
        self,
        *,
        prompt: str,
        repo_path: Path | None,
        log_path: Path,
        title: str | None = None,
        remote: bool = False,
    ) -> ExecutionResult:
        await asyncio.sleep(0.3)
        iteration = 1
        match = re.search(r"(\d{3})-work", str(log_path))
        if match:
            iteration = int(match.group(1))
        if "review" in str(log_path).lower():
            summary = "REVIEW: APPROVED\n- Mock reviewer passes"
        else:
            summary = f"Mock iteration {iteration}: {prompt[:60]}"
        output = summary
        if iteration >= 2 and "review" not in str(log_path).lower():
            output += f"\n{COMPLETION_SIGNAL_DEFAULT}"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(output, encoding="utf-8")
        return ExecutionResult(
            summary=summary,
            output=output,
            success=True,
            claude_session_id="mock-session-id",
        )


def default_executor() -> AgentExecutor:
    if os.environ.get("MINIFLEET_MOCK", "").lower() in ("1", "true", "yes"):
        return MockAgentExecutor()
    return ClaudeCodeExecutor()
