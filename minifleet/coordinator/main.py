import asyncio
import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from minifleet.db import Database
from minifleet.models import AgentCreate, AgentStatus, AgentUpdate, NodeHeartbeat, NodeRegister, NodeRepoStatusReport, RepoCreate
from minifleet.loop.config import LoopConfig
from minifleet.notifications import notify_agent_event

DATA_DIR = Path(os.environ.get("MINIFLEET_DATA", Path.home() / ".minifleet"))
DB_PATH = DATA_DIR / "fleet.db"
LOGS_DIR = DATA_DIR / "logs"
DASHBOARD_DIR = Path(__file__).resolve().parent.parent.parent / "dashboard"

db = Database(DB_PATH)
app = FastAPI(title="MiniFleet", version="0.2.0")


class LogAppend(BaseModel):
    chunk: str
    offset: int = 0


@app.on_event("startup")
def startup() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def _notify_if_terminal(agent: dict, previous_status: str | None = None) -> None:
    status = agent.get("status")
    if status in ("completed", "failed", "cancelled") and status != previous_status:
        notify_agent_event(event=status, agent=agent, node_name=agent.get("node_name"))


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/nodes/register")
def register_node(payload: NodeRegister, request: Request) -> dict:
    ip = request.client.host if request.client else None
    node = db.upsert_node(
        name=payload.name,
        hostname=payload.hostname,
        max_concurrent=payload.max_concurrent,
        device_type=payload.device_type,
        ip=ip,
    )
    return node.model_dump(mode="json")


@app.post("/api/nodes/{node_id}/heartbeat")
def heartbeat(node_id: str, request: Request, payload: NodeHeartbeat | None = None) -> dict:
    node = db.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    ip = request.client.host if request.client else None
    health = payload or NodeHeartbeat()
    db.heartbeat_node(
        node_id,
        ip=ip,
        cpu_percent=health.cpu_percent,
        memory_percent=health.memory_percent,
        disk_percent=health.disk_percent,
        claude_version=health.claude_version,
    )
    return {"ok": True}


@app.get("/api/nodes")
def list_nodes() -> list[dict]:
    db.mark_stale_nodes_offline()
    return [n.model_dump(mode="json") for n in db.list_nodes()]


@app.post("/api/agents")
def create_agent(payload: AgentCreate) -> dict:
    node_id = payload.node_id
    if payload.node_name and not node_id:
        node = db.get_node_by_name(payload.node_name)
        if not node:
            raise HTTPException(status_code=404, detail=f"Node '{payload.node_name}' not found")
        node_id = node.id

    if payload.repo and not db.get_repo_by_name(payload.repo):
        raise HTTPException(status_code=404, detail=f"Repo '{payload.repo}' not registered")

    title = payload.title or payload.prompt[:80].strip()
    if len(payload.prompt) > 80 and not payload.title:
        title += "…"

    loop_config = None
    if payload.loop_config:
        loop_config = payload.loop_config.model_dump()
    elif payload.loop:
        loop_config = LoopConfig().model_dump()

    agent = db.create_agent(
        prompt=payload.prompt,
        title=title,
        node_id=node_id,
        repo=payload.repo,
        repo_path=payload.repo_path,
        remote=payload.remote,
        loop=payload.loop,
        loop_config=loop_config,
    )
    return agent.model_dump(mode="json")


@app.get("/api/agents")
def list_agents(node_id: str | None = None, status: str | None = None) -> list[dict]:
    agent_status = AgentStatus(status) if status else None
    agents = db.list_agents(node_id=node_id, status=agent_status, limit=200)
    return [a.model_dump(mode="json") for a in agents]


@app.get("/api/agents/{agent_id}")
def get_agent(agent_id: str) -> dict:
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent.model_dump(mode="json")


