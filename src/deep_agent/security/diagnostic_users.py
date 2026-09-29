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
from deepfos_platform.discovery.target import normalize_service_target
from deepfos_platform.lib.httpcli import AioHttpCli
from deepfos_platform.lib.utils import concat_url
from deepfos_platform.options import OPTION as PLATFORM_OPTION

from deep_agent.agent.observability import log_event
from deep_agent.local_security import (
    LOCAL_CONSOLE_ADMIN_SCOPE_KEY,
    LOCAL_CURRENT_USERNAME_SCOPE_KEY,
)
from deep_agent.security.console_admin import (
    ConsoleAdminAuthenticationError,
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
    username: str | None
    nickname: str | None
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
        "username": None, "nickname": None, "email": None, "avatar_url": None, "resolution": resolution,
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
        raw_id = next((item[key] for key in ("userId", "id", "user_id")
                       if item.get(key) is not None and item.get(key) != ""), None)
        user_id = str(raw_id) if isinstance(raw_id, int) and not isinstance(raw_id, bool) else _text(raw_id)
        if user_id not in allowed:
            continue
        username = _text(item.get("userName")) or _text(item.get("username"))
        nickname = _text(item.get("nickName")) or _text(item.get("nickname"))
        display_name = username or nickname or user_id
        result[user_id] = {
            "id": user_id, "user_id": user_id, "display_name": display_name,
            "username": username or None, "nickname": nickname or None,
            "email": _text(item.get("email"), 320) or None,
            "avatar_url": safe_avatar_url(item.get("avatar")),
            "resolution": "resolved",
        }
    return result


class PlatformDiagnosticUserFetcher:
    """Use System's cross-space account adapter, as other business services do.

    ``/user/get-user-detail-list`` accepts only the requested user IDs and the
    caller's authentication. System owns the downstream Account integration;
    diagnostics must not supply or infer an App, Space, or enterprise identity.
    """

    def __init__(self, *, system_service: str | None = None) -> None:
        self._profile_service = normalize_service_target(str(
            PLATFORM_OPTION.server.system if system_service is None else system_service
        ))
        # The namespace is deployment-specific; never retain a configured secret as a key.
        self.cache_namespace = hashlib.sha256(self._profile_service.encode()).hexdigest()

    async def _resolve_url(
        self,
    ) -> tuple[str, Callable[[], None] | None, Callable[[], None] | None]:
        target = self._profile_service
        if not target:
            raise DiagnosticUserUnavailableError("account service is not configured")
        parsed = urlsplit(target)
        if parsed.scheme:
            if (parsed.scheme not in {"http", "https"} or not parsed.netloc
                    or parsed.username or parsed.password or parsed.query or parsed.fragment):
                raise DiagnosticUserUnavailableError("account service URL is invalid")
            return concat_url(target, "user", "get-user-detail-list"), None, None
        try:
            discovery = ServiceDiscovery.instantiate()
            if discovery.closed:
                await ServiceDiscovery.start()
            on_success, on_failure, base_url = await discovery.get_url(target)
        except Exception as exc:
            raise DiagnosticUserUnavailableError("account service is unavailable") from exc
        return concat_url(base_url, "user", "get-user-detail-list"), on_success, on_failure

    async def __call__(
        self, user_ids: tuple[str, ...], headers: Mapping[str, str],
    ) -> dict[str, DiagnosticUserProfile]:
        if not user_ids or len(user_ids) > _BATCH_SIZE:
            raise ValueError("account lookup batch must contain 1 to 100 user IDs")
        response = None
        on_failure: Callable[[], None] | None = None
        started = time.monotonic()
        phase, reason, outcome = "discovery", "unavailable", "failed"
        facts = {"profile_service": "configured_url" if "://" in self._profile_service else self._profile_service,
                 "discovery_type": str(PLATFORM_OPTION.srvdis.implementation),
                 "discovery_namespace": str(PLATFORM_OPTION.k8s.namespace or "unset"),
                 "requested_count": len(user_ids),
                 "auth_context": ".".join(name for name in ("cookie", "authorization", "token", "x-user-token")
                                          if headers.get(name)) or "none"}
        try:
            url, on_success, on_failure = await self._resolve_url()
            # Retain only the network origin, never configured path/query credentials.
            parsed = urlsplit(url)
            if not parsed.username and not parsed.password:
                facts["target_origin"] = f"{parsed.scheme}://{parsed.netloc}"
            phase = "request"
            response = await AioHttpCli.post(
                url, json=list(user_ids), headers=dict(headers),
                timeout=aiohttp.ClientTimeout(connect=2.0, total=2.0),
                allow_redirects=False,
            )
            facts["http_status"] = response.status
            if response.status in {401, 403}:
                reason = "authentication_rejected"
                raise DiagnosticUserAuthenticationError(response.status)
            if not 200 <= response.status < 300:
                reason = "http_error"
                raise DiagnosticUserUnavailableError("account lookup returned an error")
            phase = "response"
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.content.iter_chunked(16384):
                size += len(chunk)
                if size > _MAX_RESPONSE_BYTES:
                    reason = "response_too_large"
                    raise DiagnosticUserUnavailableError("account lookup response is too large")
                chunks.append(chunk)
            phase, reason = "decode", "invalid_json"
            payload = json.loads(b"".join(chunks))
            phase, reason = "normalize", "invalid_payload"
            if isinstance(payload, Mapping):
                code = payload.get("code", payload.get("statusCode"))
                if isinstance(code, (str, int)) and not isinstance(code, bool):
                    # Only numeric upstream codes are safe to retain.
                    candidate = str(code)
                    if candidate.lstrip("-").isdigit() and len(candidate) <= 12:
                        facts["error_code"] = candidate
                if payload.get("status") is False:
                    reason = "business_failure"
            result = _normalize_profiles(payload, user_ids)
            facts["resolved_count"] = sum(v["display_name"] != key for key, v in result.items())
            if on_success is not None:
                on_success()
            outcome, reason = "completed", "resolved" if facts["resolved_count"] == len(user_ids) else "partial"
            facts.pop("error_code", None)
            return result
        except DiagnosticUserAuthenticationError as exc:
            reason = "authentication_rejected"
            facts["error_type"] = type(exc).__name__
            raise
        except asyncio.CancelledError:
            reason = "deadline_or_cancelled"
            raise
        except Exception as exc:
            cause = exc.__cause__ or exc
            facts["error_type"] = type(cause).__name__
            if phase == "discovery" and type(getattr(cause, "status", None)) is int:
                facts["http_status"] = cause.status
            if isinstance(exc, TimeoutError):
                reason = "timeout"
            elif phase == "discovery":
                reason = "service_discovery_failed"
            elif phase == "request":
                reason = "http_error" if response is not None else "network_error"
            if on_failure is not None:
                on_failure()
            if isinstance(exc, DiagnosticUserUnavailableError):
                raise
            raise DiagnosticUserUnavailableError("account lookup is unavailable") from exc
        finally:
            if response is not None:
                response.release()
            log_event(f"diagnostic_users_{outcome}", phase=phase, reason=reason,
                      duration_ms=round((time.monotonic() - started) * 1000), **facts)


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
        try:
            headers = forwarded_platform_auth_headers(request, caller)
        except ConsoleAdminAuthenticationError:
            raise DiagnosticUserAuthenticationError() from None
        has_platform_login = any(headers.get(name, "").strip() for name in (
            "authorization", "cookie", "token", "x-user-token",
        ))
        if request.scope.get(LOCAL_CONSOLE_ADMIN_SCOPE_KEY) is True and not has_platform_login:
            # Only trusted local_dev scope enables this. A user-supplied header cannot.
            local_name = _text(request.scope.get(LOCAL_CURRENT_USERNAME_SCOPE_KEY))
            if caller in result and local_name:
                result[caller] = {**_fallback(caller, "local"), "display_name": local_name, "username": local_name}
            return result

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
        except Exception as exc:
            # Covers the overall deadline, including discovery and all batches.
            log_event("diagnostic_users_fallback", phase="resolve",
                      reason="deadline_exceeded" if isinstance(exc, TimeoutError) else "lookup_unavailable",
                      error_type=type(exc).__name__, requested_count=len(missing))
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
