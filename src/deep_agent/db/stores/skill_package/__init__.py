from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.db.dialect import DbDialect
from deep_agent.db.stores.skill_package.base import SkillPackageStoreBase
from deep_agent.db.stores.skill_package.mysql import MySQLSkillPackageStore
from deep_agent.db.stores.skill_package.postgres import PostgresSkillPackageStore
from deep_agent.db.stores.skill_package.sqlite import SqliteSkillPackageStore

__all__ = ["SkillPackageStoreBase", "create_skill_package_store"]


def create_skill_package_store(
    dialect: DbDialect,
    session_factory: async_sessionmaker[AsyncSession],
) -> SkillPackageStoreBase:
    match dialect:
        case DbDialect.MYSQL:
            return MySQLSkillPackageStore(session_factory)
        case DbDialect.SQLITE:
            return SqliteSkillPackageStore(session_factory)
        case _:
            return PostgresSkillPackageStore(session_factory)
