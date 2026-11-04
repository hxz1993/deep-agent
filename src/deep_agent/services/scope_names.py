"""Best-effort current-environment App/Space display names, never session facts.

One caller-authenticated directory request enriches an entire response. Directory
failures leave names absent; they cannot hide a database/query failure or grant
access to any session. No credentials or platform URLs are returned to clients.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import urlsplit

import aiohttp
from fastapi import Request

from deepfos_platform.discovery import ServiceDiscovery
from deepfos_platform.discovery.target import normalize_service_target
from deepfos_platform.lib.httpcli import AioHttpCli
from deepfos_platform.lib.utils import concat_url
from deepfos_platform.options import OPTION as PLATFORM_OPTION

from deep_agent.security.console_admin import (
    ConsoleAdminAuthenticationError,
    forwarded_platform_auth_headers,
    platform_credential_digest,
)

_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_DIRECTORY_ITEMS = 20_000
_NAME_LANGUAGE = "zh-cn"


@dataclass(frozen=True)
class ScopeDirectory:
    apps: dict[str, str] = field(default_factory=dict)
    spaces: dict[str, str] = field(default_factory=dict)

    def copy(self) -> ScopeDirectory:
        # Cached maps must not be mutated by an API response's enrichment.
        return ScopeDirectory(apps=dict(self.apps), spaces=dict(self.spaces))


class ScopeDirectoryUnavailableError(RuntimeError):
    """The optional display directory could not be read."""


def _text(value: object, max_length: int = 256) -> str:
    return value.strip()[:max_length] if isinstance(value, str) else ""


def _name(value: object) -> str:
    """Accept plain platform names and Chinese entries in multilingual names."""
    if isinstance(value, str):
        plain = value.strip()
        if plain.startswith("{"):
            try:
                return _name(json.loads(plain))
            except (ValueError, TypeError):
                return ""
        return plain[:256]
    if isinstance(value, Mapping):
        languages = {str(key).lower().replace("_", "-"): item for key, item in value.items()}
        for language in ("zh-cn", "zh", "cn"):
            result = _text(languages.get(language))
            if result:
                return result
    return ""


def normalize_scope_directory(payload: object) -> ScopeDirectory:
    if not isinstance(payload, Mapping):
        raise ScopeDirectoryUnavailableError("invalid scope directory")
    if payload.get("status") is False or str(payload.get("code", payload.get("statusCode", ""))) in {"401", "403"}:
        raise ScopeDirectoryUnavailableError("scope directory unavailable")
    if "data" in payload and any(key in payload for key in ("status", "code", "message", "statusCode")):
        payload = payload["data"]
    if not isinstance(payload, Mapping):
        raise ScopeDirectoryUnavailableError("invalid scope directory")
    directory = ScopeDirectory()
    item_count = 0
    for spaces in payload.values():
        if not isinstance(spaces, list):
            raise ScopeDirectoryUnavailableError("invalid scope directory")
        for space in spaces:
            item_count += 1
            if item_count > _MAX_DIRECTORY_ITEMS:
                raise ScopeDirectoryUnavailableError("scope directory is too large")
            if not isinstance(space, Mapping):
                continue
            space_id, space_name = _text(space.get("space")), _name(space.get("spaceName"))
            if space_id and space_name:
                directory.spaces[space_id] = space_name
            apps = space.get("appList")
            if not isinstance(apps, list):
                continue
            for app in apps:
                item_count += 1
                if item_count > _MAX_DIRECTORY_ITEMS:
                    raise ScopeDirectoryUnavailableError("scope directory is too large")
                if not isinstance(app, Mapping):
                    continue
                app_id, app_name = _text(app.get("app")), _name(app.get("appName"))
                if app_id and app_name:
                    directory.apps[app_id] = app_name
    return directory


class PlatformScopeDirectoryFetcher:
    """Call System's existing read-only POST with only the current caller auth."""

    def __init__(self, *, system_service: str | None = None) -> None:
        self._service = normalize_service_target(str(
            PLATFORM_OPTION.server.system if system_service is None else system_service
        ))
        self.cache_namespace = hashlib.sha256(self._service.encode()).hexdigest()

    async def _resolve_url(self) -> tuple[str, Callable[[], None] | None, Callable[[], None] | None]:
        if not self._service:
            raise ScopeDirectoryUnavailableError("System service is not configured")
        parsed = urlsplit(self._service)
        if parsed.scheme:
            if (parsed.scheme not in {"http", "https"} or not parsed.netloc
                    or parsed.username or parsed.password or parsed.query or parsed.fragment):
                raise ScopeDirectoryUnavailableError("invalid System service URL")
            return concat_url(self._service, "space-app-list"), None, None
        discovery = ServiceDiscovery.instantiate()
        if discovery.closed:
            await ServiceDiscovery.start()
        on_success, on_failure, base_url = await discovery.get_url(self._service)
        return concat_url(base_url, "space-app-list"), on_success, on_failure

    async def __call__(self, headers: Mapping[str, str]) -> ScopeDirectory:
        response = None
        on_failure = None
        try:
            url, on_success, on_failure = await self._resolve_url()
            response = await AioHttpCli.post(
                url, params={"enableSpecialGroup": "false"}, headers=dict(headers),
                json=[],
                timeout=aiohttp.ClientTimeout(connect=2.0, total=2.0), allow_redirects=False,
            )
            if not 200 <= response.status < 300:
                raise ScopeDirectoryUnavailableError("scope directory unavailable")
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.content.iter_chunked(16_384):
                size += len(chunk)
                if size > _MAX_RESPONSE_BYTES:
                    raise ScopeDirectoryUnavailableError("scope directory is too large")
                chunks.append(chunk)
            directory = normalize_scope_directory(json.loads(b"".join(chunks)))
            if on_success is not None:
                on_success()
            return directory
        except (Exception, asyncio.CancelledError):
            if on_failure is not None:
                on_failure()
            raise
        finally:
            if response is not None:
                response.release()


