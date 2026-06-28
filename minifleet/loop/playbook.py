"""Playbook loader — reusable loop skills."""

from __future__ import annotations

from pathlib import Path

import minifleet

BUILTIN_DEFAULT = """# Default MiniFleet Playbook

## Read → Work → Verify → Write

1. **Read state** — Open `state.md` and `checklist.md` in the job workspace
2. **Do work** — Complete exactly one focused unit of work (one bug, one test file, one refactor)
3. **Verify** — Run tests/lint if applicable; ensure changes are coherent
4. **Write state** — Update `state.md` with what you did and what remains

## Rules
- One iteration = one unit of work. Do not try to finish everything in one pass unless trivial.
- Prefer small, verifiable commits to the filesystem state.
- If blocked, document the blocker in state.md and pick a different sub-task.
- Never delete progress from state.md — append and update checkboxes.
"""

BUILTIN_REVIEWER = """# Reviewer Playbook

You are the **reviewer** agent. The worker agent just completed an iteration.

## Your job
1. Read the worker's changes in the repo (git diff if available)
2. Read `{job_path}/state.md` for claimed progress
3. Critique: missing tests, sloppy code, incomplete work, hallucinated completion

## Output format (strict)
First line must be exactly one of:
- `REVIEW: APPROVED` — work is acceptable to proceed
- `REVIEW: REJECTED` — worker must fix issues next iteration

Then bullet points explaining your decision (max 8 bullets).
"""


def _search_paths(name: str, repo_path: Path | None) -> list[Path]:
    pkg_dir = Path(minifleet.__file__).resolve().parent / "playbooks"
    paths: list[Path] = [pkg_dir / f"{name}.md"]
    if repo_path:
        paths.append(repo_path / ".minifleet" / "playbooks" / f"{name}.md")
    paths.append(Path.home() / ".minifleet" / "playbooks" / f"{name}.md")
    return paths


def load_playbook(name: str | None, repo_path: Path | None) -> str:
    if not name or name == "default":
        for path in _search_paths("default", repo_path):
            if path.exists():
                return path.read_text(encoding="utf-8")
        return BUILTIN_DEFAULT

    for path in _search_paths(name, repo_path):
        if path.exists():
            return path.read_text(encoding="utf-8")

    if name == "reviewer":
        return BUILTIN_REVIEWER
    return BUILTIN_DEFAULT


def build_reviewer_prompt(
    *,
    job_path: Path,
    repo_path: Path | None,
    iteration: int,
    worker_summary: str,
) -> str:
    playbook = load_playbook("reviewer", repo_path)
    repo_hint = f"Repository: `{repo_path}`\n" if repo_path else ""
    return f"""{playbook}

{repo_hint}
Job workspace: `{job_path}`
Iteration: {iteration}

## Worker summary
{worker_summary}

Review now. Output REVIEW: APPROVED or REVIEW: REJECTED on the first line.
"""
