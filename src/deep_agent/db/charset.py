"""Read-only Unicode compatibility checks for the administrator database summary."""
from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import Text, bindparam, text
from sqlalchemy.exc import SQLAlchemyError

from deep_agent.db.models import Base
from deep_agent.db.types import TextJSON


class DatabaseCharsetInfo(BaseModel):
    status: Literal["ok", "warning", "unknown", "not_applicable"] = "unknown"
    database: str | None = None
    client: str | None = None
    connection: str | None = None
    results: str | None = None
    checked_columns: int = 0
    issues: list[str] = Field(default_factory=list)


_DISPLAY_COLUMNS = frozenset({"name", "title", "description", "username", "nickname", "display_name"})


def business_text_columns() -> set[tuple[str, str]]:
    """JSON, free text and display names; opaque IDs and protocol enums are excluded."""
    return {(table.name, column.name)
            for table in Base.metadata.tables.values() for column in table.columns
            if isinstance(column.type, (Text, TextJSON)) or column.name in _DISPLAY_COLUMNS}


def mysql_charset_info(settings, columns) -> DatabaseCharsetInfo:
    info = DatabaseCharsetInfo(**{key: settings.get(key) for key in ("database", "client", "connection", "results")})
    for key, label in (("database", "数据库默认"), ("client", "客户端"), ("connection", "连接"), ("results", "返回")):
        value = getattr(info, key)
        # NULL character_set_results disables server-side result conversion.
        if key == "results" and value is None:
            continue
        if (value or "").lower() != "utf8mb4":
            info.issues.append(f"{label}字符集为 {value or '未知'}，应为 utf8mb4")
    expected = business_text_columns()
    seen = set()
    for row in columns:
        key = (row["table_name"], row["column_name"])
        if key not in expected:
            continue
        seen.add(key)
        info.checked_columns += 1
        charset = row["character_set_name"]
        if (charset or "").lower() != "utf8mb4":
            info.issues.append(f"{key[0]}.{key[1]}：{charset or '未知'}，应为 utf8mb4")
    missing = expected - seen
    for table, column in sorted(missing):
        info.issues.append(f"未能检查字段 {table}.{column}")
    info.status = "warning" if info.issues else "ok"
    # No metadata visibility must never be presented as a successful field check.
    if not seen:
        info.status = "unknown"
    return info


async def read_database_charset(session, dialect: str) -> DatabaseCharsetInfo:
    try:
        async with asyncio.timeout(3):
            if dialect == "mysql":
                settings = (await session.execute(text(
                    "SELECT @@character_set_database AS `database`, "
                    "@@character_set_client AS client, @@character_set_connection AS connection, "
                    "@@character_set_results AS results"
                ))).mappings().one()
                statement = text(
                    "SELECT TABLE_NAME AS table_name, COLUMN_NAME AS column_name, "
                    "CHARACTER_SET_NAME AS character_set_name FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN :tables "
                    "ORDER BY TABLE_NAME, ORDINAL_POSITION"
                ).bindparams(bindparam("tables", expanding=True))
                columns = (await session.execute(statement, {"tables": sorted(Base.metadata.tables)})).mappings().all()
                return mysql_charset_info(settings, columns)
            if dialect == "sqlite":
                encoding = (await session.execute(text("PRAGMA encoding"))).scalar_one()
                return DatabaseCharsetInfo(status="ok", database=str(encoding))
            if dialect == "postgresql":
                database = str((await session.execute(text("SHOW server_encoding"))).scalar_one())
                client = str((await session.execute(text("SHOW client_encoding"))).scalar_one())
                good = database.upper() == client.upper() == "UTF8"
                return DatabaseCharsetInfo(status="ok" if good else "warning", database=database, client=client,
                                           connection=client, issues=[] if good else ["数据库与客户端编码应为 UTF8"])
            return DatabaseCharsetInfo(status="not_applicable")
    except (SQLAlchemyError, TimeoutError):
        # The basic connection/revision summary remains available. Never expose
        # SQL, connection URLs or driver errors and never silently mark healthy.
        return DatabaseCharsetInfo(status="unknown", issues=["字符集检查未完成，请稍后重试或核对数据库读取权限"])
