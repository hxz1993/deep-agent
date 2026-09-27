from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.db.models import Agent, LlmModel, UiToolPackage, ChatSession, ChatSessionEvent, ChannelAccount, ChannelSessionBinding, McpServer
from deep_agent.db.deletion import DeletionImpact, DeletionConfirmationRequired
from deep_agent.db.reference_integrity import ReferenceConflict, lock_reference, lock_optional, is_unique_conflict
from deep_agent.db.stores._session import SessionBoundMixin
from deep_agent.schemas.agent import AgentCreate, AgentUpdate


class AgentStoreBase(SessionBoundMixin):
    async def get_by_id(self, agent_id: str) -> Agent | None:
        async with self._read() as db:
            return await self._get_by_id(db, agent_id)

    async def get_by_name(self, name: str) -> Agent | None:
        async with self._read() as db:
            return await self._get_by_name(db, name)

    async def list_all(self) -> list[Agent]:
        async with self._read() as db:
            return await self._list_all(db)

    async def create_unique(self, data: AgentCreate) -> Agent:
        try:
            async with self._write() as db:
                if data.model_id is not None:
                    await lock_reference(db, LlmModel, LlmModel.id, data.model_id, "LLM model")
                await self._lock_ui_packages(db, data.capabilities)
                await self._lock_mcp_servers(db, data.capabilities)
                if await self._get_by_name(db, data.name) is not None:
                    raise ValueError(f"Agent already exists: {data.name}")
                row = Agent(
                    name=data.name,
                    model_id=data.model_id,
                    capabilities=data.capabilities,
                    ui_tool_selection={},
                    skills=data.skills,
                    prompt=data.prompt,
                )
                db.add(row)
                await db.flush()
                await db.refresh(row)
                return row
        except IntegrityError as exc:
            if not is_unique_conflict(exc):
                raise
            raise ValueError(f"Agent write conflict: {data.name}") from exc

    async def update_unique(self, agent_id: str, data: AgentUpdate) -> Agent | None:
        try:
            async with self._write() as db:
                row = await self._get_by_id(db, agent_id)
                if row is None:
                    return None
                old_model, old_caps = row.model_id, list(row.capabilities or [])
                new_model = data.model_id if "model_id" in data.model_fields_set else old_model
                for model_id in sorted(value for value in {old_model, new_model} if value is not None):
                    await lock_reference(db, LlmModel, LlmModel.id, model_id, "LLM model")
                await self._lock_ui_packages(db, old_caps + (data.capabilities or []))
                await self._lock_mcp_servers(db, data.capabilities if data.capabilities is not None else old_caps,
                                             old_capabilities=old_caps)
                row = await lock_optional(db, Agent, Agent.id, agent_id)
                if row is None:
                    return None
                if row.model_id != old_model or list(row.capabilities or []) != old_caps:
                    raise ReferenceConflict("Agent references changed concurrently; refresh and retry")
                if data.name is not None and data.name != row.name:
                    conflict = await self._get_by_name(db, data.name)
                    if conflict is not None:
                        raise ValueError(f"Agent already exists: {data.name}")
                updates = data.model_dump(exclude_unset=True)
                # Keep the legacy column for existing databases, but never use its old per-tool scope.
                updates["ui_tool_selection"] = {}
                for field, value in updates.items():
                    setattr(row, field, value)
                await db.flush()
                await db.refresh(row)
                return row
        except IntegrityError as exc:
            if not is_unique_conflict(exc):
                raise
            raise ValueError(f"Agent write conflict: {agent_id}") from exc

    async def list_referencing_capability(self, capability: str) -> list[Agent]:
        async with self._read() as db:
            rows = await self._list_all(db)
            return [row for row in rows if capability in (row.capabilities or [])]

    async def list_referencing_skill(self, skill_code: str) -> list[Agent]:
        async with self._read() as db:
            rows = await self._list_all(db)
            return [row for row in rows if skill_code in (row.skills or [])]

    async def _deletion_impact(self, db, agent_id: str, user_id: str) -> DeletionImpact | None:
        if await lock_optional(db, Agent, Agent.id, agent_id) is None:
            return None
        if (await db.execute(select(ChannelAccount.id).where(
            ChannelAccount.agent_id == agent_id).limit(1).with_for_update())).first():
            raise ReferenceConflict("Agent 正被渠道配置使用，请先将对应渠道改绑到其他 Agent 或删除渠道；仅停用渠道不会解除关联")
        sessions = (await db.execute(select(ChatSession.session_id, ChatSession.user_id, ChatSession.created_at)
            .where(ChatSession.agent_id == agent_id).order_by(ChatSession.session_id).with_for_update())).all()
        return DeletionImpact.build("agent", agent_id, user_id, sessions=[tuple(row) for row in sessions])

    async def deletion_impact(self, agent_id: str, *, user_id: str) -> DeletionImpact | None:
        async with self._write() as db:
            return await self._deletion_impact(db, agent_id, user_id)

    async def delete(self, agent_id: str, *, user_id: str = "", confirmation_token: str | None = None) -> bool:
        async with self._write() as db:
            impact = await self._deletion_impact(db, agent_id, user_id)
            if impact is None:
                return False
            if (impact.counts["session_count"] or confirmation_token) and confirmation_token != impact.token:
                raise DeletionConfirmationRequired(impact)
            sessions = select(ChatSession.session_id).where(ChatSession.agent_id == agent_id)
            await db.execute(update(ChannelSessionBinding).where(ChannelSessionBinding.session_id.in_(sessions)).values(session_id=None))
            await db.execute(delete(ChatSessionEvent).where(ChatSessionEvent.session_id.in_(sessions)))
            await db.execute(delete(ChatSession).where(ChatSession.agent_id == agent_id))
            result = await db.execute(delete(Agent).where(Agent.id == agent_id))
            await db.flush()
            return result.rowcount > 0

    @staticmethod
    async def _lock_mcp_servers(db: AsyncSession, capabilities: list[str], *, old_capabilities=()) -> None:
        from deep_agent.capabilities.mcp.builtin_config import is_shipped_builtin
        names = {code.removeprefix("mcp:") for code in [*old_capabilities, *capabilities] if code.startswith("mcp:")}
        for name in sorted(names):
            row = await lock_optional(db, McpServer, McpServer.name, name)
            if row is None and f"mcp:{name}" in capabilities and not is_shipped_builtin(name):
                raise ReferenceConflict(f"MCP not found: {name}")

    @staticmethod
    async def _lock_ui_packages(db: AsyncSession, capabilities: list[str]) -> None:
        # builtin UI definitions live in code, not in this table.
        for code in sorted({c for c in capabilities if c.startswith("ui:") and c != "ui:builtin"}):
            await lock_reference(db, UiToolPackage, UiToolPackage.code, code, "UI tool package")

    async def _get_by_id(self, db: AsyncSession, agent_id: str) -> Agent | None:
        result = await db.execute(select(Agent).where(Agent.id == agent_id))
        return result.scalar_one_or_none()

    async def _get_by_name(self, db: AsyncSession, name: str) -> Agent | None:
        result = await db.execute(select(Agent).where(Agent.name == name))
        return result.scalar_one_or_none()

    async def _list_all(self, db: AsyncSession) -> list[Agent]:
        result = await db.execute(select(Agent).order_by(Agent.name))
        return list(result.scalars().all())
