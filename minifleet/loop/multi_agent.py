"""Multi-agent critique loop (worker + reviewer)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from minifleet.loop.playbook import build_reviewer_prompt
from minifleet.worker.executor import AgentExecutor, ExecutionResult


@dataclass
class ReviewResult:
    approved: bool
    summary: str
    output: str


def parse_review(output: str) -> ReviewResult:
    first_line = output.strip().split("\n")[0].upper()
    approved = "APPROVED" in first_line and "REJECTED" not in first_line
    summary_match = re.search(r"REVIEW:\s*(APPROVED|REJECTED)", output, re.I)
    summary = summary_match.group(0) if summary_match else ("Approved" if approved else "Rejected")
    return ReviewResult(approved=approved, summary=summary, output=output)


async def run_reviewer(
    executor: AgentExecutor,
    *,
    job_path: Path,
    repo_path: Path | None,
    iteration: int,
    worker_summary: str,
    log_path: Path,
) -> ReviewResult:
    prompt = build_reviewer_prompt(
        job_path=job_path,
        repo_path=repo_path,
        iteration=iteration,
        worker_summary=worker_summary,
    )
    result: ExecutionResult = await executor.run(
        prompt=prompt,
        repo_path=repo_path,
        log_path=log_path,
        title=f"Reviewer iteration {iteration}",
    )
    return parse_review(result.output or result.summary)
