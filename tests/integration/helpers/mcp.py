from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets._dynamic import DynamicToolset
from pydantic_ai.usage import RunUsage

from deep_agent.agent.deps import AgentDeps
from deep_agent.capabilities.mcp.toolset_builder import build_mcp_toolset

HEADER_ECHO_SERVER = (
    Path(__file__).resolve().parents[1] / "fixtures" / "header_echo_server.py"
)
ECHO_TOOL_NAME = "echo_echo_request_headers"


def free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_tcp(host: str, port: int, *, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"Timed out waiting for {host}:{port}")


@contextmanager
def streamable_http_echo_server(
    *,
    record_path: Path | None = None,
) -> Iterator[str]:
    port = free_tcp_port()
    env = None
    if record_path is not None:
        env = {**dict(os.environ), "MCP_HEADER_RECORD": str(record_path)}
    proc = subprocess.Popen(
        [
            sys.executable,
            str(HEADER_ECHO_SERVER),
            "--transport",
            "streamable-http",
            "--port",
            str(port),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    try:
        wait_for_tcp("127.0.0.1", port)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def _run_context(request_headers: dict[str, str]) -> RunContext[AgentDeps]:
    return RunContext(
        deps=AgentDeps(session_id="mcp-header-test", request_headers=request_headers),
        model=TestModel(),
        usage=RunUsage(),
    )


async def call_echo_tool(toolset: Any, request_headers: dict[str, str]) -> dict[str, str]:
    """Call MCP echo tool directly (MCP-layer integration, not full HTTP path)."""
    ctx = _run_context(request_headers)
    active = await toolset.for_run(ctx)

    async with active:
        tools = await active.get_tools(ctx)
        if ECHO_TOOL_NAME not in tools:
            raise AssertionError(f"Expected tool {ECHO_TOOL_NAME!r}, got {sorted(tools)}")

        raw = await active.call_tool(ECHO_TOOL_NAME, {}, ctx, tools[ECHO_TOOL_NAME])
        if isinstance(raw, str):
            return json.loads(raw)
        if isinstance(raw, dict):
            return {str(k).lower(): str(v) for k, v in raw.items()}
        raise AssertionError(f"Unexpected tool result type: {type(raw)!r} {raw!r}")


def url_echo_toolset(
    url: str,
    static_headers: dict[str, str] | None = None,
    *,
    request_context_mode: str | None = None,
):
    config: dict[str, Any] = {"url": url}
    if static_headers:
        config["headers"] = static_headers
    if request_context_mode:
        config["request_context"] = {"mode": request_context_mode}
    return build_mcp_toolset("echo", "url", config)
