from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from loguru import logger
from pydantic import BaseModel

from deep_agent.api.v1.request_identity import RequestUserDep
from deep_agent.security.current_user import (
    CurrentUserAuthenticationError,
    CurrentUserProfileResolver,
    CurrentUserUnavailableError,
)

router = APIRouter()


class CurrentUserResponse(BaseModel):
    user_id: str
    username: str


_CURRENT_USER_PROFILE_RESOLVER = CurrentUserProfileResolver()


def get_current_user_profile_resolver() -> CurrentUserProfileResolver:
    return _CURRENT_USER_PROFILE_RESOLVER


CurrentUserProfileResolverDep = Annotated[
    CurrentUserProfileResolver,
    Depends(get_current_user_profile_resolver),
]


@router.get("/current-user", response_model=CurrentUserResponse)
async def get_current_user(
    request: Request,
    user: RequestUserDep,
    resolver: CurrentUserProfileResolverDep,
) -> CurrentUserResponse:
    """Return the signed-in user's non-sensitive account display data."""
    try:
        profile = await resolver.profile_for(request, user)
    except CurrentUserAuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="平台登录状态已失效，请重新登录",
        ) from exc
    except CurrentUserUnavailableError as exc:
        logger.warning("暂时无法读取当前用户名")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="暂时无法读取当前用户名，请稍后重试",
        ) from exc
    return CurrentUserResponse(
        user_id=profile.user_id,
        username=profile.username,
    )


__all__ = [
    "CurrentUserResponse",
    "get_current_user",
    "get_current_user_profile_resolver",
    "router",
]
