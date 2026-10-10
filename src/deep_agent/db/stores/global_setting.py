from __future__ import annotations

from collections.abc import Callable

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
        return await self.update_atomic(key, transform=lambda _: value, version=version)

    async def update_atomic(
        self,
        key: str,
        *,
        transform: Callable[[GlobalSetting | None], dict],
        version: int,
    ) -> GlobalSetting:
        """Resolve a local update against the current row within one write transaction.

        The exclusive configuration barrier serializes workers even when the
        setting does not exist yet; locking only the setting row cannot protect
        concurrent first inserts. The callback must not perform external I/O.
        """
        async with self._write(exclusive=True) as db:
            result = await db.execute(
                select(GlobalSetting).where(GlobalSetting.key == key).with_for_update()
            )
            row = result.scalar_one_or_none()
            value = transform(row)
            if row is None:
                row = GlobalSetting(key=key, value=value, version=version)
                db.add(row)
            else:
                row.value = value
                row.version = version
            await db.flush()
            await db.refresh(row)
            return row
