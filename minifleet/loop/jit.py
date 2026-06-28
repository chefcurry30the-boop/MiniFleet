"""Just-in-Time context builder (Shopify Sidekick pattern)."""

from __future__ import annotations

from pathlib import Path

from minifleet.loop.config import COMPLETION_SIGNAL_DEFAULT
from minifleet.loop.playbook import load_playbook
from minifleet.loop.state import read_checklist, read_state


def truncate(text: str, limit: int = 3000) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n…(truncated)…"


def build_iteration_prompt(
    *,
    objective: str,
    job_path: Path,
    repo_path: Path | None,
    iteration: int,
    max_iterations: int,
    playbook_name: str | None,
    verify_output: str | None,
    verify_ok: bool | None,
    completion_signal: str = COMPLETION_SIGNAL_DEFAULT,
    multi_agent: bool = False,
) -> str:
    """Build minimal per-iteration context — not a token more, not a token less."""

    state = read_state(job_path)
    checklist = read_checklist(job_path)
    playbook = load_playbook(playbook_name, repo_path)

    verify_section = ""
    if verify_output:
        status = "PASSED" if verify_ok else "FAILED"
        verify_section = f"""
## Last verification ({status})
```text
{truncate(verify_output, 2000)}
```
Fix failures before signaling completion.
"""

    repo_hint = f"Working directory: `{repo_path}`\n" if repo_path else ""

    return f"""You are iteration {iteration} of {max_iterations} in a MiniFleet autonomous loop.

{repo_hint}
## Playbook (follow this process)
{truncate(playbook, 2500)}

## Current state (read carefully, update state.md when done)
{truncate(state, 3500)}

## Checklist
{truncate(checklist, 1500)}

## Your objective
{objective}
{verify_section}
## Instructions
1. READ state.md and checklist.md in the job workspace: `{job_path}`
2. Do ONE focused unit of work toward the objective
3. UPDATE `{job_path}/state.md` — mark progress, next steps, iteration notes
4. UPDATE `{job_path}/checklist.md` — check off completed items
5. If ALL objectives are met AND verification would pass, output exactly: `{completion_signal}`

Do not output `{completion_signal}` until the task is genuinely complete.
{"After your work, a reviewer agent will critique changes before the next iteration." if multi_agent else ""}
"""
