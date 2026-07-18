from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header
from pydantic import BaseModel

from deep_agent.api.v1.request_identity import RequestUserDep


router = APIRouter()


class RequestContextResponse(BaseModel):
    authenticated: bool = True
    user: str
    app: str | None = None
    space: str | None = None
    language: str | None = None


def _normalize_header(value: str | None) -> str | None:
    normalized = (value or "").strip()
    return normalized or None


@router.get("/request-context", response_model=RequestContextResponse)
async def get_request_context(
    user: RequestUserDep,
    app: Annotated[str | None, Header()] = None,
    space: Annotated[str | None, Header()] = None,
    language: Annotated[str | None, Header()] = None,
) -> RequestContextResponse:
    """Return the non-sensitive platform context effective for this request."""
    return RequestContextResponse(
        user=user,
        app=_normalize_header(app),
        space=_normalize_header(space),
        language=_normalize_header(language),
    )
