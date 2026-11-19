from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from contextlib import contextmanager
from ipaddress import ip_address
from time import monotonic
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from deep_agent.agent.request_context import DEFAULT_LANGUAGE
from deep_agent.diagnostics import emit
from deep_agent.security.console_admin import (
    ConsoleAdminAuthenticationError, ConsoleAdminUnavailableError, PlatformEnterpriseAdminChecker,
    forwarded_platform_auth_headers,
)
from deep_agent.services.integration_app_access import (
    IntegrationAppAccessError, PlatformAppAccessClient, app_space,
)

_CREDENTIALS = ("authorization", "token", "x-user-token", "x-deepfos-key")
_SERVICE_PREFIX = "/deep-agent-server1-0"
_PLATFORM_GATEWAY = "/deepfos-server"
_PLATFORM_PREFIX = _PLATFORM_GATEWAY + _SERVICE_PREFIX
_PLATFORM_ADDRESS_TTL = 300


@contextmanager
def observe_access(protocol: str, publication_id: str | None = None, *, phase: str | None = None):
    started = monotonic()
    try:
        yield
    except HTTPException as exc:
        emit("published_agent_access_failed", level="WARNING", protocol=protocol,
             publication_id=publication_id, http_status=exc.status_code, phase=phase,
             exception=exc if exc.status_code >= 500 else None,
             duration_ms=round((monotonic() - started) * 1000))
        raise


def _gateway_values(request: Request, name: str) -> list[str]:
    # Use the metadata appended by the last gateway hop. The ingress must
    # discard client-supplied forwarding headers before proxying a request.
    return [value.strip() for header in request.headers.getlist(name) for value in header.split(",")]


def service_base_url(request: Request) -> str | None:
    prefix = request.scope.get("root_path", "").rstrip("/")
    scheme = request.scope["scheme"]
    hosts = request.headers.getlist("host")
    if len(hosts) != 1:
        return None
    authority = hosts[0]

    prefixes = [value.rstrip("/") for value in _gateway_values(request, "x-forwarded-prefix")]
    if prefixes and prefixes[-1] in {_SERVICE_PREFIX, _PLATFORM_GATEWAY, _PLATFORM_PREFIX}:
        forwarded_prefix = prefixes[-1]
        if forwarded_prefix == _PLATFORM_GATEWAY or prefixes[-2:] == [_PLATFORM_GATEWAY, _SERVICE_PREFIX]:
            forwarded_prefix = _PLATFORM_PREFIX
        if not prefix or prefix == forwarded_prefix:
            prefix = forwarded_prefix
            scheme = (_gateway_values(request, "x-forwarded-proto") or [scheme])[-1]
            authority = (_gateway_values(request, "x-forwarded-host") or [authority])[-1]

    return _safe_service_address(scheme, authority, prefix)


def _safe_service_address(scheme: str, authority: str, prefix: str) -> str | None:
    if scheme not in {"http", "https"} or not re.fullmatch(r"(?:/[A-Za-z0-9_.~-]+)*", prefix):
        return None
    if any(segment in {".", ".."} for segment in prefix.split("/")):
        return None
    if not re.fullmatch(r"(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\])(?::[0-9]{1,5})?", authority):
        return None
    try:
        address = urlsplit(f"{scheme}://{authority}")
        port = address.port
        host = address.hostname
        private = host == "localhost"
        if scheme == "http" and not private:
            private = ip_address(host).is_private
    except ValueError:
        return None
    if scheme == "http" and not private:
        return None
    if port == 0:
        return None
    host = f"[{host}]" if ":" in host else host
    if port and port != (443 if scheme == "https" else 80):
        host = f"{host}:{port}"
    return f"{scheme}://{host}{prefix}"


def needs_platform_address(request: Request, base: str | None) -> bool:
    if base is None or request.scope.get("root_path"):
        return False
    parsed = urlsplit(base)
    if parsed.scheme != "http" or parsed.hostname == "localhost":
        return False
    # The normalized URL only permits IP literals (or localhost) over HTTP.
    return not ip_address(parsed.hostname).is_loopback


def _platform_service_address(value: object) -> str | None:
    if not isinstance(value, str) or not value or any(ch.isspace() for ch in value.strip()):
        return None
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return None
    if parsed.query or parsed.fragment or parsed.path.rstrip("/") not in {"", _PLATFORM_GATEWAY}:
        return None
    return _safe_service_address(parsed.scheme, parsed.netloc, _PLATFORM_PREFIX)


class AgentServiceAddress:
    """Cache the canonical platform route when the gateway rewrites Host."""

    def __init__(self):
        self._base: str | None = None
        self._expires = 0.0
        self._lock = asyncio.Lock()

    async def resolve(self, request: Request, base: str | None, headers: Mapping[str, str]) -> str | None:
        if not needs_platform_address(request, base):
            return base
        if self._expires > monotonic():
            return self._base
        async with self._lock:
            if self._expires > monotonic():
                return self._base
            try:
                result = await PlatformAppAccessClient(headers)._get("system", "server-config-params")
            except IntegrationAppAccessError:
                return None
            address = _platform_service_address(result.get("EXPORT_ENV_REALM_NAME")) if isinstance(result, Mapping) else None
            if address:
                self._base = address
                self._expires = monotonic() + _PLATFORM_ADDRESS_TTL
            return address

    async def for_admin(self, request: Request, user: str) -> str | None:
        base = service_base_url(request)
        if not needs_platform_address(request, base):
            return base
        try:
            headers = forwarded_platform_auth_headers(request, user)
        except ConsoleAdminAuthenticationError:
            return None
        return await self.resolve(request, base, headers)


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
