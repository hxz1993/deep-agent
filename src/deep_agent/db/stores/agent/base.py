from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.db.models import Agent, LlmModel, UiToolPackage, ChatSession, ChannelAccount
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
                await lock_reference(db, LlmModel, LlmModel.id, data.model_id, "LLM model")
                await self._lock_ui_packages(db, data.capabilities)
                if await self._get_by_name(db, data.name) is not None:
                    raise ValueError(f"Agent already exists: {data.name}")
                row = Agent(
                    name=data.name,
                    model_id=data.model_id,
                    temperature=data.temperature,
                    capabilities=data.capabilities,
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
                for model_id in sorted({old_model, data.model_id or old_model}):
                    await lock_reference(db, LlmModel, LlmModel.id, model_id, "LLM model")
                await self._lock_ui_packages(db, old_caps + (data.capabilities or []))
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

    async def delete(self, agent_id: str) -> bool:
        async with self._write() as db:
            if await lock_optional(db, Agent, Agent.id, agent_id) is None:
                return False
            for model, key, message in (
                (ChatSession, ChatSession.session_id, "Agent 存在历史会话，请先删除相关会话"),
                (ChannelAccount, ChannelAccount.id, "Agent 正被渠道配置使用，请先调整对应渠道"),
            ):
                if (await db.execute(select(key).where(model.agent_id == agent_id).limit(1).with_for_update())).first():
                    raise ReferenceConflict(message)
            result = await db.execute(delete(Agent).where(Agent.id == agent_id))
            await db.flush()
            return result.rowcount > 0

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
