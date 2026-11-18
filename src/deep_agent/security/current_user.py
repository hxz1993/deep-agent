from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

import aiohttp
from fastapi import Request

from deepfos_platform.discovery import ServiceDiscovery
from deepfos_platform.discovery.target import normalize_service_target
from deepfos_platform.lib.httpcli import AioHttpCli
from deepfos_platform.lib.utils import concat_url
from deepfos_platform.options import OPTION as PLATFORM_OPTION

from deep_agent.agent.request_context import (
    CONSOLE_CONTEXT_HEADER_PREFIX,
    resolve_request_context,
)
from deep_agent.local_security import LOCAL_CURRENT_USERNAME_SCOPE_KEY
from deep_agent.security.diagnostic_users import safe_avatar_url
from deep_agent.security.console_admin import (
    ConsoleAdminAuthenticationError,
    forwarded_platform_auth_headers,
    platform_credential_digest,
)

_CACHE_MAX_ENTRIES = 256
_PROFILE_TIMEOUT_SECONDS = 5.0


class CurrentUserAuthenticationError(RuntimeError):
    """The platform rejected the forwarded login state."""


class CurrentUserUnavailableError(RuntimeError):
    """The platform could not return a valid current-user profile."""

    http_status: int | None = None


@dataclass(frozen=True)
class CurrentUserProfile:
    user_id: str
    username: str | None
    nickname: str | None = None
    email: str | None = None
    avatar_url: str | None = None
    resolution: str = "resolved"


CurrentUserFetcher = Callable[
    [str, Mapping[str, str]],
    Awaitable[CurrentUserProfile],
]


def _account_profile_headers(
    headers: Mapping[str, str], user_id: str,
) -> dict[str, str]:
    """Use the same credential whitelist and API Key checks for every caller."""
    try:
        request = Request({"type": "http", "headers": [
            (name.lower().encode("latin-1"), value.encode("latin-1"))
            for name, value in headers.items()
        ]})
        forwarded = forwarded_platform_auth_headers(request, user_id)
    except (ConsoleAdminAuthenticationError, AttributeError, TypeError, UnicodeError):
        raise CurrentUserAuthenticationError("invalid platform credentials") from None

    languages: dict[str, str] = {}
    override_language = f"{CONSOLE_CONTEXT_HEADER_PREFIX}language"
    for name, value in request.headers.items():
        if name not in {"language", override_language}:
            continue
        if name in languages or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise CurrentUserAuthenticationError("invalid platform credentials")
        languages[name] = value.strip()
    # Console uses its override header; chat has already resolved it into
    # ``language``. Browser Accept-Language is not the platform language and is
    # absent from the default chat whitelist, so it must not split this cache.
    forwarded["language"] = resolve_request_context(
        app=None, space=None,
        language=languages.get(override_language) or languages.get("language"),
    ).language
    return forwarded


def _profile_payload(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise CurrentUserUnavailableError(
            "current-user profile returned malformed data"
        )
    if value.get("status") is False:
        raise CurrentUserUnavailableError(
            "current-user profile returned malformed data"
        )
    nested = value.get("data")
    if "data" in value:
        if not isinstance(nested, Mapping):
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
        allow_empty: bool = False,
    ) -> None:
        target = (
            PLATFORM_OPTION.server.account
            if account_service is None
            else account_service
        )
        self._account_service = normalize_service_target(str(target))
        self._allow_empty = allow_empty
        self._timeout = aiohttp.ClientTimeout(
            connect=min(3.0, timeout_seconds),
            total=timeout_seconds,
        )

    @property
    def cache_namespace(self) -> str:
        """Identify the account service this fetcher actually calls, without its URL."""
        return hashlib.sha256(self._account_service.encode("utf-8")).hexdigest()

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
            error = CurrentUserUnavailableError(
                f"current-user profile returned HTTP {status_code}"
            )
            error.http_status = status_code
            raise error

        try:
            payload = _profile_payload(json.loads(body))
        except (TypeError, json.JSONDecodeError) as exc:
            raise CurrentUserUnavailableError(
                "current-user profile returned malformed data"
            ) from exc

        returned_ids = tuple(
            returned_id
            for name in ("id", "userId")
            if (value := payload.get(name)) is not None
            and (returned_id := str(value).strip())
        )
        username_value = (
            payload.get("username", payload.get("userName", ""))
            if self._allow_empty else payload.get("username") or payload.get("userName")
        )
        username = username_value.strip()[:256] if isinstance(username_value, str) else (
            None if self._allow_empty and username_value is None else ""
        )
        if any(returned_id != user_id for returned_id in returned_ids):
            raise CurrentUserUnavailableError(
                "current-user profile identity does not match the request"
            )
        if not username and not self._allow_empty:
            raise CurrentUserUnavailableError(
                "current-user profile does not contain a username"
            )
        nickname = (
            payload.get("nickname", payload.get("nickName"))
            if self._allow_empty else payload.get("nickname") or payload.get("nickName")
        )
        email = payload.get("email")
        nickname = nickname.strip()[:256] if isinstance(nickname, str) else None
        email = email.strip()[:320] if isinstance(email, str) else None
        if not self._allow_empty:
            nickname, email = nickname or None, email or None
        profile = CurrentUserProfile(
            user_id=user_id, username=username,
            nickname=nickname,
            email=email,
            avatar_url=safe_avatar_url(payload.get("avatar")),
        )
        has_profile_data = any((
            profile.username, profile.nickname, profile.email, profile.avatar_url,
        ))
        # A non-empty profile without its account ID cannot be attributed to the
        # trusted caller. An entirely empty successful response is safe to expose.
        has_unattributed_data = any(
            value is not None and (not isinstance(value, str) or bool(value.strip()))
            for name in ("username", "userName", "nickname", "nickName", "email", "avatar")
            if (value := payload.get(name)) is not None
        )
        if not returned_ids and (not self._allow_empty or has_unattributed_data):
            raise CurrentUserUnavailableError(
                "current-user profile identity does not match the request"
            )
        if not has_profile_data:
            return replace(profile, resolution="empty")
        return profile


