"""Resolve the fixed target and current login for a bound MCP connection."""

from collections.abc import Mapping
from urllib.parse import urlsplit

from httpx import AsyncClient, HTTPStatusError, Request, Response, Timeout
from pydantic_ai.exceptions import ToolFailed

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.request_context import DEFAULT_LANGUAGE
from deep_agent.services.integration_app_access import (
    IntegrationAppAccessError,
    PlatformAppAccessClient,
    app_space,
)
from deepfos_platform.lib.utils import concat_url
from deepfos_platform.options import OPTION as PLATFORM_OPTION

_LOGIN_HEADERS = ("authorization", "cookie", "token", "x-user-token")


def bound_mcp_target(config: Mapping) -> tuple[str, str]:
    url = config.get("url")
    expected = concat_url(str(PLATFORM_OPTION.server.base), "deepfos-mcp/mcp")
    if not isinstance(url, str) or url != expected or not url.startswith("https://"):
        raise IntegrationAppAccessError(422, "绑定应用仅支持当前环境的 DeepFOS HTTPS MCP 网关地址。")
    parsed = urlsplit(url)
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise IntegrationAppAccessError(422, "绑定应用仅支持当前环境的 DeepFOS HTTPS MCP 网关地址。")
    scope = config["request_context"]
    app, space = scope.get("target_app_id"), scope.get("target_space_id")
    if not isinstance(app, str) or app_space(app) != space:
        raise IntegrationAppAccessError(422, "绑定应用与空间不匹配。")
    if config.get("headers"):
        raise IntegrationAppAccessError(422, "绑定应用不能配置固定请求头。")
    return app, space


def bound_login_headers(user: str | None, headers: Mapping[str, str]) -> dict[str, str]:
    normalized = {name.lower(): value for name, value in headers.items()}
    login = {name: normalized[name] for name in _LOGIN_HEADERS if normalized.get(name)}
    if not user or not login or normalized.get("x-deepfos-key"):
        raise IntegrationAppAccessError(401, "绑定 MCP 需要当前用户的平台登录凭据，应用 Key 无法跨应用使用。")
    return {**login, "user": user, "language": normalized.get("language") or DEFAULT_LANGUAGE}


async def bound_mcp_headers(config: Mapping, deps: AgentDeps) -> dict[str, str]:
    try:
        app, space = bound_mcp_target(config)
        if deps.bound_mcp_access is None:
            login = bound_login_headers(deps.user, deps.execution_request_headers or {})
            deps.bound_mcp_access = PlatformAppAccessClient(login)
            deps.bound_mcp_login = login
        await deps.bound_mcp_access.verify_app_access(app, space)
    except IntegrationAppAccessError as exc:
        raise ToolFailed(str(exc)) from exc
    return {**deps.bound_mcp_login, "app": app, "space": space}


def verify_bound_tool_args(config: Mapping, args: Mapping) -> None:
    scope = config["request_context"]
    for key, value in args.items():
        selector = key.lower().replace("_", "")
        if selector in {"app", "appid", "space", "spaceid"}:
            target = scope["target_app_id" if selector.startswith("app") else "target_space_id"]
            if value not in (None, "", target):
                raise ToolFailed("工具参数不能替换 MCP 的绑定应用或空间。")


def restricted_mcp_http_client(url: str, headers: dict[str, str], **kwargs) -> AsyncClient:
    async def check_destination(request: Request) -> None:
        if str(request.url) != url:
            raise ValueError("Bound MCP request attempted to change its destination")

    async def reject_redirect(response: Response) -> None:
        if 300 <= response.status_code < 400:
            raise HTTPStatusError("Bound MCP redirects are forbidden", request=response.request, response=response)

    return AsyncClient(
        headers=headers, follow_redirects=False, timeout=Timeout(30.0, read=300.0),
        event_hooks={"request": [check_destination], "response": [reject_redirect]},
        **kwargs,
    )