@app.post("/api/agents/{agent_id}/cancel")
def cancel_agent(agent_id: str) -> dict:
    existing = db.get_agent(agent_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Agent not found")
    previous = existing.status.value
    agent = db.cancel_agent(agent_id)
    assert agent is not None
    data = agent.model_dump(mode="json")
    _notify_if_terminal(data, previous)
    return data


@app.post("/api/agents/{agent_id}/logs")
def append_agent_log(agent_id: str, payload: LogAppend) -> dict:
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    size = db.append_agent_log(agent_id, payload.chunk, LOGS_DIR)
    return {"ok": True, "size": size}


@app.get("/api/agents/{agent_id}/logs")
def get_agent_logs(agent_id: str, offset: int = 0) -> dict:
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    content, start, size = db.read_agent_log(agent_id, LOGS_DIR, offset)
    return {"content": content, "offset": start + len(content), "size": size}


@app.get("/api/agents/{agent_id}/logs/stream")
async def stream_agent_logs(agent_id: str, offset: int = 0) -> StreamingResponse:
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    async def event_stream():
        pos = offset
        while True:
            content, start, size = db.read_agent_log(agent_id, LOGS_DIR, pos)
            if content:
                pos = start + len(content)
                payload = json.dumps({"content": content, "offset": pos, "size": size})
                yield f"data: {payload}\n\n"
            agent_row = db.get_agent(agent_id)
            terminal = agent_row and agent_row.status.value in (
                "completed",
                "failed",
                "cancelled",
            )
            if terminal:
                yield "data: {\"done\": true}\n\n"
                break
            await asyncio.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/nodes/{node_id}/claim")
def claim_agent(node_id: str) -> dict:
    agent = db.claim_next_agent(node_id)
    if not agent:
        return JSONResponse({"agent": None})
    return {"agent": agent.model_dump(mode="json")}


@app.patch("/api/agents/{agent_id}")
def update_agent(agent_id: str, payload: AgentUpdate) -> dict:
    existing = db.get_agent(agent_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Agent not found")
    previous = existing.status.value

    if payload.status == AgentStatus.CANCELLED or (
        existing.cancel_requested and payload.status in (AgentStatus.FAILED, AgentStatus.CANCELLED, None)
    ):
        if existing.status == AgentStatus.RUNNING and payload.status is None:
            payload.status = AgentStatus.CANCELLED

    agent = db.update_agent(
        agent_id,
        status=payload.status,
        summary=payload.summary,
        error=payload.error,
        claude_session_id=payload.claude_session_id,
        iteration=payload.iteration,
        max_iterations=payload.max_iterations,
        loop_phase=payload.loop_phase,
        estimated_cost_usd=payload.estimated_cost_usd,
        cancel_requested=payload.cancel_requested,
        git_branch=payload.git_branch,
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    data = agent.model_dump(mode="json")
    _notify_if_terminal(data, previous)
    return data


@app.get("/api/dashboard")
def dashboard() -> dict:
    return db.dashboard_stats()


@app.get("/api/repos")
def list_repos() -> list[dict]:
    return [r.model_dump(mode="json") for r in db.list_repos()]


@app.post("/api/repos")
def add_repo(payload: RepoCreate) -> dict:
    if db.get_repo_by_name(payload.name):
        raise HTTPException(status_code=409, detail=f"Repo '{payload.name}' already exists")
    repo = db.create_repo(name=payload.name, url=payload.url, branch=payload.branch)
    return repo.model_dump(mode="json")


@app.delete("/api/repos/{name}")
def remove_repo(name: str) -> dict:
    if not db.delete_repo(name):
        raise HTTPException(status_code=404, detail="Repo not found")
    return {"ok": True}


@app.post("/api/nodes/{node_id}/repos/status")
def report_repo_status(node_id: str, payload: NodeRepoStatusReport) -> dict:
    node = db.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    db.upsert_node_repo_status(
        node_id,
        github_ok=payload.github_ok,
        github_method=payload.github_method,
        repos=[r.model_dump() for r in payload.repos],
    )
    return {"ok": True}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(DASHBOARD_DIR / "index.html")


if DASHBOARD_DIR.exists():
    app.mount("/static", StaticFiles(directory=DASHBOARD_DIR), name="static")


def main() -> None:
    import uvicorn

    host = os.environ.get("MINIFLEET_HOST", "0.0.0.0")
    port = int(os.environ.get("MINIFLEET_PORT", "8787"))
    uvicorn.run("minifleet.coordinator.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
