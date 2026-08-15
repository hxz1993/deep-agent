from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from deep_agent.api.v1.request_identity import RequestUserDep
from deep_agent.core.context import AppContextDep
from deep_agent.db.url import get_database_url


router = APIRouter()


class DatabaseInfoResponse(BaseModel):
    schema_version: str
    database_type: str
    database_type_label: str
    connection_mode: str
    host: str | None = None
    port: int | None = None
    database: str
    connection_summary: str
    credentials_hidden: bool = True


_DATABASE_TYPE_LABELS = {
    "sqlite": "SQLite",
    "mysql": "MySQL",
    "postgresql": "PostgreSQL",
}


def build_database_info(
    database_url: str | None = None,
    *,
    schema_version: str = "—",
) -> DatabaseInfoResponse:
    """Build a safe runtime database summary without credentials or local paths."""
    url = make_url(database_url or get_database_url())
    database_type = url.get_backend_name()
    database_type_label = _DATABASE_TYPE_LABELS.get(
        database_type,
        database_type.upper(),
    )

    if database_type == "sqlite":
        database_name = Path(url.database or "").name or "内存数据库"
        return DatabaseInfoResponse(
            schema_version=schema_version,
            database_type=database_type,
            database_type_label=database_type_label,
            connection_mode="本地文件",
            database=database_name,
            connection_summary=f"SQLite · {database_name}",
        )

    host = url.host or "—"
    database_name = url.database or "—"
    host_port = f"{host}:{url.port}" if url.port is not None else host
    return DatabaseInfoResponse(
        schema_version=schema_version,
        database_type=database_type,
        database_type_label=database_type_label,
        connection_mode="远程数据库",
        host=url.host,
        port=url.port,
        database=database_name,
        connection_summary=f"{database_type_label} · {host_port}/{database_name}",
    )


@router.get("/database-info", response_model=DatabaseInfoResponse)
async def get_database_info(
    _user: RequestUserDep,
    ctx: AppContextDep,
) -> DatabaseInfoResponse:
    """Return the live schema revision and credential-free connection summary."""
    try:
        async with ctx.db_session_factory() as session:
            result = await session.execute(text("SELECT version_num FROM alembic_version"))
            version = result.scalar_one_or_none()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to read database schema version",
        ) from exc
    if not version:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database schema version is unavailable",
        )
    return build_database_info(schema_version=str(version))
