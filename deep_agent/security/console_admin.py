from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

import aiohttp
from fastapi import Request
from loguru import logger

from deepfos_platform.discovery import ServiceDiscovery
from deepfos_platform.lib.httpcli import AioHttpCli
from deepfos_platform.lib.utils import concat_url
from deepfos_platform.options import OPTION as PLATFORM_OPTION

from deep_agent.local_security import LOCAL_CONSOLE_ADMIN_SCOPE_KEY

ENTERPRISE_ADMIN_ROLE = "enterprise_admin"
_FORWARDED_AUTH_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "token",
        "x-tenant-id",
        "x-user-token",
    }
)
_FORWARDED_AUTH_PREFIXES = ("x-deepfos-",)
_CACHE_MAX_ENTRIES = 256


class ConsoleAdminAuthenticationError(RuntimeError):
    """The platform rejected the forwarded login state."""


class ConsoleAdminUnavailableError(RuntimeError):
    """The platform could not complete an enterprise-admin check."""


@dataclass(frozen=True)
class ConsoleAdminAccess:
    allowed: bool
    roles: tuple[str, ...] = ()


EnterpriseAdminChecker = Callable[[str, Mapping[str, str]], Awaitable[bool]]


def _forwarded_auth_headers(request: Request, user_id: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for raw_name, value in request.headers.items():
        name = raw_name.lower()
        if name in _FORWARDED_AUTH_HEADERS or any(
            name.startswith(prefix) for prefix in _FORWARDED_AUTH_PREFIXES
        ):
            headers[name] = value
    # The caller already resolved this value through the trusted gateway header
    # dependency. Do not forward a differently-cased duplicate from the client.
    headers["user"] = user_id
    return headers


def _credential_digest(headers: Mapping[str, str]) -> str:
    credential_items = sorted(
        (name.lower(), value)
        for name, value in headers.items()
        if name.lower() != "user"
    )
    encoded = json.dumps(
        credential_items,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class PlatformEnterpriseAdminChecker:
    """Call the platform's global enterprise-admin decision endpoint."""

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
        self._account_service = str(target).strip()
        self._timeout = aiohttp.ClientTimeout(
            connect=min(3.0, timeout_seconds),
            total=timeout_seconds,
        )

    async def _resolve_url(
        self,
    ) -> tuple[str, Callable[[], None] | None, Callable[[], None] | None]:
        target = self._account_service
        if not target:
            raise ConsoleAdminUnavailableError("platform account service is not configured")

        parsed = urlsplit(target)
        if parsed.scheme:
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ConsoleAdminUnavailableError(
                    "platform account service URL is invalid"
                )
            return (
                concat_url(target, "oauth", "checkEnterpriseAdmin"),
                None,
                None,
            )

        try:
            discovery = ServiceDiscovery.instantiate()
            if discovery.closed:
                await ServiceDiscovery.start()
            on_success, on_failure, base_url = await discovery.get_url(target)
        except Exception as exc:  # noqa: BLE001 - discovery failures fail closed
            raise ConsoleAdminUnavailableError(
                "enterprise-admin verification service is unavailable"
            ) from exc

        return (
            concat_url(base_url, "oauth", "checkEnterpriseAdmin"),
            on_success,
            on_failure,
        )

    async def __call__(self, user_id: str, headers: Mapping[str, str]) -> bool:
        response = None
        on_success: Callable[[], None] | None = None
        on_failure: Callable[[], None] | None = None
        try:
            url, on_success, on_failure = await self._resolve_url()
            response = await AioHttpCli.get(
                url,
                params={"userId": user_id},
                headers=dict(headers),
                timeout=self._timeout,
            )
            status_code = response.status
            body = await response.text()
        except ConsoleAdminUnavailableError:
            raise
        except (asyncio.TimeoutError, aiohttp.ClientError, OSError) as exc:
            if on_failure is not None:
                on_failure()
            raise ConsoleAdminUnavailableError(
                "enterprise-admin verification is temporarily unavailable"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - fail closed on client/runtime defects
            if on_failure is not None:
                on_failure()
            raise ConsoleAdminUnavailableError(
                "enterprise-admin verification failed"
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
            raise ConsoleAdminAuthenticationError("platform login has expired")
        if not 200 <= status_code < 300:
            logger.warning(
                f"enterprise-admin verification returned HTTP {status_code}, "
                f"url: [{url}]"
            )
            raise ConsoleAdminUnavailableError(
                f"enterprise-admin verification returned HTTP {status_code}"
            )
        try:
            value = json.loads(body)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ConsoleAdminUnavailableError(
                "enterprise-admin verification returned malformed data"
            ) from exc
        if isinstance(value, bool):
            return value
        if (
            isinstance(value, dict)
            and value.get("status") is True
            and isinstance(value.get("data"), bool)
        ):
            return value["data"]
        raise ConsoleAdminUnavailableError(
            "enterprise-admin verification returned an invalid decision"
        )


class ConsoleAdminAuthorizer:
    """Resolve Console roles and keep a short credential-scoped decision cache."""

    def __init__(
        self,
        checker: EnterpriseAdminChecker | None = None,
        *,
        cache_ttl_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._checker = checker or PlatformEnterpriseAdminChecker()
        self._cache_ttl_seconds = cache_ttl_seconds
        self._clock = clock
        self._cache: dict[tuple[str, str], tuple[float, ConsoleAdminAccess]] = {}

    @classmethod
    def for_testing(
        cls,
        checker: EnterpriseAdminChecker | None = None,
    ) -> ConsoleAdminAuthorizer:
        async def allow_enterprise_admin(
            _user_id: str,
            _headers: Mapping[str, str],
        ) -> bool:
            return True

        return cls(checker or allow_enterprise_admin)

    async def access_for(self, request: Request, user_id: str) -> ConsoleAdminAccess:
        normalized_user = user_id.strip()
        if normalized_user == "admin":
            return ConsoleAdminAccess(allowed=False)

        headers = _forwarded_auth_headers(request, normalized_user)
        key = (normalized_user, _credential_digest(headers))
        now = self._clock()
        cached = self._cache.get(key)
        if cached is not None and cached[0] > now:
            return cached[1]
        if cached is not None:
            self._cache.pop(key, None)

        try:
            allowed = await self._checker(normalized_user, headers)
        except (ConsoleAdminAuthenticationError, ConsoleAdminUnavailableError):
            raise
        except Exception as exc:  # noqa: BLE001 - custom checkers must also fail closed
            raise ConsoleAdminUnavailableError(
                "enterprise-admin verification failed"
            ) from exc

        access = ConsoleAdminAccess(
            allowed=allowed,
            roles=(ENTERPRISE_ADMIN_ROLE,) if allowed else (),
        )
        self._cache[key] = (now + self._cache_ttl_seconds, access)
        if len(self._cache) > _CACHE_MAX_ENTRIES:
            oldest_key = min(self._cache, key=lambda item: self._cache[item][0])
            self._cache.pop(oldest_key, None)
        return access


__all__ = [
    "ConsoleAdminAccess",
    "ConsoleAdminAuthenticationError",
    "ConsoleAdminAuthorizer",
    "ConsoleAdminUnavailableError",
    "ENTERPRISE_ADMIN_ROLE",
    "LOCAL_CONSOLE_ADMIN_SCOPE_KEY",
    "PlatformEnterpriseAdminChecker",
]
