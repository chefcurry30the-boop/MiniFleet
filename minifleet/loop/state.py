"""Filesystem state for loop iterations (Ralph loop pattern)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


DEFAULT_CHECKLIST = """# Verification checklist

- [ ] Task objectives addressed
- [ ] Tests pass (if applicable)
- [ ] No obvious regressions introduced
"""


def job_dir(data_dir: Path, agent_id: str) -> Path:
    return data_dir / "jobs" / agent_id


def init_job_workspace(job_path: Path, *, prompt: str, title: str) -> None:
    job_path.mkdir(parents=True, exist_ok=True)
    (job_path / "iterations").mkdir(exist_ok=True)

    state_path = job_path / "state.md"
    if not state_path.exists():
        state_path.write_text(
            f"""# Task state

**Title:** {title}

## Original objective
{prompt}

## Progress
- [ ] Not started

## Next steps
- Read the objective and begin the first unit of work

## Iteration log
""",
            encoding="utf-8",
        )

    checklist_path = job_path / "checklist.md"
    if not checklist_path.exists():
        checklist_path.write_text(DEFAULT_CHECKLIST, encoding="utf-8")

    prompt_path = job_path / "prompt.md"
    prompt_path.write_text(prompt, encoding="utf-8")


def read_state(job_path: Path) -> str:
    path = job_path / "state.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def read_checklist(job_path: Path) -> str:
    path = job_path / "checklist.md"
    return path.read_text(encoding="utf-8") if path.exists() else DEFAULT_CHECKLIST


def append_iteration_log(job_path: Path, iteration: int, note: str) -> None:
    state_path = job_path / "state.md"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    entry = f"\n### Iteration {iteration} ({ts})\n{note}\n"
    with state_path.open("a", encoding="utf-8") as f:
        f.write(entry)


def write_verify_result(job_path: Path, iteration: int, output: str, ok: bool) -> None:
    verify_dir = job_path / "iterations"
    verify_dir.mkdir(exist_ok=True)
    path = verify_dir / f"{iteration:03d}-verify.txt"
    path.write_text(f"{'PASS' if ok else 'FAIL'}\n\n{output}", encoding="utf-8")


def read_last_verify(job_path: Path) -> tuple[bool | None, str]:
    verify_dir = job_path / "iterations"
    if not verify_dir.exists():
        return None, ""
    files = sorted(verify_dir.glob("*-verify.txt"), reverse=True)
    if not files:
        return None, ""
    text = files[0].read_text(encoding="utf-8", errors="replace")
    ok = text.startswith("PASS")
    return ok, text
