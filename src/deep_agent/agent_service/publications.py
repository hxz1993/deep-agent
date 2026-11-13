from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from deep_agent.db.models import Agent, AgentPublication
from deep_agent.db.reference_integrity import ReferenceConflict, is_unique_conflict, lock_optional, lock_reference
from deep_agent.db.stores._session import SessionBoundMixin

Code = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)]
ScopeId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=240)]


class AppScopeItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    app_id: ScopeId
    space_id: ScopeId


class AppScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["all", "selected", "fixed"]
    items: list[AppScopeItem] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def check_items(self):
        if self.mode == "all" and self.items:
            raise ValueError("全部应用时不能同时指定应用")
        if self.mode == "fixed" and len(self.items) != 1:
            raise ValueError("固定应用时必须选择一个应用")
        if self.mode == "selected" and not self.items:
            raise ValueError("指定应用时至少添加一个应用")
        if len(set(self.items)) != len(self.items):
            raise ValueError("应用范围中有重复项")
        return self


class PublicationScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    apps: AppScope

    @property
    def fixed_app(self) -> AppScopeItem | None:
        return self.apps.items[0] if self.apps.mode == "fixed" else None

    def allows(self, app_id: str, space_id: str) -> bool:
        return self.apps.mode == "all" or AppScopeItem(app_id=app_id, space_id=space_id) in self.apps.items


class PublicationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Code
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
    async def list(self) -> list[AgentPublication]:
        async with self._read() as db:
            return list((await db.scalars(select(AgentPublication).order_by(AgentPublication.created_at, AgentPublication.code))).all())

    async def get(self, code: str) -> AgentPublication | None:
        async with self._read() as db:
            return await db.scalar(select(AgentPublication).where(AgentPublication.code == code))

    async def create(self, body: PublicationCreate) -> AgentPublication:
        try:
            async with self._write() as db:
                await lock_reference(db, Agent, Agent.id, body.agent_id, "Agent")
                row = AgentPublication(id=str(uuid4()), **body.model_dump(mode="json"))
                db.add(row)
                await db.flush()
                await db.refresh(row)
                return row
        except IntegrityError as exc:
            if not is_unique_conflict(exc):
                raise
            raise ReferenceConflict("发布编码已存在") from exc

    async def update(self, code: str, body: PublicationPatch) -> AgentPublication | None:
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
            await db.flush()
            return row

    async def delete(self, code: str, revision: int) -> bool:
        async with self._write() as db:
            row = await lock_optional(db, AgentPublication, AgentPublication.code, code)
            if row is None:
                return False
            if row.revision != revision:
                raise ReferenceConflict("发布配置已更新，请刷新后重试")
            await db.delete(row)
            return True
