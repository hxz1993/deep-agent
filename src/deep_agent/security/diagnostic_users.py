"""Bounded, best-effort platform account display data for admin diagnostics.

The caller has already passed the Console administrator dependency. Target IDs
are data, never identities: all account requests use the administrator's login.
This module does not require a test App/Space or expose a directory-search API.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import TypedDict
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

import aiohttp
from fastapi import Request

from deepfos_platform.discovery import ServiceDiscovery
from deepfos_platform.lib.httpcli import AioHttpCli
from deepfos_platform.lib.utils import concat_url
from deepfos_platform.options import OPTION as PLATFORM_OPTION

from deep_agent.local_security import (
    LOCAL_CONSOLE_ADMIN_SCOPE_KEY,
    LOCAL_CURRENT_USERNAME_SCOPE_KEY,
)
from deep_agent.security.console_admin import (
    forwarded_platform_auth_headers,
    platform_credential_digest,
)

_BATCH_SIZE = 100
_MAX_RESPONSE_BYTES = 512 * 1024
_AUTH_QUERY_NAMES = frozenset({
    "token", "accesstoken", "authtoken", "authorization", "auth", "apptoken",
    "key", "apikey", "secret", "password", "cookie", "credential", "signature", "sig",
})


class DiagnosticUserAuthenticationError(RuntimeError):
    """Account lookup rejected authentication; do not disguise it as missing data."""

    def __init__(self, status_code: int = 401) -> None:
        self.status_code = 403 if status_code == 403 else 401
        super().__init__("platform account authorization was rejected")


class DiagnosticUserUnavailableError(RuntimeError):
    """Display enrichment is unavailable, without affecting session facts."""


class DiagnosticUserProfile(TypedDict):
    id: str
    user_id: str
    display_name: str
    email: str | None
    avatar_url: str | None
    resolution: str


DiagnosticUserFetcher = Callable[
    [tuple[str, ...], Mapping[str, str]],
    Awaitable[dict[str, DiagnosticUserProfile]],
]


def _text(value: object, limit: int = 256) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def safe_avatar_url(value: object) -> str | None:
    """Allow plain HTTP(S)/same-origin images, but never credential-bearing URLs."""
    candidate = _text(value, 2049)
    if not candidate or len(candidate) > 2048:
        return None
    decoded = unquote(candidate)
    if "\\" in decoded or any(ord(char) < 32 or ord(char) == 127 for char in decoded):
        return None
    try:
        parsed = urlsplit(candidate)
        if parsed.username or parsed.password:
            return None
        if parsed.scheme:
            if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
                return None
            # Accessing port validates malformed ports without making a request.
            parsed.port
        elif not candidate.startswith("/") or candidate.startswith("//"):
            return None
        for name, _ in parse_qsl(parsed.query, keep_blank_values=True):
            normalized = "".join(char for char in name.lower() if char.isalnum())
            if (normalized in _AUTH_QUERY_NAMES or normalized.startswith("xamz")
                    or any(word in normalized for word in (
                        "token", "secret", "password", "credential", "signature", "authorization",
                    ))):
                return None
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
    except ValueError:
        return None


def _fallback(user_id: str, resolution: str = "unavailable") -> DiagnosticUserProfile:
    return {
        "id": user_id, "user_id": user_id, "display_name": user_id,
        "email": None, "avatar_url": None, "resolution": resolution,
    }


def _normalize_profiles(
    payload: object, requested_ids: tuple[str, ...],
) -> dict[str, DiagnosticUserProfile]:
    if isinstance(payload, Mapping):
        code = str(payload.get("code") or payload.get("statusCode") or "")
        if code in {"401", "403"}:
            raise DiagnosticUserAuthenticationError(int(code))
        if payload.get("status") is False:
            raise DiagnosticUserUnavailableError("account lookup was unsuccessful")
        payload = payload.get("data")
    if not isinstance(payload, list):
        raise DiagnosticUserUnavailableError("account lookup returned malformed data")
    allowed = set(requested_ids)
    result: dict[str, DiagnosticUserProfile] = {}
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        user_id = _text(item.get("userId") or item.get("id") or item.get("user_id"))
        if user_id not in allowed:
            continue
        display_name = (
            _text(item.get("nickName")) or _text(item.get("nickname"))
            or _text(item.get("userName")) or _text(item.get("username")) or user_id
        )
        result[user_id] = {
            "id": user_id, "user_id": user_id, "display_name": display_name,
            "email": _text(item.get("email"), 320) or None,
            "avatar_url": safe_avatar_url(item.get("avatar")),
            "resolution": "resolved",
        }
    return result


class PlatformDiagnosticUserFetcher:
    """Adapter for the platform account service's existing batch endpoint."""

    def __init__(self, *, account_service: str | None = None) -> None:
        self._account_service = str(
            PLATFORM_OPTION.server.account if account_service is None else account_service
        ).strip()
        # The namespace is deployment-specific; never retain a configured secret as a key.
        self.cache_namespace = hashlib.sha256(self._account_service.encode()).hexdigest()

    async def _resolve_url(
        self,
    ) -> tuple[str, Callable[[], None] | None, Callable[[], None] | None]:
        target = self._account_service
        if not target:
            raise DiagnosticUserUnavailableError("account service is not configured")
        parsed = urlsplit(target)
        if parsed.scheme:
            if (parsed.scheme not in {"http", "https"} or not parsed.netloc
                    or parsed.username or parsed.password or parsed.query or parsed.fragment):
                raise DiagnosticUserUnavailableError("account service URL is invalid")
            return concat_url(target, "api", "user", "info", "by-user-ids"), None, None
        try:
            discovery = ServiceDiscovery.instantiate()
            if discovery.closed:
                await ServiceDiscovery.start()
            on_success, on_failure, base_url = await discovery.get_url(target)
        except Exception as exc:
            raise DiagnosticUserUnavailableError("account service is unavailable") from exc
        return concat_url(base_url, "api", "user", "info", "by-user-ids"), on_success, on_failure

    async def __call__(
        self, user_ids: tuple[str, ...], headers: Mapping[str, str],
    ) -> dict[str, DiagnosticUserProfile]:
        if not user_ids or len(user_ids) > _BATCH_SIZE:
            raise ValueError("account lookup batch must contain 1 to 100 user IDs")
        response = None
        on_failure: Callable[[], None] | None = None
        try:
            url, on_success, on_failure = await self._resolve_url()
            response = await AioHttpCli.post(
                url, json=list(user_ids), headers=dict(headers),
                timeout=aiohttp.ClientTimeout(connect=2.0, total=2.0),
                allow_redirects=False,
            )
            if response.status in {401, 403}:
                raise DiagnosticUserAuthenticationError(response.status)
            if not 200 <= response.status < 300:
                raise DiagnosticUserUnavailableError("account lookup returned an error")
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.content.iter_chunked(16384):
                size += len(chunk)
                if size > _MAX_RESPONSE_BYTES:
                    raise DiagnosticUserUnavailableError("account lookup response is too large")
                chunks.append(chunk)
            result = _normalize_profiles(json.loads(b"".join(chunks)), user_ids)
            if on_success is not None:
                on_success()
            return result
        except DiagnosticUserAuthenticationError:
            raise
        except Exception as exc:
            if on_failure is not None:
                on_failure()
            if isinstance(exc, DiagnosticUserUnavailableError):
                raise
            raise DiagnosticUserUnavailableError("account lookup is unavailable") from exc
        finally:
            if response is not None:
                response.release()


