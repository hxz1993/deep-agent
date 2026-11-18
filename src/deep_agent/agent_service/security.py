from __future__ import annotations

import re
from contextlib import contextmanager
from time import monotonic
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from deep_agent.agent.request_context import DEFAULT_LANGUAGE
from deep_agent.diagnostics import emit
from deep_agent.security.console_admin import ConsoleAdminAuthenticationError, ConsoleAdminUnavailableError, PlatformEnterpriseAdminChecker
from deep_agent.services.integration_app_access import (
    IntegrationAppAccessError, PlatformAppAccessClient, app_space,
)

_CREDENTIALS = ("authorization", "token", "x-user-token", "x-deepfos-key")


@contextmanager
def observe_access(protocol: str, publication_id: str | None = None):
    started = monotonic()
    try:
        yield
    except HTTPException as exc:
        emit("published_agent_access_failed", level="WARNING", protocol=protocol,
             publication_id=publication_id, http_status=exc.status_code,
             exception=exc if exc.status_code >= 500 else None,
             duration_ms=round((monotonic() - started) * 1000))
        raise


def public_base_url(value: str) -> str:
    parsed = urlsplit(value)
    loopback = parsed.hostname in {"127.0.0.1", "::1", "localhost"}
    if (parsed.scheme not in {"https", "http"} or (parsed.scheme == "http" and not loopback)
            or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or any(ch.isspace() for ch in value)):
        raise ValueError("Agent Service public URL must use HTTPS or loopback HTTP")
    return value.rstrip("/")


async def platform_caller(request: Request, *, path_app: str | None = None, require_app: bool = True) -> dict[str, str]:
    headers = {}
    header_names = (*_CREDENTIALS, "language") if path_app is not None else (*_CREDENTIALS, "language", "app", "space")
    for name in header_names:
        values = request.headers.getlist(name)
        if len(values) > 1:
            raise HTTPException(400, f"{name} Header 重复，请只发送一次。")
        if any(len(value) > 8192 or any(ord(ch) < 32 or ord(ch) == 127 for ch in value) for value in values):
            raise HTTPException(400, f"{name} Header 格式无效。")
        if values and values[0].strip():
            headers[name] = values[0].strip()
    app = path_app if path_app is not None else headers.get("app")
    if not app and (require_app or "x-deepfos-key" in headers):
        raise HTTPException(400, "请在 app Header 中传入 App ID，或使用指定 App 的接入地址。")
    space = None
    if app:
        try:
            space = app_space(app)
        except IntegrationAppAccessError as exc:
            source = "接入地址中的 App ID" if path_app is not None else "app Header"
            raise HTTPException(400, f"{source}无效。") from exc
        if "space" in headers and headers["space"] != space:
            raise HTTPException(400, "space Header 与 App 所属空间不一致，请更正或省略 space。")
        headers.update(app=app, space=space)
    elif "space" in headers:
        raise HTTPException(400, "请与 app 一起发送 space Header")
    credentials = [name for name in _CREDENTIALS if name in headers]
    if not credentials:
        raise HTTPException(401, "缺少调用凭据。请通过 Authorization: Bearer <Token> 或 X-Deepfos-Key 提供一种凭据。")
    if len(credentials) > 1:
        raise HTTPException(401, "收到多种调用凭据。请仅保留平台 Token 或 DeepFOS API Key。")
    name = credentials[0]
    if name == "authorization":
        match = re.fullmatch(r"Bearer ([^ ,]+)", headers[name], flags=re.I)
        if not match:
            raise HTTPException(401, "Authorization Header 格式无效，请使用 Bearer <Token>。")
        headers["token"] = match[1]
        del headers[name]
    elif name == "x-user-token":
        headers["token"] = headers.pop(name)
    headers.setdefault("language", DEFAULT_LANGUAGE)
    try:
        key = headers.get("x-deepfos-key")
        if key:
            user = await PlatformEnterpriseAdminChecker().resolve_api_key_user(key, app, space)
        else:
            # The account service resolves the actual owner from the credential.
            # An untrusted inbound user header never reaches this request.
            profile = await PlatformAppAccessClient(
                {"token": headers["token"], "language": headers["language"]},
            )._get("account", "api/user/full-detail")
            ids = {str(profile[key]).strip() for key in ("id", "userId")
                   if isinstance(profile, dict) and profile.get(key) is not None}
            if len(ids) != 1 or not re.fullmatch(r"[A-Za-z0-9_.@:-]{1,128}", next(iter(ids))):
                raise HTTPException(401, "Platform did not verify the caller identity")
            user = ids.pop()
        headers["user"] = user
        if app and space:
            await PlatformAppAccessClient(headers).verify_app_access(app, space)
    except IntegrationAppAccessError as exc:
        if exc.status == 401:
            raise HTTPException(401, "平台无法验证调用凭据，请检查凭据是否有效。") from exc
        raise HTTPException(exc.status, str(exc)) from exc
    except ConsoleAdminAuthenticationError as exc:
        raise HTTPException(401, "平台拒绝了 API Key，请检查 Key 是否有效并已授权访问目标应用。") from exc
    except ConsoleAdminUnavailableError as exc:
        raise HTTPException(503, "Platform authentication is temporarily unavailable") from exc
    return headers
