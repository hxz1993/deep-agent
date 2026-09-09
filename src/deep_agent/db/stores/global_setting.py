from __future__ import annotations

from sqlalchemy import select

from deep_agent.db.models import GlobalSetting
from deep_agent.db.stores._session import SessionBoundMixin


class GlobalSettingStore(SessionBoundMixin):
    async def get(self, key: str) -> GlobalSetting | None:
        async with self._read() as db:
            result = await db.execute(
                select(GlobalSetting).where(GlobalSetting.key == key)
            )
            return result.scalar_one_or_none()

    async def upsert(
        self,
        key: str,
        *,
        value: dict,
        version: int,
    ) -> GlobalSetting:
        async with self._write() as db:
            result = await db.execute(
                select(GlobalSetting).where(GlobalSetting.key == key)
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = GlobalSetting(key=key, value=value, version=version)
                db.add(row)
            else:
                row.value = value
                row.version = version
            await db.flush()
            await db.refresh(row)
            return row