class CurrentUserProfileResolver:
    """Resolve current-user display data with a short credential-scoped cache."""

    def __init__(
        self,
        fetcher: CurrentUserFetcher | None = None,
        *,
        cache_ttl_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetcher = fetcher or PlatformCurrentUserFetcher(allow_empty=True)
        self._cache_ttl_seconds = cache_ttl_seconds
        self._clock = clock
        self._cache: dict[
            tuple[str, str, str],
            tuple[float, CurrentUserProfile],
        ] = {}
        self._inflight: dict[
            tuple[asyncio.AbstractEventLoop, tuple[str, str, str]],
            asyncio.Task[CurrentUserProfile],
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
            return await self.profile_for_headers(
                normalized_user, {}, local_username=local_username,
            )

        profile = await self.profile_for_headers(normalized_user, request.headers)
        if profile.nickname == "" or profile.email == "":
            return replace(profile, nickname=profile.nickname or None, email=profile.email or None)
        return profile

    async def profile_for_headers(
        self,
        user_id: str,
        headers: Mapping[str, str],
        *,
        local_username: str | None = None,
        allow_empty: bool = False,
    ) -> CurrentUserProfile:
        """Resolve safe profile fields from an already authenticated execution identity.

        ``local_username`` must come from trusted server context, never a tool
        argument or client body. Empty successful account responses are available
        to execution callers that explicitly opt in; Console remains strict.
        """
        normalized_user = user_id.strip()
        if not normalized_user:
            raise CurrentUserAuthenticationError("current-user identity is missing")
        normalized_local = (local_username or "").strip()
        if normalized_local:
            return CurrentUserProfile(
                user_id=normalized_user, username=normalized_local[:256], resolution="local",
            )

        normalized_headers = _account_profile_headers(headers, normalized_user)
        namespace = getattr(self._fetcher, "cache_namespace", "")
        if not isinstance(namespace, str):
            namespace = ""
        key = (
            namespace, normalized_user, platform_credential_digest(normalized_headers),
        )
        now = self._clock()
        cached = self._cache.get(key)
        if cached is not None and cached[0] > now:
            profile = cached[1]
        else:
            if cached is not None:
                self._cache.pop(key, None)
            flight_key = (asyncio.get_running_loop(), key)
            task = self._inflight.get(flight_key)
            if task is None:
                task = asyncio.create_task(
                    self._fetch_profile(key, normalized_user, normalized_headers),
                )
                self._inflight[flight_key] = task

                def complete(done: asyncio.Task[CurrentUserProfile]) -> None:
                    if self._inflight.get(flight_key) is done:
                        self._inflight.pop(flight_key, None)
                    # A cancelled waiter must neither cancel a shared request nor
                    # leave an unobserved exception when every waiter went away.
                    if not done.cancelled():
                        done.exception()

                task.add_done_callback(complete)
            profile = await asyncio.shield(task)
        if not allow_empty and not profile.username:
            raise CurrentUserUnavailableError(
                "current-user profile does not contain a username"
            )
        return profile

    async def _fetch_profile(
        self,
        key: tuple[str, str, str],
        user_id: str,
        headers: Mapping[str, str],
    ) -> CurrentUserProfile:
        try:
            # The task can outlive a cancelled waiter. Bound the complete lookup,
            # including discovery, even when no execution context is waiting.
            async with asyncio.timeout(_PROFILE_TIMEOUT_SECONDS):
                profile = await self._fetcher(user_id, headers)
        except (CurrentUserAuthenticationError, CurrentUserUnavailableError):
            raise
        except Exception as exc:  # noqa: BLE001 - injected fetchers fail closed
            raise CurrentUserUnavailableError(
                "current-user profile request failed"
            ) from exc
        if not isinstance(profile, CurrentUserProfile) or profile.user_id != user_id:
            raise CurrentUserUnavailableError(
                "current-user profile identity does not match the request"
            )
        self._cache[key] = (self._clock() + self._cache_ttl_seconds, profile)
        if len(self._cache) > _CACHE_MAX_ENTRIES:
            oldest_key = min(self._cache, key=lambda item: self._cache[item][0])
            self._cache.pop(oldest_key, None)
        return profile


_CURRENT_USER_PROFILE_RESOLVER: CurrentUserProfileResolver | None = None


def get_current_user_profile_resolver() -> CurrentUserProfileResolver:
    """Share successful account lookups between Console and execution contexts."""
    global _CURRENT_USER_PROFILE_RESOLVER
    # Configuration adapters must be able to initialize before the first lookup;
    # importing security must not capture the account service prematurely.
    if _CURRENT_USER_PROFILE_RESOLVER is None:
        _CURRENT_USER_PROFILE_RESOLVER = CurrentUserProfileResolver()
    return _CURRENT_USER_PROFILE_RESOLVER


__all__ = [
    "CurrentUserAuthenticationError",
    "CurrentUserFetcher",
    "CurrentUserProfile",
    "CurrentUserProfileResolver",
    "CurrentUserUnavailableError",
    "PlatformCurrentUserFetcher",
    "get_current_user_profile_resolver",
]
