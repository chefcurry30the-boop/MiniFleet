"""Playbook loop runner — Phases 1–4 orchestration."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from minifleet.loop.config import COMPLETION_SIGNAL_DEFAULT, LoopConfig
from minifleet.loop.jit import build_iteration_prompt
from minifleet.loop.multi_agent import run_reviewer
from minifleet.loop.state import (
    append_iteration_log,
    init_job_workspace,
    job_dir,
    read_last_verify,
    write_verify_result,
)
from minifleet.loop.verify import run_verify
from minifleet.worker.executor import AgentExecutor, ExecutionResult


@dataclass
class LoopResult:
    success: bool
    summary: str
    iterations: int
    estimated_cost_usd: float
    error: str | None = None
    stop_reason: str = "completed"


class LoopRunner:
    def __init__(
        self,
        *,
        executor: AgentExecutor,
        data_dir: Path,
        coordinator_url: str,
    ):
        self.executor = executor
        self.data_dir = data_dir
        self.coordinator_url = coordinator_url.rstrip("/")

    async def report(
        self,
        client: httpx.AsyncClient,
        agent_id: str,
        *,
        iteration: int,
        loop_phase: str,
        summary: str,
        estimated_cost_usd: float,
        max_iterations: int,
    ) -> None:
        try:
            await client.patch(
                f"{self.coordinator_url}/api/agents/{agent_id}",
                json={
                    "iteration": iteration,
                    "loop_phase": loop_phase,
                    "summary": summary,
                    "estimated_cost_usd": estimated_cost_usd,
                    "max_iterations": max_iterations,
                },
            )
        except httpx.HTTPError:
            pass

    async def run(
        self,
        client: httpx.AsyncClient,
        agent: dict,
        repo_path: Path | None,
    ) -> LoopResult:
        agent_id = agent["id"]
        objective = agent["prompt"]
        title = agent.get("title", objective[:60])
        config = self._parse_config(agent)
        path = job_dir(self.data_dir, agent_id)
        init_job_workspace(path, prompt=objective, title=title)

        consecutive_signals = 0
        estimated_cost = 0.0
        start = time.monotonic()
        last_summary = "Loop started"
        stop_reason = "max_iterations"
        last_iteration = 0

        for iteration in range(1, config.max_iterations + 1):
            last_iteration = iteration
            elapsed = time.monotonic() - start
            if elapsed > config.max_duration_seconds:
                stop_reason = "timeout"
                break
            if config.cost_cap_usd and estimated_cost >= config.cost_cap_usd:
                stop_reason = "cost_cap"
                break

            # READ
            await self.report(
                client,
                agent_id,
                iteration=iteration,
                loop_phase="read",
                summary=f"Iteration {iteration}/{config.max_iterations} — reading state",
                estimated_cost_usd=estimated_cost,
                max_iterations=config.max_iterations,
            )

            _, last_verify_out = read_last_verify(path)
            verify_ok_prev, _ = read_last_verify(path)

            # WORK (JIT prompt)
            await self.report(
                client,
                agent_id,
                iteration=iteration,
                loop_phase="work",
                summary=f"Iteration {iteration} — worker agent running",
                estimated_cost_usd=estimated_cost,
                max_iterations=config.max_iterations,
            )

            work_prompt = build_iteration_prompt(
                objective=objective,
                job_path=path,
                repo_path=repo_path,
                iteration=iteration,
                max_iterations=config.max_iterations,
                playbook_name=config.playbook,
                verify_output=last_verify_out if last_verify_out else None,
                verify_ok=verify_ok_prev,
                completion_signal=config.completion_signal,
                multi_agent=config.multi_agent,
            )

            work_log = path / "iterations" / f"{iteration:03d}-work.log"
            work_result: ExecutionResult = await self.executor.run(
                prompt=work_prompt,
                repo_path=repo_path,
                log_path=work_log,
                title=f"{title} iter {iteration}",
                remote=bool(agent.get("remote")),
            )
            estimated_cost += config.cost_per_iteration_usd
            last_summary = work_result.summary or f"Iteration {iteration} work done"

            if not work_result.success:
                append_iteration_log(path, iteration, f"Work failed: {work_result.error}")
                return LoopResult(
                    success=False,
                    summary=last_summary,
                    iterations=iteration,
                    estimated_cost_usd=estimated_cost,
                    error=work_result.error,
                    stop_reason="work_failed",
                )

            # MULTI-AGENT REVIEW (Phase 4)
            if config.multi_agent:
                await self.report(
                    client,
                    agent_id,
                    iteration=iteration,
                    loop_phase="review",
                    summary=f"Iteration {iteration} — reviewer agent",
                    estimated_cost_usd=estimated_cost,
                    max_iterations=config.max_iterations,
                )
                review_log = path / "iterations" / f"{iteration:03d}-review.log"
                review = await run_reviewer(
                    self.executor,
                    job_path=path,
                    repo_path=repo_path,
                    iteration=iteration,
                    worker_summary=work_result.summary,
                    log_path=review_log,
                )
                estimated_cost += config.cost_per_iteration_usd
                append_iteration_log(
                    path,
                    iteration,
                    f"Reviewer: {review.summary}",
                )
                if not review.approved:
                    consecutive_signals = 0
                    await self.report(
                        client,
                        agent_id,
                        iteration=iteration,
                        loop_phase="write",
                        summary=f"Iteration {iteration} — rejected by reviewer, continuing",
                        estimated_cost_usd=estimated_cost,
                        max_iterations=config.max_iterations,
                    )
                    continue

            # VERIFY
            await self.report(
                client,
                agent_id,
                iteration=iteration,
                loop_phase="verify",
                summary=f"Iteration {iteration} — running verification",
                estimated_cost_usd=estimated_cost,
                max_iterations=config.max_iterations,
            )
            verify_ok, verify_out = await run_verify(config.verify_command, repo_path)
            write_verify_result(path, iteration, verify_out, verify_ok)

            # Completion signal check (Phase 3)
            signal = config.completion_signal
            if signal in (work_result.output or ""):
                consecutive_signals += 1
            else:
                consecutive_signals = 0

            append_iteration_log(
                path,
                iteration,
                f"Work OK. Verify: {'pass' if verify_ok else 'fail'}. "
                f"Completion signals: {consecutive_signals}/{config.completion_threshold}",
            )

            # WRITE
            await self.report(
                client,
                agent_id,
                iteration=iteration,
                loop_phase="write",
                summary=last_summary,
                estimated_cost_usd=estimated_cost,
                max_iterations=config.max_iterations,
            )

            if (
                consecutive_signals >= config.completion_threshold
                and verify_ok
            ):
                stop_reason = "completed"
                return LoopResult(
                    success=True,
                    summary=last_summary,
                    iterations=iteration,
                    estimated_cost_usd=estimated_cost,
                    stop_reason=stop_reason,
                )

        final_ok = stop_reason == "completed"
        return LoopResult(
            success=final_ok,
            summary=last_summary if final_ok else f"Stopped ({stop_reason}) after {last_iteration} iterations",
            iterations=last_iteration,
            estimated_cost_usd=estimated_cost,
            error=None if final_ok else f"Loop ended: {stop_reason}",
            stop_reason=stop_reason,
        )

    @staticmethod
    def _parse_config(agent: dict) -> LoopConfig:
        raw = agent.get("loop_config")
        if isinstance(raw, dict):
            return LoopConfig(**raw)
        if isinstance(raw, str):
            import json

            return LoopConfig(**json.loads(raw))
        return LoopConfig()
