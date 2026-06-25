from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout
from loguru import logger

from deep_agent.capabilities.mcp.builtin_config import resolve_builtin_spawn_config
from deep_agent.lib.const import MCP_REGISTRY_JSON_ENV, MCP_SERVER_TTL_ENV
from deep_agent.core.paths import REPO_ROOT
from deep_agent.options import OPTION

_SHARED_HOST = "127.0.0.1"
_MCP_HTTP_PATH = "/mcp"


def shared_mcp_identity(server_name: str) -> str:
    return f"builtin-{server_name}"


def _json_path(runtime_dir: Path, identity: str) -> Path:
    return runtime_dir / f"{identity}.json"


def _lock_path(runtime_dir: Path, identity: str) -> Path:
    return runtime_dir / f"{identity}.lock"


def _free_tcp_port(host: str = _SHARED_HOST) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _tcp_open(host: str, port: int, *, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _read_record(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _write_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")


def _record_url(record: dict[str, Any]) -> str | None:
    url = record.get("url")
    if isinstance(url, str) and url.strip():
        return url.strip()
    host = str(record.get("host") or _SHARED_HOST)
    port = record.get("port")
    if port is None:
        return None
    return f"http://{host}:{int(port)}{_MCP_HTTP_PATH}"


def _record_is_live(record: dict[str, Any]) -> bool:
    host = str(record.get("host") or _SHARED_HOST)
    port = record.get("port")
    if port is None:
        return False
    return _tcp_open(host, int(port))


@contextlib.asynccontextmanager
async def _async_identity_lock(lock_path: Path, timeout: float):
    lock = FileLock(str(lock_path), timeout=timeout)
    await asyncio.to_thread(lock.acquire)
    try:
        yield
    finally:
        await asyncio.to_thread(lock.release)


async def _wait_for_live_server(
    host: str,
    port: int,
    process: asyncio.subprocess.Process,
    *,
    timeout: float,
    server_name: str,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.returncode is not None:
            raise RuntimeError(
                f"Shared MCP server {server_name!r} (pid={process.pid}) exited before "
                f"listening on {host}:{port}. "
            )
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=0.25,
            )
        except (OSError, asyncio.TimeoutError):
            await asyncio.sleep(0.05)
            continue
        writer.close()
        await writer.wait_closed()
        return
    raise TimeoutError(f"Timed out waiting for MCP server {server_name!r} at {host}:{port}")


@dataclass
class McpSharedRegistry:
    """Pod-local lazy coordination for shared streamable-http builtin MCP servers."""

    runtime_dir: Path | None = None
    lock_timeout: float | None = None
    server_ttl: float | None = None
    connect_timeout: float | None = None

    def __post_init__(self) -> None:
        self._runtime_dir = Path(
            self.runtime_dir
            if self.runtime_dir is not None
            else OPTION.agent.mcp_runtime_dir
        )
        self._lock_timeout = (
            float(OPTION.agent.mcp_shared_lock_timeout)
            if self.lock_timeout is None
            else self.lock_timeout
        )
        self._server_ttl = (
            float(OPTION.agent.mcp_shared_server_ttl)
            if self.server_ttl is None
            else self.server_ttl
        )
        self._connect_timeout = (
            float(OPTION.agent.mcp_init_timeout)
            if self.connect_timeout is None
            else self.connect_timeout
        )
        self._touch_refs: dict[str, int] = {}
        self._touch_tasks: dict[str, asyncio.Task[None]] = {}
        self._async_lock = asyncio.Lock()

    def _paths(self, server_name: str) -> tuple[str, Path, Path]:
        identity = shared_mcp_identity(server_name)
        return identity, _json_path(self._runtime_dir, identity), _lock_path(self._runtime_dir, identity)

    async def _drop_stale_record(self, json_path: Path, lock_path: Path) -> None:
        record = _read_record(json_path)
        if record is None or _record_is_live(record):
            return
        try:
            async with _async_identity_lock(lock_path, self._lock_timeout):
                record = _read_record(json_path)
                if record is None or _record_is_live(record):
                    return
                json_path.unlink(missing_ok=True)
                logger.info("Dropped stale MCP registry record at {}", json_path)
        except Timeout:
            logger.debug("Skipped stale MCP record cleanup: lock busy ({})", lock_path)

    async def ensure_server(self, server_name: str, config: dict[str, Any] | None = None) -> str:
        identity, json_path, lock_path = self._paths(server_name)
        await self._drop_stale_record(json_path, lock_path)

        record = _read_record(json_path)
        if record is not None:
            url = _record_url(record)
            if url and _record_is_live(record):
                return url

        try:
            async with _async_identity_lock(lock_path, self._lock_timeout):
                record = _read_record(json_path)
                if record is not None:
                    url = _record_url(record)
                    if url and _record_is_live(record):
                        return url

                port = _free_tcp_port()
                spawn_config = resolve_builtin_spawn_config(server_name, config)
                env = os.environ.copy()
                env.update(spawn_config.get("env") or {})
                env[MCP_REGISTRY_JSON_ENV] = str(json_path)
                env[MCP_SERVER_TTL_ENV] = str(self._server_ttl)
                env["PYTHONPATH"] = str(REPO_ROOT)
                command = spawn_config["command"]
                args = list(spawn_config.get("args", []))
                args.extend(
                    [
                        "--transport",
                        "streamable-http",
                        "--host",
                        _SHARED_HOST,
                        "--port",
                        str(port),
                    ]
                )
                # ponytail: omit stdout/stderr so the child inherits the worker console.
                process = await asyncio.create_subprocess_exec(
                    command,
                    *args,
                    env=env,
                    cwd=spawn_config.get("cwd"),
                )
                new_record = {
                    "url": f"http://{_SHARED_HOST}:{port}{_MCP_HTTP_PATH}",
                    "host": _SHARED_HOST,
                    "port": port,
                    "pid": process.pid,
                    "server_name": server_name,
                }
                _write_record(json_path, new_record)
                logger.info(
                    "Started shared MCP server {!r} at {} (pid={})",
                    server_name,
                    new_record["url"],
                    process.pid,
                )
                await _wait_for_live_server(
                    _SHARED_HOST,
                    port,
                    process,
                    timeout=self._connect_timeout,
                    server_name=server_name,
                )
                return str(new_record["url"])
        except Timeout as exc:
            raise TimeoutError(
                f"Timed out acquiring MCP shared lock for {server_name!r} ({lock_path})"
            ) from exc

    async def start_touch(self, server_name: str) -> None:
        identity, json_path, _ = self._paths(server_name)
        async with self._async_lock:
            refs = self._touch_refs.get(identity, 0) + 1
            self._touch_refs[identity] = refs
            if refs == 1:
                self._touch_tasks[identity] = asyncio.create_task(
                    self._touch_loop(identity, json_path),
                    name=f"mcp-touch-{identity}",
                )

    async def stop_touch(self, server_name: str) -> None:
        identity, _, _ = self._paths(server_name)
        async with self._async_lock:
            refs = self._touch_refs.get(identity, 0)
            if refs <= 1:
                self._touch_refs.pop(identity, None)
                task = self._touch_tasks.pop(identity, None)
                if task is not None:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            else:
                self._touch_refs[identity] = refs - 1

    async def _touch_loop(self, identity: str, json_path: Path) -> None:
        interval = max(1.0, self._server_ttl / 5)
        try:
            while True:
                if json_path.is_file():
                    now = time.time()
                    os.utime(json_path, (now, now))
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise

    async def aclose(self) -> None:
        async with self._async_lock:
            tasks = list(self._touch_tasks.values())
            self._touch_tasks.clear()
            self._touch_refs.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