class DiagnosticUserProfileResolver:
    """Enrich only supplied accounts; bounded LRU cache and one overall deadline."""

    def __init__(
        self, fetcher: DiagnosticUserFetcher | None = None, *,
        cache_ttl_seconds: float = 30.0, cache_max_entries: int = 2048,
        timeout_seconds: float = 2.0, deployment_scope: str | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetcher = fetcher or PlatformDiagnosticUserFetcher()
        namespace = getattr(self._fetcher, "cache_namespace", None)
        self._scope = deployment_scope or (namespace if isinstance(namespace, str) else "injected")
        self._ttl = min(60.0, max(0.0, cache_ttl_seconds))
        self._max_entries = min(2048, max(0, cache_max_entries))
        self._timeout = min(2.0, max(0.001, timeout_seconds))
        self._clock = clock
        self._cache: OrderedDict[
            tuple[str, str, str, str, str], tuple[float, DiagnosticUserProfile]
        ] = OrderedDict()

    def clear(self) -> None:
        self._cache.clear()

    async def profiles_for(
        self, request: Request, caller_user_id: str, user_ids: Iterable[str],
    ) -> dict[str, DiagnosticUserProfile]:
        ids = tuple(dict.fromkeys(value.strip() for value in user_ids if isinstance(value, str) and value.strip()))
        if not ids:
            return {}
        caller = caller_user_id.strip()
        if not caller:
            raise DiagnosticUserAuthenticationError()
        result = {user_id: _fallback(user_id) for user_id in ids}
        if request.scope.get(LOCAL_CONSOLE_ADMIN_SCOPE_KEY) is True:
            # Only trusted local_dev scope enables this. A user-supplied header cannot.
            local_name = _text(request.scope.get(LOCAL_CURRENT_USERNAME_SCOPE_KEY))
            if caller in result and local_name:
                result[caller] = {**_fallback(caller, "local"), "display_name": local_name}
            return result

        headers = forwarded_platform_auth_headers(request, caller)
        scope = (str(self._scope), str(request.base_url), caller, platform_credential_digest(headers))
        now = self._clock()
        for key, (expires_at, _) in tuple(self._cache.items()):
            if expires_at <= now:
                self._cache.pop(key, None)
        missing: list[str] = []
        for user_id in ids:
            key = (*scope, user_id)
            cached = self._cache.get(key)
            if cached is None:
                missing.append(user_id)
            else:
                result[user_id] = dict(cached[1])
                self._cache.move_to_end(key)
        try:
            # Includes service discovery and every batch. No per-batch 2-second reset.
            async with asyncio.timeout(self._timeout):
                for start in range(0, len(missing), _BATCH_SIZE):
                    batch = tuple(missing[start:start + _BATCH_SIZE])
                    profiles = await self._fetcher(batch, headers)
                    if not isinstance(profiles, Mapping):
                        raise DiagnosticUserUnavailableError("account lookup returned malformed data")
                    for user_id in batch:
                        profile = profiles.get(user_id) or _fallback(user_id, "missing")
                        result[user_id] = dict(profile)
                        if self._ttl and self._max_entries:
                            key = (*scope, user_id)
                            self._cache[key] = (self._clock() + self._ttl, dict(profile))
                            self._cache.move_to_end(key)
                            while len(self._cache) > self._max_entries:
                                self._cache.popitem(last=False)
        except DiagnosticUserAuthenticationError:
            # A rejected login must not leave old display data reusable for this caller.
            for key in tuple(self._cache):
                if key[:3] == scope[:3]:
                    self._cache.pop(key, None)
            raise
        except Exception:
            # Availability affects display only. Never return upstream text or credentials.
            pass
        return result


_RESOLVER = DiagnosticUserProfileResolver()


async def resolve_diagnostic_users(
    request: Request, caller_user_id: str, user_ids: Iterable[str],
) -> dict[str, DiagnosticUserProfile]:
    return await _RESOLVER.profiles_for(request, caller_user_id, user_ids)


__all__ = [
    "DiagnosticUserAuthenticationError", "DiagnosticUserProfile",
    "DiagnosticUserProfileResolver", "DiagnosticUserUnavailableError",
    "PlatformDiagnosticUserFetcher", "resolve_diagnostic_users", "safe_avatar_url",
]