ScopeDirectoryFetcher = Callable[[Mapping[str, str]], Awaitable[ScopeDirectory]]
_CacheKey = tuple[str, str, str, str, str]


class ScopeNameResolver:
    """Five-minute bounded cache, caller isolation and coalesced overall deadline."""

    def __init__(
        self, fetcher: ScopeDirectoryFetcher | None = None, *,
        deployment_scope: str | None = None, cache_ttl_seconds: float = 300.0,
        cache_max_entries: int = 256, timeout_seconds: float = 2.0,
        failure_backoff_seconds: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetcher = fetcher or PlatformScopeDirectoryFetcher()
        namespace = getattr(self._fetcher, "cache_namespace", "injected")
        self._scope = deployment_scope or str(namespace)
        self._ttl = min(300.0, max(0.0, cache_ttl_seconds))
        self._limit = min(256, max(0, cache_max_entries))
        self._timeout = min(2.0, max(0.001, timeout_seconds))
        self._failure_backoff = min(10.0, max(0.0, failure_backoff_seconds))
        self._clock = clock
        self._cache: OrderedDict[_CacheKey, tuple[float, ScopeDirectory]] = OrderedDict()
        self._failures: OrderedDict[_CacheKey, float] = OrderedDict()
        self._inflight: dict[tuple[asyncio.AbstractEventLoop, _CacheKey], asyncio.Task[ScopeDirectory]] = {}

    async def directory_for(self, request: Request, user: str) -> ScopeDirectory:
        try:
            headers = forwarded_platform_auth_headers(request, user)
        except ConsoleAdminAuthenticationError:
            return ScopeDirectory()
        if not user or not any(headers.get(name) for name in (
            "authorization", "cookie", "token", "x-user-token", "x-deepfos-key",
        )):
            return ScopeDirectory()
        requested_language = _text(request.headers.get("x-deep-agent-language") or request.headers.get("language")) or _NAME_LANGUAGE
        headers["language"] = _NAME_LANGUAGE
        key = (self._scope, str(request.base_url), user, platform_credential_digest(headers), requested_language)
        now = self._clock()
        for expired_key, (expires_at, _) in tuple(self._cache.items()):
            if expires_at <= now:
                self._cache.pop(expired_key, None)
        for expired_key, expires_at in tuple(self._failures.items()):
            if expires_at <= now:
                self._failures.pop(expired_key, None)
        cached = self._cache.get(key)
        if cached:
            self._cache.move_to_end(key)
            return cached[1].copy()
        if key in self._failures:
            return ScopeDirectory()
        inflight_key = (asyncio.get_running_loop(), key)
        task = self._inflight.get(inflight_key)
        if task is None:
            # Bound pending catalogs as well as completed cache entries.
            if len(self._inflight) >= 256:
                return ScopeDirectory()
            task = asyncio.create_task(self._load(key, headers))
            self._inflight[inflight_key] = task

            def finish(done: asyncio.Task[ScopeDirectory]) -> None:
                self._inflight.pop(inflight_key, None)
                if not done.cancelled():
                    done.exception()

            task.add_done_callback(finish)
        # One disconnected reader must not cancel a shared catalog lookup.
        return (await asyncio.shield(task)).copy()

    async def _load(self, key: _CacheKey, headers: Mapping[str, str]) -> ScopeDirectory:
        try:
            # This includes discovery, response transfer and decoding.
            async with asyncio.timeout(self._timeout):
                result = await self._fetcher(headers)
                if not isinstance(result, ScopeDirectory):
                    raise ScopeDirectoryUnavailableError("invalid scope directory")
        except Exception:
            # Optional names never turn a successful facts query into an error.
            # Deliberately do not log upstream exception text or response bodies.
            if self._failure_backoff and self._limit:
                self._failures[key] = self._clock() + self._failure_backoff
                self._failures.move_to_end(key)
                while len(self._failures) > self._limit:
                    self._failures.popitem(last=False)
            return ScopeDirectory()
        self._failures.pop(key, None)
        if self._ttl and self._limit:
            self._cache[key] = (self._clock() + self._ttl, result.copy())
            self._cache.move_to_end(key)
            while len(self._cache) > self._limit:
                self._cache.popitem(last=False)
        return result


@lru_cache(maxsize=1)
def _environment_resolver(system_service: str, deployment_scope: str) -> ScopeNameResolver:
    return ScopeNameResolver(
        PlatformScopeDirectoryFetcher(system_service=system_service), deployment_scope=deployment_scope,
    )


async def resolve_scope_directory(request: Request, user: str) -> ScopeDirectory:
    # Options may be loaded after API imports. Bind lazily, and never share names
    # when a process's discovery deployment is explicitly reconfigured.
    if not user or not any(request.headers.get(name, "").strip() for name in (
        "authorization", "cookie", "token", "x-user-token", "x-deepfos-key",
    )):
        return ScopeDirectory()
    try:
        service = str(PLATFORM_OPTION.server.system)
        discovery = str(PLATFORM_OPTION.srvdis.implementation)
        environment = [service, discovery, str(PLATFORM_OPTION.k8s.namespace), str(PLATFORM_OPTION.general.system_env)]
        if not urlsplit(normalize_service_target(service)).scheme:
            if discovery == "eureka":
                environment.append(str(PLATFORM_OPTION.eureka.server))
            elif discovery == "nacos":
                environment.extend(str(getattr(PLATFORM_OPTION.nacos, field)) for field in ("server", "namespace", "group"))
            elif discovery == "local":
                environment.append(str(PLATFORM_OPTION.server.base))
        scope = hashlib.sha256(json.dumps(environment).encode()).hexdigest()
        return await _environment_resolver(service, scope).directory_for(request, user)
    except Exception:
        return ScopeDirectory()


__all__ = ["ScopeDirectory", "ScopeNameResolver", "PlatformScopeDirectoryFetcher", "resolve_scope_directory"]
