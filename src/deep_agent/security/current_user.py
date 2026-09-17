from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

import aiohttp
from fastapi import Request
from loguru import logger

from deepfos_platform.discovery import ServiceDiscovery
from deepfos_platform.discovery.target import normalize_service_target
from deepfos_platform.lib.httpcli import AioHttpCli
from deepfos_platform.lib.utils import concat_url
from deepfos_platform.options import OPTION as PLATFORM_OPTION

from deep_agent.local_security import LOCAL_CURRENT_USERNAME_SCOPE_KEY
from deep_agent.security.diagnostic_users import safe_avatar_url
from deep_agent.security.console_admin import (
    forwarded_platform_auth_headers,
    platform_credential_digest,
)

_CACHE_MAX_ENTRIES = 256


class CurrentUserAuthenticationError(RuntimeError):
    """The platform rejected the forwarded login state."""


class CurrentUserUnavailableError(RuntimeError):
    """The platform could not return a valid current-user profile."""


@dataclass(frozen=True)
class CurrentUserProfile:
    user_id: str
    username: str
    nickname: str | None = None
    email: str | None = None
    avatar_url: str | None = None
    resolution: str = "resolved"


CurrentUserFetcher = Callable[
    [str, Mapping[str, str]],
    Awaitable[CurrentUserProfile],
]


def _profile_payload(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise CurrentUserUnavailableError(
            "current-user profile returned malformed data"
        )
    nested = value.get("data")
    if "data" in value:
        if value.get("status") is False or not isinstance(nested, Mapping):
            raise CurrentUserUnavailableError(
                "current-user profile returned malformed data"
            )
        return nested
    return value


class PlatformCurrentUserFetcher:
    """Read the signed-in user's account profile from the platform account service."""

    def __init__(
        self,
        *,
        account_service: str | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        target = (
            PLATFORM_OPTION.server.account
            if account_service is None
            else account_service
        )
        self._account_service = normalize_service_target(str(target))
        self._timeout = aiohttp.ClientTimeout(
            connect=min(3.0, timeout_seconds),
            total=timeout_seconds,
        )

    async def _resolve_url(
        self,
    ) -> tuple[str, Callable[[], None] | None, Callable[[], None] | None]:
        target = self._account_service
        if not target:
            raise CurrentUserUnavailableError(
                "platform account service is not configured"
            )

        parsed = urlsplit(target)
        if parsed.scheme:
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise CurrentUserUnavailableError(
                    "platform account service URL is invalid"
                )
            return concat_url(target, "api", "user", "full-detail"), None, None

        try:
            discovery = ServiceDiscovery.instantiate()
            if discovery.closed:
                await ServiceDiscovery.start()
            on_success, on_failure, base_url = await discovery.get_url(target)
        except Exception as exc:  # noqa: BLE001 - discovery failures are unavailable
            raise CurrentUserUnavailableError(
                "current-user profile service is unavailable"
            ) from exc
        return (
            concat_url(base_url, "api", "user", "full-detail"),
            on_success,
            on_failure,
        )

    async def __call__(
        self,
        user_id: str,
        headers: Mapping[str, str],
    ) -> CurrentUserProfile:
        response = None
        on_success: Callable[[], None] | None = None
        on_failure: Callable[[], None] | None = None
        try:
            url, on_success, on_failure = await self._resolve_url()
            response = await AioHttpCli.get(
                url,
                headers=dict(headers),
                timeout=self._timeout,
            )
            status_code = response.status
            body = await response.text()
        except CurrentUserUnavailableError:
            raise
        except (asyncio.TimeoutError, aiohttp.ClientError, OSError) as exc:
            if on_failure is not None:
                on_failure()
            raise CurrentUserUnavailableError(
                "current-user profile is temporarily unavailable"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - invalid clients fail closed
            if on_failure is not None:
                on_failure()
            raise CurrentUserUnavailableError(
                "current-user profile request failed"
            ) from exc
        finally:
            if response is not None:
                response.release()

        if status_code >= 500:
            if on_failure is not None:
                on_failure()
        elif on_success is not None:
            on_success()

        if status_code == 401:
            raise CurrentUserAuthenticationError("platform login has expired")
        if not 200 <= status_code < 300:
            logger.warning(
                f"current-user profile returned HTTP {status_code}, url: [{url}]"
            )
            raise CurrentUserUnavailableError(
                f"current-user profile returned HTTP {status_code}"
            )

        try:
            payload = _profile_payload(json.loads(body))
        except (TypeError, json.JSONDecodeError) as exc:
            raise CurrentUserUnavailableError(
                "current-user profile returned malformed data"
            ) from exc

        returned_id = str(payload.get("id") or payload.get("userId") or "").strip()
        username = str(
            payload.get("username") or payload.get("userName") or ""
        ).strip()
        if returned_id != user_id:
            raise CurrentUserUnavailableError(
                "current-user profile identity does not match the request"
            )
        if not username:
            raise CurrentUserUnavailableError(
                "current-user profile does not contain a username"
            )
        nickname = payload.get("nickname") or payload.get("nickName")
        email = payload.get("email")
        return CurrentUserProfile(
            user_id=user_id, username=username,
            nickname=nickname.strip()[:256] or None if isinstance(nickname, str) else None,
            email=email.strip()[:320] or None if isinstance(email, str) else None,
            avatar_url=safe_avatar_url(payload.get("avatar")),
        )


class CurrentUserProfileResolver:
    """Resolve current-user display data with a short credential-scoped cache."""

    def __init__(
        self,
        fetcher: CurrentUserFetcher | None = None,
        *,
        cache_ttl_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetcher = fetcher or PlatformCurrentUserFetcher()
        self._cache_ttl_seconds = cache_ttl_seconds
        self._clock = clock
        self._cache: dict[
            tuple[str, str],
            tuple[float, CurrentUserProfile],
        ] = {}

    async def profile_for(
        self,
        request: Request,
        user_id: str,
    ) -> CurrentUserProfile:
        normalized_user = user_id.strip()
        local_username = str(
            request.scope.get(LOCAL_CURRENT_USERNAME_SCOPE_KEY) or ""
        ).strip()
        if local_username:
            return CurrentUserProfile(
                user_id=normalized_user,
                username=local_username,
                resolution="local",
            )

        headers = forwarded_platform_auth_headers(request, normalized_user)
        key = (normalized_user, platform_credential_digest(headers))
        now = self._clock()
        cached = self._cache.get(key)
        if cached is not None and cached[0] > now:
            return cached[1]
        if cached is not None:
            self._cache.pop(key, None)

        try:
            profile = await self._fetcher(normalized_user, headers)
        except (CurrentUserAuthenticationError, CurrentUserUnavailableError):
            raise
        except Exception as exc:  # noqa: BLE001 - injected fetchers fail closed
            raise CurrentUserUnavailableError(
                "current-user profile request failed"
            ) from exc

        self._cache[key] = (now + self._cache_ttl_seconds, profile)
        if len(self._cache) > _CACHE_MAX_ENTRIES:
            oldest_key = min(self._cache, key=lambda item: self._cache[item][0])
            self._cache.pop(oldest_key, None)
        return profile


__all__ = [
    "CurrentUserAuthenticationError",
    "CurrentUserFetcher",
    "CurrentUserProfile",
    "CurrentUserProfileResolver",
    "CurrentUserUnavailableError",
    "PlatformCurrentUserFetcher",
]
