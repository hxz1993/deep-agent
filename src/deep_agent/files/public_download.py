"""Bounded public-file download shared by PTC relay and channel media preflight."""
from __future__ import annotations

import asyncio
import mimetypes
import re
import time
from email.message import Message
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit, urlunsplit

import httpx2
from pydantic_ai import ModelRetry
from pydantic_ai._ssrf import safe_download

from deep_agent.agent.tool_errors import tool_error_message
from deep_agent.web.proxy_compat import proxy_compatible_web_fetch

MAX_PUBLIC_FILE_BYTES = 20 * 1024 * 1024


def _safe_download_name(url: str, response: httpx2.Response, mime: str) -> str:
    disposition = response.headers.get("content-disposition")
    candidate: str | None = None
    if disposition:
        message = Message()
        message["content-disposition"] = disposition
        candidate = message.get_filename()
    if not candidate:
        candidate = PurePosixPath(unquote(urlsplit(url).path)).name
    candidate = (candidate or "").replace("\\", "/").rsplit("/", 1)[-1]
    candidate = "".join(char for char in candidate if ord(char) >= 32).strip(" .")
    if candidate:
        return candidate[:255]
    extension = mimetypes.guess_extension(mime, strict=False) or ".bin"
    return f"download{extension}"


def _public_response_url(response: httpx2.Response, fallback: str) -> str:
    request = response.request
    parsed = urlsplit(str(request.url))
    host = request.headers.get("host")
    if not host:
        return fallback
    return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, ""))


async def load_public_file(
    url: str, *, max_bytes: int = MAX_PUBLIC_FILE_BYTES,
) -> tuple[bytes, str, str, str]:
    """Read a public file into call-local memory, without credentials or storage."""
    if max_bytes <= 0:
        raise ModelRetry("本次文件已无可用发送额度")
    max_bytes = min(max_bytes, MAX_PUBLIC_FILE_BYTES)
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise ModelRetry("url 只接受有效的绝对 HTTP(S) 公网地址") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ModelRetry("url 只接受绝对 HTTP(S) 公网地址")
    if parsed.username is not None or parsed.password is not None:
        raise ModelRetry("url 不允许包含用户名或密码")

    # Preserve PTC's existing download policy, including every redirect's SSRF
    # check, address pinning and explicit-proxy DNS/SNI compatibility.
    from deep_agent.web.runtime import (
        _WEB_FETCH_ATTEMPT_TIMEOUT_SECONDS,
        _WEB_FETCH_MAX_ATTEMPTS,
        _WEB_FETCH_RETRY_DELAY_SECONDS,
        _WEB_FETCH_TOTAL_TIMEOUT_SECONDS,
        _fetch_retry_decision,
    )

    deadline = time.monotonic() + _WEB_FETCH_TOTAL_TIMEOUT_SECONDS
    attempts = 0
    while True:
        attempts += 1
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ModelRetry("公网文件下载超时")
        try:
            async with asyncio.timeout(min(_WEB_FETCH_ATTEMPT_TIMEOUT_SECONDS, remaining)):
                async with proxy_compatible_web_fetch():
                    response = await safe_download(
                        url, allow_local=False,
                        timeout=int(_WEB_FETCH_ATTEMPT_TIMEOUT_SECONDS),
                        headers={"Accept": "*/*"}, max_bytes=max_bytes,
                    )
            break
        except asyncio.CancelledError:
            raise
        except (ValueError, httpx2.HTTPError, TimeoutError) as exc:
            decision = _fetch_retry_decision(exc)
            remaining = deadline - time.monotonic()
            if (decision is not None and attempts < _WEB_FETCH_MAX_ATTEMPTS
                    and remaining > decision.delay_seconds):
                await asyncio.sleep(decision.delay_seconds)
                continue
            if (decision is None and attempts < _WEB_FETCH_MAX_ATTEMPTS
                    and isinstance(exc, httpx2.RequestError)
                    and remaining > _WEB_FETCH_RETRY_DELAY_SECONDS):
                await asyncio.sleep(_WEB_FETCH_RETRY_DELAY_SECONDS)
                continue
            raise ModelRetry(tool_error_message("公网文件下载失败或地址不安全", exc)) from exc

    data = response.content
    if len(data) > max_bytes:
        raise ModelRetry(f"文件超过本次 {max_bytes} 字节上限")
    raw_mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    mime = (raw_mime if re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", raw_mime)
            else "application/octet-stream")
    final_url = _public_response_url(response, url)
    return data, _safe_download_name(final_url, response, mime), mime, final_url
