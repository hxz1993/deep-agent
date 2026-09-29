from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from loguru import logger

from deep_agent.api.v1.request_identity import RequestUserDep
from deep_agent.core.context import AppContextDep
from deep_agent.schemas.admin import ConsoleAdminAccessResponse
from deep_agent.security import (
    ConsoleAdminAccess,
    ConsoleAdminAuthenticationError,
    ConsoleAdminUnavailableError,
    ENTERPRISE_ADMIN_ROLE,
    LOCAL_CONSOLE_ADMIN_SCOPE_KEY,
)

router = APIRouter()


async def resolve_console_admin_access(
    request: Request,
    user: RequestUserDep,
    ctx: AppContextDep,
) -> ConsoleAdminAccess:
    # Only the loopback-only local_dev ASGI adapter can set this internal scope
    # marker. It is deliberately not an HTTP header or a production option.
    if request.scope.get(LOCAL_CONSOLE_ADMIN_SCOPE_KEY) is True:
        return ConsoleAdminAccess(
            allowed=True,
            roles=(ENTERPRISE_ADMIN_ROLE,),
        )
    try:
        return await ctx.console_admin_authorizer.access_for(request, user)
    except ConsoleAdminAuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="平台身份凭据无效或已失效，请检查鉴权信息",
        ) from exc
    except ConsoleAdminUnavailableError as exc:
        logger.warning('暂时无法验证企业管理员权限')
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="暂时无法验证企业管理员权限，请稍后重试",
        ) from exc


async def require_console_admin(
    access: Annotated[ConsoleAdminAccess, Depends(resolve_console_admin_access)],
) -> ConsoleAdminAccess:
    if not access.allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="当前账号没有DeepAgent控制台管理权限",
        )
    return access


CONSOLE_ADMIN_DEPENDENCIES = (Depends(require_console_admin),)


@router.get("/admin/access", response_model=ConsoleAdminAccessResponse)
async def get_console_admin_access(
    access: Annotated[ConsoleAdminAccess, Depends(resolve_console_admin_access)],
) -> ConsoleAdminAccessResponse:
    return ConsoleAdminAccessResponse(
        allowed=access.allowed,
        roles=list(access.roles),
    )


__all__ = [
    "CONSOLE_ADMIN_DEPENDENCIES",
    "get_console_admin_access",
    "require_console_admin",
    "resolve_console_admin_access",
    "router",
]
