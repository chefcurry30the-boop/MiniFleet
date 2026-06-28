import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from minifleet.github import parse_repo_slug
from minifleet.models import Agent, AgentStatus, Node, NodeRepoStatus, NodeStatus, Repo


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value)


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    hostname TEXT NOT NULL,
                    max_concurrent INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'offline',
                    last_seen TEXT,
                    ip TEXT
                );

                CREATE TABLE IF NOT EXISTS agents (
                    id TEXT PRIMARY KEY,
                    node_id TEXT,
                    title TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    repo_path TEXT,
                    status TEXT NOT NULL DEFAULT 'queued',
                    summary TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    FOREIGN KEY (node_id) REFERENCES nodes(id)
                );

                CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);
                CREATE INDEX IF NOT EXISTS idx_agents_node ON agents(node_id);

                CREATE TABLE IF NOT EXISTS repos (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    url TEXT NOT NULL,
                    branch TEXT NOT NULL DEFAULT 'main',
                    slug TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS node_repo_status (
                    node_id TEXT NOT NULL,
                    repo_name TEXT NOT NULL,
                    ok INTEGER NOT NULL DEFAULT 0,
                    path TEXT,
                    branch TEXT,
                    "commit" TEXT,
                    error TEXT,
                    synced_at TEXT,
                    PRIMARY KEY (node_id, repo_name),
                    FOREIGN KEY (node_id) REFERENCES nodes(id)
                );

                CREATE TABLE IF NOT EXISTS node_github (
                    node_id TEXT PRIMARY KEY,
                    github_ok INTEGER NOT NULL DEFAULT 0,
                    github_method TEXT,
                    updated_at TEXT,
                    FOREIGN KEY (node_id) REFERENCES nodes(id)
                );
                """
            )
            self._migrate_schema(conn)

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(agents)").fetchall()}
        if "remote" not in columns:
            conn.execute("ALTER TABLE agents ADD COLUMN remote INTEGER NOT NULL DEFAULT 0")
        if "claude_session_id" not in columns:
            conn.execute("ALTER TABLE agents ADD COLUMN claude_session_id TEXT")
        if "repo" not in columns:
            conn.execute("ALTER TABLE agents ADD COLUMN repo TEXT")
        if "loop" not in columns:
            conn.execute("ALTER TABLE agents ADD COLUMN loop INTEGER NOT NULL DEFAULT 1")
        if "loop_config" not in columns:
            conn.execute("ALTER TABLE agents ADD COLUMN loop_config TEXT")
        if "iteration" not in columns:
            conn.execute("ALTER TABLE agents ADD COLUMN iteration INTEGER NOT NULL DEFAULT 0")
        if "max_iterations" not in columns:
            conn.execute("ALTER TABLE agents ADD COLUMN max_iterations INTEGER NOT NULL DEFAULT 0")
        if "loop_phase" not in columns:
            conn.execute("ALTER TABLE agents ADD COLUMN loop_phase TEXT")
        if "estimated_cost_usd" not in columns:
            conn.execute("ALTER TABLE agents ADD COLUMN estimated_cost_usd REAL NOT NULL DEFAULT 0")
        if "multi_agent" not in columns:
            conn.execute("ALTER TABLE agents ADD COLUMN multi_agent INTEGER NOT NULL DEFAULT 0")

        node_columns = {row[1] for row in conn.execute("PRAGMA table_info(nodes)").fetchall()}
        if "remote_control" not in node_columns:
            conn.execute("ALTER TABLE nodes ADD COLUMN remote_control INTEGER NOT NULL DEFAULT 0")
        if "device_type" not in node_columns:
            conn.execute("ALTER TABLE nodes ADD COLUMN device_type TEXT NOT NULL DEFAULT 'mac'")

    def upsert_node(
        self,
        *,
        name: str,
        hostname: str,
        max_concurrent: int,
        device_type: str = "mac",
        ip: Optional[str] = None,
    ) -> Node:
        now = utcnow().isoformat()
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM nodes WHERE name = ?", (name,)).fetchone()
            if row:
                node_id = row["id"]
                conn.execute(
                    """
                    UPDATE nodes
                    SET hostname = ?, max_concurrent = ?, device_type = ?, status = 'online',
                        last_seen = ?, ip = COALESCE(?, ip)
                    WHERE id = ?
                    """,
                    (hostname, max_concurrent, device_type, now, ip, node_id),
                )
            else:
                node_id = str(uuid.uuid4())
                conn.execute(
                    """
                    INSERT INTO nodes (id, name, hostname, max_concurrent, device_type, status, last_seen, ip)
                    VALUES (?, ?, ?, ?, ?, 'online', ?, ?)
                    """,
                    (node_id, name, hostname, max_concurrent, device_type, now, ip),
                )
        node = self.get_node(node_id)
        assert node is not None
        return node

    def heartbeat_node(self, node_id: str, ip: Optional[str] = None) -> None:
        now = utcnow().isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE nodes
                SET last_seen = ?, status = 'online', ip = COALESCE(?, ip)
                WHERE id = ?
                """,
                (now, ip, node_id),
            )

    def mark_stale_nodes_offline(self, stale_seconds: int = 90) -> None:
        cutoff = utcnow().timestamp() - stale_seconds
        with self.connect() as conn:
            rows = conn.execute("SELECT id, last_seen FROM nodes").fetchall()
            for row in rows:
                last_seen = parse_dt(row["last_seen"])
                if not last_seen or last_seen.timestamp() < cutoff:
                    conn.execute(
                        "UPDATE nodes SET status = 'offline' WHERE id = ?",
                        (row["id"],),
                    )

    def list_nodes(self) -> list[Node]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM nodes ORDER BY name").fetchall()
        return [self._row_to_node(row) for row in rows]

    def get_node(self, node_id: str) -> Optional[Node]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        return self._row_to_node(row) if row else None

    def get_node_by_name(self, name: str) -> Optional[Node]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM nodes WHERE name = ?", (name,)).fetchone()
        return self._row_to_node(row) if row else None

    def create_agent(
        self,
        *,
        prompt: str,
        title: str,
        node_id: Optional[str] = None,
        repo: Optional[str] = None,
        repo_path: Optional[str] = None,
        remote: bool = False,
        loop: bool = True,
        loop_config: Optional[dict[str, Any]] = None,
    ) -> Agent:
        agent_id = str(uuid.uuid4())
        now = utcnow().isoformat()
        config_json = json.dumps(loop_config) if loop_config else None
        multi_agent = int(loop_config.get("multi_agent", False)) if loop_config else 0
        max_iter = int(loop_config.get("max_iterations", 20)) if loop_config else 20
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO agents (
                    id, node_id, title, prompt, repo, repo_path, status, created_at, remote,
                    loop, loop_config, max_iterations, multi_agent
                )
                VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_id,
                    node_id,
                    title,
                    prompt,
                    repo,
                    repo_path,
                    now,
                    int(remote),
                    int(loop),
                    config_json,
                    max_iter,
                    multi_agent,
                ),
            )
        agent = self.get_agent(agent_id)
        assert agent is not None
        return agent

    def create_repo(self, *, name: str, url: str, branch: str = "main") -> Repo:
        repo_id = str(uuid.uuid4())
        now = utcnow().isoformat()
        slug = parse_repo_slug(url)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO repos (id, name, url, branch, slug, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (repo_id, name, url, branch, slug, now),
            )
        repo = self.get_repo_by_name(name)
        assert repo is not None
        return repo

    def list_repos(self) -> list[Repo]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM repos ORDER BY name").fetchall()
        return [self._row_to_repo(row) for row in rows]

    def get_repo_by_name(self, name: str) -> Optional[Repo]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM repos WHERE name = ?", (name,)).fetchone()
        return self._row_to_repo(row) if row else None

    def delete_repo(self, name: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM repos WHERE name = ?", (name,))
        return cur.rowcount > 0

    def upsert_node_repo_status(
        self,
        node_id: str,
        *,
        github_ok: bool,
        github_method: Optional[str],
        repos: list[dict],
    ) -> None:
        now = utcnow().isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO node_github (node_id, github_ok, github_method, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    github_ok = excluded.github_ok,
                    github_method = excluded.github_method,
                    updated_at = excluded.updated_at
                """,
                (node_id, int(github_ok), github_method, now),
            )
            for item in repos:
                conn.execute(
                    """
                    INSERT INTO node_repo_status
                        (node_id, repo_name, ok, path, branch, "commit", error, synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(node_id, repo_name) DO UPDATE SET
                        ok = excluded.ok,
                        path = excluded.path,
                        branch = excluded.branch,
                        "commit" = excluded."commit",
                        error = excluded.error,
                        synced_at = excluded.synced_at
                    """,
                    (
                        node_id,
                        item["name"],
                        int(item.get("ok", False)),
                        item.get("path"),
                        item.get("branch"),
                        item.get("commit"),
                        item.get("error"),
                        now,
                    ),
                )

    def get_node_github(self, node_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM node_github WHERE node_id = ?",
                (node_id,),
            ).fetchone()
        if not row:
            return {"github_ok": False, "github_method": None, "updated_at": None}
        return {
            "github_ok": bool(row["github_ok"]),
            "github_method": row["github_method"],
            "updated_at": row["updated_at"],
        }

    def get_node_repo_statuses(self, node_id: str) -> list[NodeRepoStatus]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM node_repo_status
                WHERE node_id = ?
                ORDER BY repo_name
                """,
                (node_id,),
            ).fetchall()
        return [self._row_to_node_repo_status(row) for row in rows]

    def get_agent(self, agent_id: str) -> Optional[Agent]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT a.*, n.name AS node_name
                FROM agents a
                LEFT JOIN nodes n ON n.id = a.node_id
                WHERE a.id = ?
                """,
                (agent_id,),
            ).fetchone()
        return self._row_to_agent(row) if row else None

    def list_agents(
        self,
        *,
        node_id: Optional[str] = None,
        status: Optional[AgentStatus] = None,
        limit: int = 100,
    ) -> list[Agent]:
        query = """
            SELECT a.*, n.name AS node_name
            FROM agents a
            LEFT JOIN nodes n ON n.id = a.node_id
            WHERE 1=1
        """
        params: list[object] = []
        if node_id:
            query += " AND a.node_id = ?"
            params.append(node_id)
        if status:
            query += " AND a.status = ?"
            params.append(status.value)
        query += " ORDER BY a.created_at DESC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_agent(row) for row in rows]

    def count_running_on_node(self, node_id: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS c FROM agents
                WHERE node_id = ? AND status = 'running'
                """,
                (node_id,),
            ).fetchone()
        return int(row["c"])

    def claim_next_agent(self, node_id: str) -> Optional[Agent]:
        node = self.get_node(node_id)
        if not node or node.status != NodeStatus.ONLINE:
            return None

        running = self.count_running_on_node(node_id)
        if node.max_concurrent > 0 and running >= node.max_concurrent:
            return None

        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT a.*, n.name AS node_name
                FROM agents a
                LEFT JOIN nodes n ON n.id = a.node_id
                WHERE a.status = 'queued'
                  AND (a.node_id IS NULL OR a.node_id = ?)
                ORDER BY a.created_at ASC
                LIMIT 1
                """,
                (node_id,),
            ).fetchone()
            if not row:
                return None

            now = utcnow().isoformat()
            conn.execute(
                """
                UPDATE agents
                SET status = 'running', node_id = ?, started_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (node_id, now, row["id"]),
            )
        return self.get_agent(row["id"])

    def update_agent(
        self,
        agent_id: str,
        *,
        status: Optional[AgentStatus] = None,
        summary: Optional[str] = None,
        error: Optional[str] = None,
        claude_session_id: Optional[str] = None,
        iteration: Optional[int] = None,
        max_iterations: Optional[int] = None,
        loop_phase: Optional[str] = None,
        estimated_cost_usd: Optional[float] = None,
    ) -> Optional[Agent]:
        now = utcnow().isoformat()
        with self.connect() as conn:
            fields: list[str] = []
            params: list[object] = []
            if status is not None:
                fields.append("status = ?")
                params.append(status.value)
                if status in (AgentStatus.COMPLETED, AgentStatus.FAILED, AgentStatus.CANCELLED):
                    fields.append("completed_at = ?")
                    params.append(now)
            if summary is not None:
                fields.append("summary = ?")
                params.append(summary)
            if error is not None:
                fields.append("error = ?")
                params.append(error)
            if claude_session_id is not None:
                fields.append("claude_session_id = ?")
                params.append(claude_session_id)
            if iteration is not None:
                fields.append("iteration = ?")
                params.append(iteration)
            if max_iterations is not None:
                fields.append("max_iterations = ?")
                params.append(max_iterations)
            if loop_phase is not None:
                fields.append("loop_phase = ?")
                params.append(loop_phase)
            if estimated_cost_usd is not None:
                fields.append("estimated_cost_usd = ?")
                params.append(estimated_cost_usd)
            if not fields:
                return self.get_agent(agent_id)
            params.append(agent_id)
            conn.execute(
                f"UPDATE agents SET {', '.join(fields)} WHERE id = ?",
                params,
            )
        return self.get_agent(agent_id)

    def dashboard_stats(self) -> dict:
        self.mark_stale_nodes_offline()
        nodes = self.list_nodes()
        totals = {"running": 0, "queued": 0, "completed": 0, "failed": 0}
        node_dashboards = []

        for node in nodes:
            agents = self.list_agents(node_id=node.id, limit=50)
            counts = {"running": 0, "queued": 0, "completed": 0, "failed": 0}
            for agent in agents:
                if agent.status.value in counts:
                    counts[agent.status.value] += 1
                    totals[agent.status.value] += 1
            github = self.get_node_github(node.id)
            node_dashboards.append(
                {
                    "node": node.model_dump(mode="json"),
                    "github": github,
                    "repos": [
                        s.model_dump(mode="json")
                        for s in self.get_node_repo_statuses(node.id)
                    ],
                    "running": counts["running"],
                    "queued": counts["queued"],
                    "completed": counts["completed"],
                    "failed": counts["failed"],
                    "agents": [a.model_dump(mode="json") for a in agents[:20]],
                }
            )

        unassigned = self.list_unassigned_agents(limit=50)
        if unassigned:
            counts = {"running": 0, "queued": 0, "completed": 0, "failed": 0}
            for agent in unassigned:
                if agent.status.value in counts:
                    counts[agent.status.value] += 1
                    totals[agent.status.value] += 1
            node_dashboards.append(
                {
                    "node": {
                        "id": "unassigned",
                        "name": "unassigned",
                        "hostname": "—",
                        "max_concurrent": 0,
                        "status": "online",
                        "last_seen": utcnow().isoformat(),
                        "ip": None,
                    },
                    "running": counts["running"],
                    "queued": counts["queued"],
                    "completed": counts["completed"],
                    "failed": counts["failed"],
                    "agents": [a.model_dump(mode="json") for a in unassigned[:20]],
                }
            )

        return {"nodes": node_dashboards, "totals": totals, "repos": [r.model_dump(mode="json") for r in self.list_repos()]}

    def list_unassigned_agents(self, limit: int = 100) -> list[Agent]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT a.*, NULL AS node_name
                FROM agents a
                WHERE a.node_id IS NULL
                ORDER BY a.created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_agent(row) for row in rows]

    def export_state(self) -> str:
        return json.dumps(self.dashboard_stats(), indent=2)

    @staticmethod
    def _row_to_node(row: sqlite3.Row) -> Node:
        keys = row.keys()
        return Node(
            id=row["id"],
            name=row["name"],
            hostname=row["hostname"],
            device_type=row["device_type"] if "device_type" in keys else "mac",
            max_concurrent=row["max_concurrent"],
            status=NodeStatus(row["status"]),
            last_seen=parse_dt(row["last_seen"]) or utcnow(),
            ip=row["ip"],
        )

    @staticmethod
    def _row_to_agent(row: sqlite3.Row) -> Agent:
        keys = row.keys()
        loop_config = None
        if "loop_config" in keys and row["loop_config"]:
            try:
                loop_config = json.loads(row["loop_config"])
            except json.JSONDecodeError:
                loop_config = None
        return Agent(
            id=row["id"],
            node_id=row["node_id"],
            node_name=row["node_name"] if "node_name" in keys else None,
            title=row["title"],
            prompt=row["prompt"],
            repo_path=row["repo_path"],
            repo=row["repo"] if "repo" in keys else None,
            status=AgentStatus(row["status"]),
            summary=row["summary"],
            remote=bool(row["remote"]) if "remote" in keys else False,
            claude_session_id=row["claude_session_id"] if "claude_session_id" in keys else None,
            loop=bool(row["loop"]) if "loop" in keys else True,
            loop_config=loop_config,
            iteration=int(row["iteration"]) if "iteration" in keys else 0,
            max_iterations=int(row["max_iterations"]) if "max_iterations" in keys else 0,
            loop_phase=row["loop_phase"] if "loop_phase" in keys else None,
            estimated_cost_usd=float(row["estimated_cost_usd"]) if "estimated_cost_usd" in keys else 0.0,
            multi_agent=bool(row["multi_agent"]) if "multi_agent" in keys else False,
            created_at=parse_dt(row["created_at"]) or utcnow(),
            started_at=parse_dt(row["started_at"]),
            completed_at=parse_dt(row["completed_at"]),
            error=row["error"],
        )

    @staticmethod
    def _row_to_repo(row: sqlite3.Row) -> Repo:
        return Repo(
            id=row["id"],
            name=row["name"],
            url=row["url"],
            branch=row["branch"],
            slug=row["slug"],
            created_at=parse_dt(row["created_at"]) or utcnow(),
        )

    @staticmethod
    def _row_to_node_repo_status(row: sqlite3.Row) -> NodeRepoStatus:
        return NodeRepoStatus(
            name=row["repo_name"],
            ok=bool(row["ok"]),
            path=row["path"],
            branch=row["branch"],
            commit=row["commit"] if "commit" in row.keys() else None,
            error=row["error"],
            synced_at=parse_dt(row["synced_at"]),
        )
