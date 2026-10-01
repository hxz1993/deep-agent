from __future__ import annotations

import asyncio
import contextvars
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from urllib.parse import urlsplit

import dns.asyncresolver
import pydantic_ai._ssrf as pydantic_ssrf
from httpcore2._async.http11 import AsyncHTTP11UpgradeStream


_PUBLIC_DNS_SERVERS = ("1.1.1.1", "8.8.8.8")
_PROXY_FETCH_ACTIVE: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "deepagent_web_fetch_proxy_active",
    default=False,
)
_PROXY_TLS_SERVER_NAME: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "deepagent_web_fetch_proxy_tls_server_name",
    default=None,
)
_ORIGINAL_RESOLVE_HOSTNAME = pydantic_ssrf.resolve_hostname
_ORIGINAL_SEND_REQUEST = pydantic_ssrf._send_request
_ORIGINAL_START_TLS = AsyncHTTP11UpgradeStream.start_tls
_PATCHED = False


def _http_tunnel_proxy_configured() -> bool:
    for key in (
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
        "HTTP_PROXY",
        "http_proxy",
    ):
        value = str(os.environ.get(key) or "").strip()
        if value and urlsplit(value).scheme.casefold() in {"http", "https"}:
            return True
    return False


def _proxy_compatibility_active() -> bool:
    """Keep proxy-safe DNS/SNI active for deferred Pydantic media downloads."""
    return _PROXY_FETCH_ACTIVE.get() or (
        _PATCHED and _http_tunnel_proxy_configured()
    )


async def _public_resolve_hostname(hostname: str) -> list[str]:
    """Resolve a public hostname without consuming a local fake-IP answer.

    Clash-style fake-IP DNS is useful for transparent proxying, but an SSRF-safe
    downloader must connect to the exact address it validated. Public DNS keeps
    that address pinning meaningful when an explicit HTTP CONNECT proxy is used.
    """

    resolver = dns.asyncresolver.Resolver(configure=False)
    resolver.nameservers = list(_PUBLIC_DNS_SERVERS)
    resolver.timeout = 2.0
    resolver.lifetime = 4.0

    async def query(record_type: str) -> list[str]:
        try:
            answer = await resolver.resolve(hostname, record_type, search=False)
        except Exception:  # one address family or resolver may be unavailable
            return []
        return [str(record) for record in answer]

    ipv4, ipv6 = await asyncio.gather(query("A"), query("AAAA"))
    resolved = list(dict.fromkeys([*ipv4, *ipv6]))
    if resolved:
        return resolved
    return await _ORIGINAL_RESOLVE_HOSTNAME(hostname)


async def _resolve_hostname(hostname: str) -> list[str]:
    if not _proxy_compatibility_active():
        return await _ORIGINAL_RESOLVE_HOSTNAME(hostname)
    return await _public_resolve_hostname(hostname)


async def _send_request(client: Any, request: Any):
    if not _proxy_compatibility_active():
        return await _ORIGINAL_SEND_REQUEST(client, request)
    token = _PROXY_TLS_SERVER_NAME.set(request.extensions.get("sni_hostname"))
    try:
        return await _ORIGINAL_SEND_REQUEST(client, request)
    finally:
        _PROXY_TLS_SERVER_NAME.reset(token)


async def _start_tls(
    self: AsyncHTTP11UpgradeStream,
    ssl_context: Any,
    server_hostname: str | None = None,
    timeout: float | None = None,
):
    # httpcore2's HTTP CONNECT path currently ignores request.extensions
    # ["sni_hostname"] and uses the pinned IP as SNI. Preserve the original
    # hostname that Pydantic AI already validated and attached to the request.
    effective_hostname = _PROXY_TLS_SERVER_NAME.get() or server_hostname
    return await _ORIGINAL_START_TLS(
        self,
        ssl_context,
        effective_hostname,
        timeout,
    )


def install_proxy_compatibility() -> None:
    """Install process-wide hooks that activate only for an explicit HTTP proxy.

    Pydantic AI may download an ``ImageUrl`` after the tool call has returned,
    while it is assembling the next model request.  The hooks therefore remain
    installed, but change DNS/SNI behavior only when a compatible explicit HTTP
    tunnel proxy is configured (or inside the scoped Web Fetch context).
    """
    global _PATCHED
    if _PATCHED:
        return
    pydantic_ssrf.resolve_hostname = _resolve_hostname
    pydantic_ssrf._send_request = _send_request
    AsyncHTTP11UpgradeStream.start_tls = _start_tls
    _PATCHED = True


@asynccontextmanager
async def proxy_compatible_web_fetch(*, server_hostname: str | None = None) -> AsyncIterator[None]:
    """Scope the upstream Pydantic AI fetcher to explicit HTTP proxies."""

    install_proxy_compatibility()
    token = _PROXY_FETCH_ACTIVE.set(_http_tunnel_proxy_configured())
    tls_token = _PROXY_TLS_SERVER_NAME.set(server_hostname)
    try:
        yield
    finally:
        _PROXY_TLS_SERVER_NAME.reset(tls_token)
        _PROXY_FETCH_ACTIVE.reset(token)
