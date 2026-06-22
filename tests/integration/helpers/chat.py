from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic_ai.models.test import TestModel

from deep_agent.api.v1 import api_router
from deep_agent.capabilities.mcp.session_pool import McpSessionPool
from deep_agent.capabilities.registry import Capability, CapabilityRegistry
from deep_agent.capabilities.mcp.toolset_builder import build_mcp_toolset
from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.memory import InMemorySessionStore

HEADER_ECHO_SERVER = (
    Path(__file__).resolve().parents[1] / "fixtures" / "header_echo_server.py"
)
ECHO_TOOL_NAME = "echo_echo_request_headers"


def create_test_app(*, record_path: Path | None = None) -> FastAPI:
    """Build a minimal FastAPI app without production lifespan/DB side effects."""
    test_app = FastAPI()
    test_app.include_router(api_router)
    configure_test_app(test_app, record_path=record_path)
    return test_app


def echo_capability(*, record_path: Path | None = None) -> Capability:
    env = dict(os.environ)
    if record_path is not None:
        env["MCP_HEADER_RECORD"] = str(record_path)
    toolset = build_mcp_toolset(
        "echo",
        "stdio",
        {
            "command": sys.executable,
            "args": [str(HEADER_ECHO_SERVER)],
            "env": env,
        },
    )
    return Capability(
        name="mcp:echo",
        kind="mcp",
        toolset=toolset,
        description="Test MCP header echo server.",
        tools=[],
    )


def configure_test_app(
    app: FastAPI,
    *,
    record_path: Path | None = None,
) -> CapabilityRegistry:
    """Attach in-memory dependencies and a single header-echo MCP capability."""
    ctx = AppContext.for_testing(
        registry=CapabilityRegistry(),
        store=InMemorySessionStore(),
        mcp_session_pool=McpSessionPool(),
    )
    init_context(ctx)
    ctx.registry.register(echo_capability(record_path=record_path))
    return ctx.registry


@contextmanager
def test_chat_client(
    *,
    record_path: Path | None = None,
    call_tools: list[str] | None = None,
) -> Iterator[tuple[TestClient, Path]]:
    """Yield a TestClient whose agent uses TestModel and the header-echo MCP server."""
    record_file = record_path or Path(tempfile.mkstemp(suffix=".json")[1])
    test_model = TestModel(
        call_tools=call_tools or [ECHO_TOOL_NAME],
        custom_output_text="ok",
    )
    app = create_test_app(record_path=record_file)
    try:
        with patch("deep_agent.agent.factory._resolve_model", return_value=test_model):
            with TestClient(app) as client:
                yield client, record_file
    finally:
        reset_context()
