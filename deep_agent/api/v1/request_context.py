from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel

from deep_agent.agent.request_context import resolve_request_context
from deep_agent.api.v1.request_identity import RequestUserDep


router = APIRouter()


class RequestContextResponse(BaseModel):
    authenticated: bool = True
    user: str
    app: str | None = None
    space: str | None = None
    language: str | None = None


@router.get("/request-context", response_model=RequestContextResponse)
async def get_request_context(
    user: RequestUserDep,
    app: Annotated[str | None, Header()] = None,
    space: Annotated[str | None, Header()] = None,
    language: Annotated[str | None, Header()] = None,
) -> RequestContextResponse:
    """Return the non-sensitive platform context effective for this request."""
    try:
        context = resolve_request_context(
            app=app,
            space=space,
            language=language,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return RequestContextResponse(
        user=user,
        app=context.app,
        space=context.space,
        language=context.language,
    )
