"""Content-validated browser caching for the build-free Console resources."""
from __future__ import annotations

import hashlib
from pathlib import Path

import anyio
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope


_MAX_CACHED_BYTES = 8 * 1024 * 1024
_UI_ASSET_SUFFIXES = frozenset({
    ".js", ".mjs", ".css", ".html", ".svg", ".png", ".jpg", ".jpeg",
    ".gif", ".webp", ".avif", ".ico", ".woff", ".woff2", ".ttf", ".otf", ".eot",
})


def _read_content(path: str) -> tuple[bytes, str] | None:
    # Hash the bytes that will actually be sent. File timestamps and lengths can
    # both survive a deployment, so neither is a sufficient cache validator.
    # Bound the read as well as checking stat below: a file may grow between
    # lookup and open. Downloads and oversized assets remain streamed natively.
    with Path(path).open("rb") as source:
        content = source.read(_MAX_CACHED_BYTES + 1)
    if len(content) > _MAX_CACHED_BYTES:
        return None
    return content, f'"sha256-{hashlib.sha256(content).hexdigest()}"'


def _matches_etag(value: str, etag: str) -> bool:
    # GET/HEAD If-None-Match uses weak comparison, including a list or wildcard.
    for candidate in value.split(","):
        candidate = candidate.strip()
        if candidate.startswith("W/"):
            candidate = candidate[2:]
        if candidate == "*" or candidate == etag:
            return True
    return False


class ConsoleStaticFiles(StaticFiles):
    """Content-validate UI assets without changing native static downloads."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        # Retain StaticFiles' containment, symlink, method and directory rules,
        # but bypass its mtime/size validator and If-Modified-Since fallback.
        lookup_scope = dict(scope)
        lookup_scope["headers"] = [
            (key, value)
            for key, value in scope["headers"]
            if key.lower() not in (b"if-none-match", b"if-modified-since")
        ]
        response = await super().get_response(path, lookup_scope)
        if not isinstance(response, FileResponse) or response.status_code != 200:
            response.headers["Cache-Control"] = "no-store"
            return response

        async def native_response() -> Response:
            # Preserve FileResponse's streaming and any Range/If-Range support
            # provided by the installed Starlette version. This also preserves
            # the existing conditional-response behavior for downloads.
            native = await super(ConsoleStaticFiles, self).get_response(path, scope)
            native.headers["cache-control"] = "no-store"
            return native

        if "range" in Headers(scope=scope):
            return await native_response()

        source_path = Path(response.path)
        if source_path.name == "index.html":
            response.headers["cache-control"] = "no-store"
            for header in ("etag", "last-modified"):
                del response.headers[header]
            return response

        if (
            "downloads" in Path(path).parts
            or source_path.suffix.lower() not in _UI_ASSET_SUFFIXES
            or response.stat_result.st_size > _MAX_CACHED_BYTES
        ):
            return await native_response()

        try:
            snapshot = await anyio.to_thread.run_sync(_read_content, str(response.path))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=401) from exc
        if snapshot is None:
            return await native_response()
        content, etag = snapshot

        headers = dict(response.headers)
        # Only content ETags validate these files. A timestamp alone must not
        # cause a stale 304 when deployment tools preserve modification times.
        headers.pop("last-modified", None)
        headers["content-length"] = str(len(content))
        headers["cache-control"] = "private, no-cache"
        headers["etag"] = etag
        if _matches_etag(Headers(scope=scope).get("if-none-match", ""), etag):
            return Response(status_code=304, headers={
                "cache-control": headers["cache-control"],
                "etag": etag,
            })

        return Response(
            content=b"" if scope["method"] == "HEAD" else content,
            headers=headers,
            background=response.background,
        )
