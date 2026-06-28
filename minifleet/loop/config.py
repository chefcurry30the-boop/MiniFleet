from __future__ import annotations

from pydantic import BaseModel, Field


COMPLETION_SIGNAL_DEFAULT = "MINIFLEET_TASK_COMPLETE"


class LoopConfig(BaseModel):
    """Guardrails and behaviour for playbook loops."""

    enabled: bool = True
    max_iterations: int = Field(default=20, ge=1, le=500)
    max_duration_seconds: int = Field(default=7200, ge=60)
    completion_signal: str = COMPLETION_SIGNAL_DEFAULT
    completion_threshold: int = Field(default=2, ge=1, description="Consecutive completion signals required")
    verify_command: str | None = None
    playbook: str | None = Field(default="default", description="Playbook name in .minifleet/playbooks/")
    multi_agent: bool = Field(default=False, description="Run reviewer agent after each work iteration")
    cost_cap_usd: float | None = Field(default=None, description="Stop loop when estimated cost exceeds cap")
    cost_per_iteration_usd: float = Field(default=0.15, description="Estimated cost per iteration for tracking")
    git_push: bool = Field(default=False, description="Auto branch, commit, and push on success")
    git_branch_prefix: str = Field(default="minifleet", description="Branch prefix for git_push")
