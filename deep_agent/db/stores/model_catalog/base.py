from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import delete, select

from deep_agent.db.models import ModelCatalog
from deep_agent.db.stores._session import SessionBoundMixin


class ModelCatalogStoreBase(SessionBoundMixin):
    async def list_all(self) -> list[ModelCatalog]:
        async with self._read() as db:
            result = await db.execute(
                select(ModelCatalog).order_by(
                    ModelCatalog.lab_label,
                    ModelCatalog.model_label,
                    ModelCatalog.provider_label,
                )
            )
            return list(result.scalars().all())

    async def replace_all(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """Replace the complete catalog in one database transaction."""
        async with self._write() as db:
            await db.execute(delete(ModelCatalog))
            db.add_all(ModelCatalog(**dict(row)) for row in rows)
            await db.flush()
            return len(rows)
