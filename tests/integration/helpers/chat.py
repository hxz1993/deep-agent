from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic_ai.models.test import TestModel

from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.session_pool import McpSessionPool
from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.db.stores import InMemorySessionStore
from deep_agent.db.stores.bundle import DataStores

HEADER_ECHO_SERVER = (
    Path(__file__).resolve().parents[1] / "fixtures" / "header_echo_server.py"
)
ECHO_TOOL_NAME = "echo_echo_request_headers"


@dataclass
class _McpRow:
    name: str
    transport: str
    config: dict[str, Any]
    description: str | None = None
    enabled: bool = True


class InMemoryMcpStore:
    """Minimal MCP store for integration tests (list_all / get_by_name only)."""

    def __init__(self, rows: list[_McpRow] | None = None) -> None:
        self._rows = list(rows or [])

    async def list_all(self) -> list[_McpRow]:
        return list(self._rows)

    async def get_by_name(self, name: str) -> _McpRow | None:
        for row in self._rows:
            if row.name == name:
                return row
        return None


def echo_mcp_row(*, record_path: Path | None = None) -> _McpRow:
    env = dict(os.environ)
    if record_path is not None:
        env["MCP_HEADER_RECORD"] = str(record_path)
    return _McpRow(
        name="echo",
        transport="stdio",
        config={
            "command": sys.executable,
            "args": [str(HEADER_ECHO_SERVER)],
            "env": env,
        },
        description="Test MCP header echo server.",
    )


def testing_data_stores(
    *,
    record_path: Path | None = None,
    mcp_rows: list[_McpRow] | None = None,
) -> DataStores:
    rows = mcp_rows if mcp_rows is not None else [echo_mcp_row(record_path=record_path)]
    return DataStores(
        chat_session=None,
        message=None,
        coordinator=InMemorySessionStore(),
        agent=None,
        mcp=InMemoryMcpStore(rows),
        ui_tool=None,
    )


def create_test_app(*, record_path: Path | None = None) -> FastAPI:
    """Build a minimal FastAPI app without production lifespan/DB side effects."""
    test_app = FastAPI()
    test_app.include_router(api_router)
    configure_test_app(test_app, record_path=record_path)
    return test_app


def configure_test_app(
    app: FastAPI,
    *,
    record_path: Path | None = None,
    stores: DataStores | None = None,
) -> AppContext:
    """Attach in-memory dependencies and MCP rows resolved at use time."""
    ctx = AppContext.for_testing(
        registry=build_registry(),
        stores=stores or testing_data_stores(record_path=record_path),
        mcp_session_pool=McpSessionPool(),
    )
    init_context(ctx)
    return ctx


@contextmanager
def test_chat_client(
    *,
    record_path: Path | None = None,
    call_tools: list[str] | None = None,
    stores: DataStores | None = None,
) -> Iterator[tuple[TestClient, Path]]:
    """Yield a TestClient whose agent uses TestModel and the header-echo MCP server."""
    record_file = record_path or Path(tempfile.mkstemp(suffix=".json")[1])
    test_model = TestModel(
        call_tools=call_tools or [ECHO_TOOL_NAME],
        custom_output_text="ok",
    )
    app = FastAPI()
    app.include_router(api_router)
    configure_test_app(app, record_path=record_file, stores=stores)
    try:
        with patch("deep_agent.agent.factory._resolve_model", return_value=test_model):
            with TestClient(app) as client:
                yield client, record_file
    finally:
        reset_context()
