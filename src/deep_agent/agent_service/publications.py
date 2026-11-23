from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import literal, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.db.models import Agent, AgentPublication, GlobalSetting
from deep_agent.db.reference_integrity import ReferenceConflict, is_unique_conflict, lock_optional, lock_reference
from deep_agent.db.stores._session import SessionBoundMixin

AgentCode = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)]
ScopeId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=240)]
_UPDATER_KEY_PREFIX = "agent_publication_updater:"


class AppScopeItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    app_id: ScopeId
    space_id: ScopeId


class AppScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["all", "selected"]
    items: list[AppScopeItem] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def check_items(self):
        if self.mode == "all" and self.items:
            raise ValueError("全部应用时不能同时指定应用")
        if self.mode == "selected" and not self.items:
            raise ValueError("指定应用时至少添加一个应用")
        if len(set(self.items)) != len(self.items):
            raise ValueError("应用范围中有重复项")
        return self


class PublicationScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    apps: AppScope

    def allows(self, app_id: str, space_id: str) -> bool:
        return self.apps.mode == "all" or AppScopeItem(app_id=app_id, space_id=space_id) in self.apps.items


class PublicationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_code: AgentCode
    name: Name
    description: Description = ""
    agent_id: Annotated[str, StringConstraints(min_length=1, max_length=36)]
    scope: PublicationScope
    enabled: bool = True


class PublicationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    name: Name | None = None
    description: Description | None = None
    agent_id: Annotated[str, StringConstraints(min_length=1, max_length=36)] | None = None
    scope: PublicationScope | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def reject_nulls(self):
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("发布配置字段不能为 null")
        return self


class PublicationStore(SessionBoundMixin):
    @staticmethod
    def _with_updaters():
        return select(AgentPublication, GlobalSetting.value).outerjoin(
            GlobalSetting, GlobalSetting.key == literal(_UPDATER_KEY_PREFIX) + AgentPublication.id
        )

    async def list_with_updaters(self) -> list[tuple[AgentPublication, str | None]]:
        async with self._read() as db:
            query = self._with_updaters().order_by(AgentPublication.created_at, AgentPublication.code)
            return [(row, self._current_updater(row, value)) for row, value in await db.execute(query)]

    async def get_with_updater(self, code: str) -> tuple[AgentPublication, str | None] | None:
        async with self._read() as db:
            result = await db.execute(self._with_updaters().where(AgentPublication.code == code))
            record = result.one_or_none()
            if record is None:
                return None
            row, value = record
            return row, self._current_updater(row, value)

    @staticmethod
    def _current_updater(row: AgentPublication, value: object) -> str | None:
        # Earlier versions can still edit publications without recording an actor.
        if not isinstance(value, dict) or value.get("revision") != row.revision:
            return None
        actor = value.get("updated_by")
        return actor if isinstance(actor, str) and actor else None

    @staticmethod
    async def _record_updater(db: AsyncSession, row: AgentPublication, actor: str) -> None:
        key = _UPDATER_KEY_PREFIX + row.id
        entry = await db.get(GlobalSetting, key)
        value = {"updated_by": actor, "revision": row.revision}
        if entry is None:
            db.add(GlobalSetting(key=key, value=value, version=1))
        else:
            entry.value = value

    async def list_enabled(self) -> list[AgentPublication]:
        async with self._read() as db:
            query = select(AgentPublication).where(AgentPublication.enabled.is_(True)).order_by(AgentPublication.code)
            return list((await db.scalars(query)).all())

    async def get_by_id(self, publication_id: str) -> AgentPublication | None:
        async with self._read() as db:
            return await db.get(AgentPublication, publication_id)

    async def get(self, code: str) -> AgentPublication | None:
        async with self._read() as db:
            return await db.scalar(select(AgentPublication).where(AgentPublication.code == code))

    async def create(self, body: PublicationCreate, *, updated_by: str | None = None) -> AgentPublication:
        try:
            async with self._write() as db:
                await lock_reference(db, Agent, Agent.id, body.agent_id, "Agent")
                row = AgentPublication(
                    id=str(uuid4()), code=body.agent_code,
                    **body.model_dump(mode="json", exclude={"agent_code"}),
                )
                db.add(row)
                await db.flush()
                if updated_by:
                    await self._record_updater(db, row, updated_by)
                await db.refresh(row)
                return row
        except IntegrityError as exc:
            if not is_unique_conflict(exc):
                raise
            raise ReferenceConflict("Agent 编码已存在") from exc

    async def update(self, code: str, body: PublicationPatch, *, updated_by: str | None = None) -> AgentPublication | None:
        async with self._write() as db:
            current = await db.scalar(select(AgentPublication).where(AgentPublication.code == code))
            if current is None:
                return None
            for agent_id in sorted({current.agent_id, body.agent_id or current.agent_id}):
                await lock_reference(db, Agent, Agent.id, agent_id, "Agent")
            row = await lock_optional(db, AgentPublication, AgentPublication.code, code)
            if row is None:
                return None
            if row.revision != body.revision or row.id != current.id:
                raise ReferenceConflict("发布配置已更新，请刷新后重试")
            for name, value in body.model_dump(mode="json", exclude_unset=True, exclude={"revision"}).items():
                setattr(row, name, value)
            row.revision += 1
            row.updated_at = datetime.now(UTC)
            if updated_by:
                await self._record_updater(db, row, updated_by)
            await db.flush()
            return row

    async def delete(self, code: str, revision: int) -> bool:
        async with self._write() as db:
            row = await lock_optional(db, AgentPublication, AgentPublication.code, code)
            if row is None:
                return False
            if row.revision != revision:
                raise ReferenceConflict("发布配置已更新，请刷新后重试")
            entry = await db.get(GlobalSetting, _UPDATER_KEY_PREFIX + row.id)
            if entry is not None:
                await db.delete(entry)
            await db.delete(row)
            return True
