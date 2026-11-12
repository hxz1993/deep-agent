"""Authoritative DeepFOS app ownership checks for integration management."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import Request

from deep_agent.agent.request_context import derive_space_from_app
from deep_agent.security.console_admin import (
    ConsoleAdminAuthenticationError,
    forwarded_platform_auth_headers,
)
from deep_agent.services.scope_names import PlatformScopeDirectoryFetcher, ScopeDirectory, _name
from deepfos_platform.discovery import ServiceDiscovery
from deepfos_platform.discovery.target import normalize_service_target
from deepfos_platform.lib.httpcli import AioHttpCli
from deepfos_platform.lib.utils import concat_url
from deepfos_platform.options import OPTION as PLATFORM_OPTION

_APP_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,49}")
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class IntegrationAppAccessError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ManagedApp:
    app_id: str
    space_id: str
    tenant_id: str
    app_name: str
    tenant_name: str
    space_name: str = ""


def app_space(app_id: str) -> str:
    if not _APP_ID.fullmatch(app_id):
        raise IntegrationAppAccessError(422, "请输入有效的应用 ID。")
    try:
        return derive_space_from_app(app_id)
    except ValueError as exc:
        raise IntegrationAppAccessError(422, "请输入有效的应用 ID。") from exc


@dataclass(frozen=True)
class Enterprise:
    id: str
    name: str


def enterprises_for_user(payload: object) -> dict[str, Enterprise]:
    if not isinstance(payload, list):
        raise IntegrationAppAccessError(503, "平台未返回可验证的企业范围。")
    result: dict[str, Enterprise] = {}
    for item in payload:
        if not isinstance(item, Mapping):
            raise IntegrationAppAccessError(503, "平台未返回可验证的企业范围。")
        code, enterprise_id = item.get("enterpriseCode"), item.get("id")
        if not all(isinstance(value, str) and 0 < len(value) <= 191 for value in (code, enterprise_id)):
            raise IntegrationAppAccessError(503, "平台未返回可验证的企业范围。")
        entry = Enterprise(enterprise_id, _name(item.get("enterpriseName")) or code)
        if code in result and result[code] != entry:
            raise IntegrationAppAccessError(503, "平台返回的企业范围不一致。")
        result[code] = entry
    return result


@dataclass(frozen=True)
class EnterprisePermissions:
    all_apps: bool
    spaces: frozenset[str]
    apps: frozenset[str]

    def allows(self, app: ManagedApp) -> bool:
        return self.all_apps or app.space_id in self.spaces or app.app_id in self.apps


def enterprise_permissions(payload: object) -> EnterprisePermissions:
    if isinstance(payload, Mapping) and isinstance(payload.get("permission"), bool):
        spaces, apps = payload.get("spaceIds"), payload.get("appIds")
        if all(isinstance(items, list) and all(isinstance(item, str) for item in items)
               for items in (spaces, apps)):
            return EnterprisePermissions(payload["permission"], frozenset(spaces), frozenset(apps))
    raise IntegrationAppAccessError(503, "平台未返回可验证的应用权限。")


def can_configure_app(payload: object) -> bool:
    if isinstance(payload, Mapping) and all(
        isinstance(payload.get(key), bool) for key in ("appAdmin", "developerRole")
    ):
        return payload["appAdmin"] or payload["developerRole"]
    raise IntegrationAppAccessError(503, "平台未返回可验证的应用权限。")


def managed_tenants(payload: object, user_id: str) -> dict[str, str]:
    if not isinstance(payload, Mapping) or payload.get("userId") != user_id:
        raise IntegrationAppAccessError(503, "平台未返回可验证的企业管理范围。")
    enterprises = payload.get("enterpriseDTOList")
    if enterprises is None:
        return {}
    if not isinstance(enterprises, list):
        raise IntegrationAppAccessError(503, "平台未返回可验证的企业管理范围。")
    result = {}
    for item in enterprises:
        if not isinstance(item, Mapping):
            continue
        tenant_id = item.get("enterpriseCode")
        if not isinstance(tenant_id, str) or not tenant_id or len(tenant_id) > 191:
            continue
        roles = item.get("roleList")
        if isinstance(roles, list) and any(
            isinstance(role, Mapping) and role.get("roleCode") == "DEFAULT_ADMIN"
            for role in roles
        ):
            result[tenant_id] = _name(item.get("enterpriseName")) or tenant_id
    return result


def active_apps(payload: object) -> dict[str, str]:
    if not isinstance(payload, list):
        raise IntegrationAppAccessError(503, "平台应用目录暂不可用。")
    return {
        item["id"]: _name(item.get("appName")) or item["id"]
        for item in payload
        if isinstance(item, Mapping) and item.get("appType") == "DEEPFOS"
        and isinstance(item.get("id"), str) and item["id"]
    }


class PlatformAppAccessClient:
    """Bounded Platform requests with one trusted identity."""

    def __init__(self, headers: Mapping[str, str]):
        self._headers = dict(headers)
        self._results: dict[tuple, asyncio.Task] = {}
        self._parallel = asyncio.Semaphore(8)

    async def _get(self, service: str, path: str, *, space: str | None = None, app: str | None = None, params=None):
        async with self._parallel:
            return await self._get_platform(service, path, space=space, app=app, params=params)

    async def _get_platform(self, service: str, path: str, *, space=None, app=None, params=None):
        on_success = on_failure = None
        headers = dict(self._headers)
        if space is not None:
            headers["space"] = space
        if app is not None:
            headers["app"] = app
        try:
            async with asyncio.timeout(4):
                target = normalize_service_target(str(getattr(PLATFORM_OPTION.server, service)))
                parsed = urlsplit(target)
                if not target or (parsed.scheme and (
                    parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username or parsed.password or parsed.query or parsed.fragment
                )):
                    raise ValueError("Invalid platform target")
                if parsed.scheme:
                    base_url = target
                else:
                    discovery = ServiceDiscovery.instantiate()
                    if discovery.closed:
                        await ServiceDiscovery.start()
                    on_success, on_failure, base_url = await discovery.get_url(target)
                response = await AioHttpCli.get(
                    concat_url(base_url, path), headers=headers, params=params,
                    allow_redirects=False,
                )
                async with response:
                    if response.status in (401, 403):
                        raise IntegrationAppAccessError(response.status, "平台拒绝了当前请求。")
                    if not 200 <= response.status < 300:
                        raise ValueError("Unexpected platform HTTP status")
                    raw = bytearray()
                    async for chunk in response.content.iter_chunked(16384):
                        raw.extend(chunk)
                        if len(raw) > _MAX_RESPONSE_BYTES:
                            raise ValueError("Platform response too large")
                    payload = json.loads(raw)
                    if isinstance(payload, Mapping) and (
                        "data" in payload or payload.get("status") is False or "statusCode" in payload
                    ):
                        status_code = str(payload.get("statusCode") or payload.get("code") or "")
                        if status_code in {"401", "403"}:
                            raise IntegrationAppAccessError(int(status_code), "平台拒绝了当前请求。")
                        if payload.get("status") is False or status_code not in {"", "200", "0"}:
                            raise ValueError("Platform rejected directory request")
                        payload = payload["data"]
                    if on_success:
                        on_success()
                    return payload
        except IntegrationAppAccessError:
            raise
        except Exception as exc:
            if on_failure:
                on_failure()
            raise IntegrationAppAccessError(503, "平台应用权限暂无法验证，请稍后重试。") from exc

    async def _once(self, service, path, *, space=None, app=None, **params):
        key = service, path, space, app, tuple(params.items())
        if key not in self._results:
            self._results[key] = asyncio.create_task(self._get(service, path, space=space, app=app, params=params))
        return await self._results[key]

    async def verify_app_access(self, app_id: str, space_id: str) -> None:
        access = await self._once("system", "check-permission-by-space-app", app=app_id, space=space_id)
        if not isinstance(access, Mapping) or any(type(access.get(key)) is not bool for key in ("app", "space")):
            raise IntegrationAppAccessError(503, "平台未返回可验证的 App 或 Space 权限。")
        if not (access["app"] and access["space"]):
            raise IntegrationAppAccessError(403, "当前用户无权访问绑定的应用或空间。")


class IntegrationAppDirectory(PlatformAppAccessClient):
    """Reuse authoritative results within a request and batch names by space."""

    def __init__(self, request: Request, user_id: str):
        try:
            headers = forwarded_platform_auth_headers(request, user_id)
        except ConsoleAdminAuthenticationError as exc:
            raise IntegrationAppAccessError(401, "平台身份凭据无效。") from exc
        if not any(headers.get(name) for name in (
            "authorization", "cookie", "token", "x-user-token", "x-deepfos-key",
        )):
            raise IntegrationAppAccessError(401, "请使用平台登录身份验证应用权限。")
        super().__init__({**headers, "language": "zh-cn"})
        self._user_id = user_id

    async def tenants(self) -> dict[str, Enterprise]:
        return enterprises_for_user(await self._once("account", "api/enterprise/list"))

    def _allows_app(self, app_id: str, space_id: str) -> bool:
        return not self._headers.get("x-deepfos-key") or (
            self._headers.get("app"), self._headers.get("space")
        ) == (app_id, space_id)

    async def _app_directories(self, candidates, tenants) -> list[ScopeDirectory]:
        fetch = PlatformScopeDirectoryFetcher()
        if candidates is None:
            # The picker explicitly requests the full catalog.
            async with asyncio.timeout(5):
                return [await fetch(self._headers, timeout_seconds=5)]

        async def load(tenant_code: str) -> ScopeDirectory:
            async with self._parallel:
                return await fetch(self._headers, tenant_code=tenant_code)

        codes = sorted({tenant_code for _, tenant_code in candidates.values()} & tenants.keys())
        async with asyncio.timeout(5):
            results = await asyncio.gather(*(load(code) for code in codes), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return results

    async def available_apps(
        self, *, candidates: Mapping[str, tuple[str, str]] | None = None,
        tenants: Mapping[str, Enterprise] | None = None,
    ) -> list[ManagedApp]:
        if candidates == {}:
            return []
        try:
            if tenants is None and candidates is None:
                tenants, directories = await asyncio.gather(
                    self.tenants(), self._app_directories(None, {}), return_exceptions=True,
                )
                if isinstance(tenants, BaseException):
                    raise tenants
                if not tenants:
                    return []
                if isinstance(directories, BaseException):
                    raise directories
            else:
                if tenants is None:
                    tenants = await self.tenants()
                if not tenants:
                    return []
                directories = await self._app_directories(candidates, tenants)
        except IntegrationAppAccessError:
            raise
        except Exception as exc:
            raise IntegrationAppAccessError(503, "应用目录暂不可用，请稍后重试。") from exc

        apps = {}
        for directory in directories:
            for app_id, (space_id, tenant_id) in directory.app_scopes.items():
                if tenant_id not in tenants or not self._allows_app(app_id, space_id):
                    continue
                if candidates is not None and candidates.get(app_id) != (space_id, tenant_id):
                    continue
                try:
                    if app_space(app_id) != space_id:
                        continue
                except IntegrationAppAccessError:
                    continue
                apps[app_id] = ManagedApp(
                    app_id, space_id, tenant_id, directory.apps.get(app_id, app_id),
                    tenants[tenant_id].name, directory.spaces.get(space_id, ""),
                )

        if self._headers.get("x-deepfos-key"):
            # The bulk endpoint seeds its result with supplied App/Space headers.
            # A scoped key must retain those headers and use the point check.
            return [app for app in apps.values() if await self._can_configure(app.app_id, app.space_id)]
        tenant_codes = sorted({app.tenant_id for app in apps.values()})

        async def load_permissions(code: str) -> EnterprisePermissions:
            return enterprise_permissions(await self._once(
                "system", "user-permission", enterpriseId=tenants[code].id,
            ))

        results = await asyncio.gather(*(load_permissions(code) for code in tenant_codes), return_exceptions=True)
        permissions = {}
        for code, result in zip(tenant_codes, results, strict=True):
            if isinstance(result, BaseException):
                raise result
            permissions[code] = result
        return sorted(
            (app for app in apps.values() if permissions[app.tenant_id].allows(app)),
            key=lambda app: (app.app_name.casefold(), app.app_id),
        )

    async def _can_configure(self, app_id: str, space_id: str) -> bool:
        try:
            payload = await self._once("system", "user/permission", space=space_id, app=app_id)
        except IntegrationAppAccessError as exc:
            if exc.status == 403:
                return False
            raise
        return can_configure_app(payload)

    async def verify(self, app_id: str, *, enterprise_admin_only: bool = False) -> ManagedApp:
        space_id = app_space(app_id)
        denied = "应用不存在或当前账号无权管理。"
        if not self._allows_app(app_id, space_id):
            raise IntegrationAppAccessError(404, denied)
        tenant_id = await self._once("system", "space/get-tenant-code", space=space_id)
        if not isinstance(tenant_id, str) or not tenant_id:
            raise IntegrationAppAccessError(404, denied)
        if enterprise_admin_only:
            # Configuration-wide import/export retains its previous, stricter boundary.
            tenant_names = managed_tenants(
                await self._once("system", "user/get-user-detail", userId=self._user_id), self._user_id,
            )
            tenant_name = tenant_names.get(tenant_id)
        else:
            enterprise = (await self.tenants()).get(tenant_id)
            tenant_name = enterprise.name if enterprise else None
        if not tenant_name:
            raise IntegrationAppAccessError(404, denied)
        apps = active_apps(await self._once("space", "api/space/app/query-app-info-list/by-space-id", space=space_id))
        if app_id not in apps or (not enterprise_admin_only and not await self._can_configure(app_id, space_id)):
            raise IntegrationAppAccessError(404, denied)
        return ManagedApp(app_id, space_id, tenant_id, apps[app_id], tenant_name)
