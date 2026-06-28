from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from minifleet.loop.config import COMPLETION_SIGNAL_DEFAULT, LoopConfig


class AgentStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NodeStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"


class NodeRegister(BaseModel):
    name: str = Field(description="Human label, e.g. mac-mini-1 or macbook-pro")
    hostname: str
    device_type: str = Field(default="mac", description="macbook, mac-mini, mac-studio, imac, mac-pro, mac")
    max_concurrent: int = Field(
        default=0,
        description="Max concurrent agents (0 = unlimited)",
    )


class Node(BaseModel):
    id: str
    name: str
    hostname: str
    device_type: str = "mac"
    max_concurrent: int
    status: NodeStatus
    last_seen: datetime
    ip: Optional[str] = None


class AgentCreate(BaseModel):
    prompt: str
    node_id: Optional[str] = None
    node_name: Optional[str] = None
    repo: Optional[str] = Field(default=None, description="Registered repo alias, e.g. my-app")
    repo_path: Optional[str] = None
    title: Optional[str] = None
    remote: bool = Field(default=False)
    loop: bool = Field(default=True, description="Run as autonomous playbook loop (default on)")
    loop_config: Optional[LoopConfig] = None


class Agent(BaseModel):
    id: str
    node_id: Optional[str]
    node_name: Optional[str]
    title: str
    prompt: str
    repo: Optional[str] = None
    repo_path: Optional[str]
    status: AgentStatus
    summary: Optional[str] = None
    remote: bool = False
    claude_session_id: Optional[str] = None
    loop: bool = True
    loop_config: Optional[dict[str, Any]] = None
    iteration: int = 0
    max_iterations: int = 0
    loop_phase: Optional[str] = None
    estimated_cost_usd: float = 0.0
    multi_agent: bool = False
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None


class AgentUpdate(BaseModel):
    status: Optional[AgentStatus] = None
    summary: Optional[str] = None
    error: Optional[str] = None
    claude_session_id: Optional[str] = None
    iteration: Optional[int] = None
    max_iterations: Optional[int] = None
    loop_phase: Optional[str] = None
    estimated_cost_usd: Optional[float] = None


class NodeDashboard(BaseModel):
    node: Node
    running: int
    queued: int
    completed: int
    failed: int
    agents: list[Agent]


class FleetDashboard(BaseModel):
    nodes: list[NodeDashboard]
    totals: dict[str, int]


class RepoCreate(BaseModel):
    name: str = Field(description="Short alias, e.g. my-app")
    url: str = Field(description="git@github.com:org/repo.git or https://github.com/org/repo")
    branch: str = "main"


class Repo(BaseModel):
    id: str
    name: str
    url: str
    branch: str
    slug: Optional[str] = None
    created_at: datetime


class NodeRepoStatus(BaseModel):
    name: str
    ok: bool
    path: Optional[str] = None
    branch: Optional[str] = None
    commit: Optional[str] = None
    error: Optional[str] = None
    synced_at: Optional[datetime] = None


class NodeRepoStatusReport(BaseModel):
    github_ok: bool
    github_method: Optional[str] = None
    repos: list[NodeRepoStatus]
