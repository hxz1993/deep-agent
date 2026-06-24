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
from deep_agent.db.stores.bundle import DataStores
from deep_agent.schemas.mcp import McpServerCreate
from tests.helpers.sqlite_stores import make_sqlite_stores_sync

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


async def _seed_mcp_rows(stores: DataStores, rows: list[_McpRow]) -> None:
    for row in rows:
        await stores.mcp.create_unique(
            McpServerCreate(
                name=row.name,
                transport=row.transport,
                config=row.config,
                description=row.description,
                enabled=row.enabled,
            )
        )


def testing_data_stores(
    *,
    record_path: Path | None = None,
    mcp_rows: list[_McpRow] | None = None,
) -> tuple[DataStores, Any, Any]:
    rows = mcp_rows if mcp_rows is not None else [echo_mcp_row(record_path=record_path)]
    stores, engine, session_factory = make_sqlite_stores_sync()
    import asyncio

    asyncio.run(_seed_mcp_rows(stores, rows))
    return stores, engine, session_factory


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
    db_engine: Any | None = None,
    db_session_factory: Any | None = None,
) -> AppContext:
    """Attach sqlite-backed dependencies and MCP rows resolved at use time."""
    if stores is None:
        stores, db_engine, db_session_factory = testing_data_stores(record_path=record_path)
    ctx = AppContext.for_testing(
        registry=build_registry(),
        stores=stores,
        mcp_session_pool=McpSessionPool(),
        db_engine=db_engine,
        db_session_factory=db_session_factory,
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
    if stores is None:
        stores, db_engine, db_session_factory = testing_data_stores(record_path=record_file)
    else:
        db_engine = None
        db_session_factory = None
    configure_test_app(
        app,
        record_path=record_file,
        stores=stores,
        db_engine=db_engine,
        db_session_factory=db_session_factory,
    )
    try:
        with patch("deep_agent.agent.factory._resolve_model", return_value=test_model):
            with TestClient(app) as client:
                yield client, record_file
    finally:
        reset_context()
