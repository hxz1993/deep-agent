from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from deep_agent.db.models import Agent, McpServer, RemoteAgentConnection
from deep_agent.db.reference_integrity import ReferenceConflict, is_unique_conflict, lock_optional
from deep_agent.db.stores._session import SessionBoundMixin

from .contracts import A2AConfig, CONNECTION_PREFIX, ConnectionWrite, McpConfig


async def lock_agent_connections(db, codes: list[str], *, old_codes=()) -> None:
    for code in sorted({code for code in [*old_codes, *codes] if code.startswith(CONNECTION_PREFIX)}):
        row = await lock_optional(db, RemoteAgentConnection, RemoteAgentConnection.id, code.removeprefix(CONNECTION_PREFIX))
        if row is None and code in codes:
            raise ReferenceConflict("远程 Agent 连接不存在，请刷新后重试")


async def mcp_connections(db, name: str) -> list[RemoteAgentConnection]:
    rows = (await db.scalars(select(RemoteAgentConnection).order_by(RemoteAgentConnection.id))).all()
    return [row for row in rows if row.config.get("mcp_server_name") == name]


class ConnectionStore(SessionBoundMixin):
    async def list(self) -> list[RemoteAgentConnection]:
        async with self._read() as db:
            return list(await db.scalars(select(RemoteAgentConnection).order_by(RemoteAgentConnection.created_at, RemoteAgentConnection.id)))

    async def get(self, connection_id: str) -> RemoteAgentConnection | None:
        async with self._read() as db:
            return await db.get(RemoteAgentConnection, connection_id)

    async def selected(self, codes) -> list[RemoteAgentConnection]:
        ids = [code.removeprefix(CONNECTION_PREFIX) for code in codes if code.startswith(CONNECTION_PREFIX)]
        if not ids:
            return []
        async with self._read() as db:
            rows = {row.id: row for row in await db.scalars(select(RemoteAgentConnection).where(RemoteAgentConnection.id.in_(ids)))}
        return [rows[connection_id] for connection_id in ids if connection_id in rows]

    async def agents(self) -> dict[str, list[dict]]:
        async with self._read() as db:
            rows = (await db.execute(select(Agent.id, Agent.name, Agent.capabilities).order_by(Agent.name))).all()
        result: dict[str, list[dict]] = {}
        for agent_id, name, codes in rows:
            for code in codes or ():
                if code.startswith(CONNECTION_PREFIX):
                    result.setdefault(code.removeprefix(CONNECTION_PREFIX), []).append({"id": agent_id, "name": name})
        return result

    async def save(self, body: ConnectionWrite, connection_id: str | None = None) -> RemoteAgentConnection | None:
        try:
            async with self._write() as db:
                if isinstance(body.config, McpConfig):
                    mcp = await lock_optional(db, McpServer, McpServer.name, body.config.mcp_server_name)
                    if mcp is None or mcp.transport != "url":
                        raise ReferenceConflict("请选择已配置的 Streamable HTTP MCP")
                row = None
                if connection_id is not None:
                    row = await lock_optional(db, RemoteAgentConnection, RemoteAgentConnection.id, connection_id)
                    if row is None:
                        return None
                    if row.revision != body.revision:
                        raise ReferenceConflict("连接已更新，请刷新后重试")
                previous = row.config if row else {}
                config = body.config.model_dump(mode="json")
                if isinstance(body.config, A2AConfig):
                    config["headers"] = {
                        key: value if value is not None else previous.get("headers", {}).get(key)
                        for key, value in body.config.headers.items()
                    }
                    config["headers"] = {key: value for key, value in config["headers"].items() if value is not None}
                    config["approved_interface"] = None
                    if (previous.get("card_url"), previous.get("headers")) == (config["card_url"], config["headers"]):
                        config["approved_interface"] = previous.get("approved_interface")
                if row is None:
                    row = RemoteAgentConnection(id=str(uuid4()), name=body.name, config=config, description=body.description)
                    db.add(row)
                else:
                    if row.config != config:
                        row.inspection = None
                    row.config = config
                    row.name = body.name
                    row.description = body.description
                    row.revision += 1
                    row.updated_at = datetime.now(UTC)
                await db.flush()
                await db.refresh(row)
                return row
        except IntegrityError as error:
            if not is_unique_conflict(error):
                raise
            raise ReferenceConflict("远程 Agent 名称已存在") from error

    async def inspection(self, connection: RemoteAgentConnection, value: dict,
                         interface: dict | None) -> RemoteAgentConnection | None:
        async with self._write() as db:
            row = await lock_optional(db, RemoteAgentConnection, RemoteAgentConnection.id, connection.id)
            if row is None:
                return None
            if row.revision != connection.revision:
                raise ReferenceConflict("检查期间连接已更新，请重新检查")
            if interface and row.config.get("approved_interface") != interface:
                row.config = {**row.config, "approved_interface": interface}
                row.revision += 1
            row.inspection = {**value, "revision": row.revision, "checked_at": datetime.now(UTC).isoformat()}
            await db.flush()
            await db.refresh(row)
            return row

    async def delete(self, connection_id: str, revision: int) -> bool:
        async with self._write() as db:
            row = await lock_optional(db, RemoteAgentConnection, RemoteAgentConnection.id, connection_id)
            if row is None:
                return False
            if row.revision != revision:
                raise ReferenceConflict("连接已更新，请刷新后重试")
            agents = (await db.execute(select(Agent.name, Agent.capabilities).with_for_update())).all()
            used_by = [name for name, codes in agents if CONNECTION_PREFIX + connection_id in (codes or [])]
            if used_by:
                raise ReferenceConflict("连接正被以下 Agent 使用：" + "、".join(sorted(used_by)))
            await db.delete(row)
            return True
