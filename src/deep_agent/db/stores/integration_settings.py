from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from deep_agent.db.models import IntegrationSettings
from deep_agent.db.stores._session import SessionBoundMixin
from deep_agent.integrations.catalog import get_integration_definition
from deep_agent.schemas.integration import validate_integration_settings


class IntegrationSettingsStore(SessionBoundMixin):
    async def get_by_code(self, code: str) -> IntegrationSettings | None:
        async with self._read() as db:
            return await db.get(IntegrationSettings, code)

    async def list_all(self) -> list[IntegrationSettings]:
        async with self._read() as db:
            return list((await db.scalars(select(IntegrationSettings))).all())

    async def save(self, code: str, settings: dict) -> IntegrationSettings | None:
        if get_integration_definition(code) is None:
            raise ValueError("Unknown integration code")
        validate_integration_settings(settings)
        async with self._write() as db:
            if not settings:
                await db.execute(delete(IntegrationSettings).where(IntegrationSettings.code == code))
                return None
            # Atomic upsert also serializes concurrent first saves on a missing key.
            dialect = db.get_bind().dialect.name
            insert = {"sqlite": sqlite_insert, "postgresql": postgres_insert, "mysql": mysql_insert}[dialect]
            statement = insert(IntegrationSettings).values(code=code, settings=settings)
            if dialect == "mysql":
                statement = statement.on_duplicate_key_update(settings=settings, updated_at=func.now())
            else:
                statement = statement.on_conflict_do_update(index_elements=[IntegrationSettings.code], set_={"settings": settings, "updated_at": func.now()})
            await db.execute(statement)
            return await db.get(IntegrationSettings, code)

    async def delete(self, code: str) -> None:
        await self.save(code, {})
