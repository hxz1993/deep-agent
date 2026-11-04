from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel

from deep_agent.agent.request_context import resolve_request_context_overrides
from deep_agent.api.v1.request_identity import RequestUserDep
from deep_agent.services.scope_names import resolve_scope_directory


router = APIRouter()


class RequestContextResponse(BaseModel):
    authenticated: bool = True
    user: str
    app: str | None = None
    space: str | None = None
    language: str | None = None
    app_name: str | None = None
    space_name: str | None = None


@router.get("/request-context", response_model=RequestContextResponse)
async def get_request_context(
    request: Request,
    user: RequestUserDep,
    app: Annotated[str | None, Header()] = None,
    space: Annotated[str | None, Header()] = None,
    language: Annotated[str | None, Header()] = None,
    console_app: Annotated[
        str | None,
        Header(alias="X-Deep-Agent-App"),
    ] = None,
    console_space: Annotated[
        str | None,
        Header(alias="X-Deep-Agent-Space"),
    ] = None,
    console_language: Annotated[
        str | None,
        Header(alias="X-Deep-Agent-Language"),
    ] = None,
) -> RequestContextResponse:
    """Return the non-sensitive platform context effective for this request."""
    try:
        context = resolve_request_context_overrides(
            app=app,
            space=space,
            language=language,
            override_app=console_app,
            override_space=console_space,
            override_language=console_language,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    directory = await resolve_scope_directory(request, user)
    return RequestContextResponse(
        user=user,
        app=context.app,
        space=context.space,
        language=context.language,
        app_name=directory.apps.get(context.app) if context.app else None,
        space_name=directory.spaces.get(context.space) if context.space else None,
    )
