from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from deep_agent.api.v1.request_identity import RequestUserDep
from deep_agent.core.context import AppContextDep


router = APIRouter()


class DatabaseInfoResponse(BaseModel):
    schema_version: str


@router.get("/database-info", response_model=DatabaseInfoResponse)
async def get_database_info(
    _user: RequestUserDep,
    ctx: AppContextDep,
) -> DatabaseInfoResponse:
    """Return the schema revision applied to the connected database."""
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
    return DatabaseInfoResponse(schema_version=str(version))
