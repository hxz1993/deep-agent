from __future__ import annotations

import json
import tempfile
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from pydantic_ai.models.test import TestModel

from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from deep_agent.db.stores.bundle import DataStores
from deep_agent.options import OPTION
from deep_agent.schemas.mcp import McpServerCreate
from tests.helpers.sqlite_stores import make_sqlite_stores, make_sqlite_stores_sync
from tests.integration.helpers.mcp import streamable_http_echo_server

ECHO_TOOL_NAME = "echo_echo_request_headers"


def _disable_title_generation_for_tests() -> None:
    """Non-title integration tests must not spawn background title LLM calls."""
    OPTION.agent.title_model = ""
    OPTION.agent.title_model_api_key = ""


async def aclose_test_context(*, engine: Any | None = None) -> None:
    try:
        ctx = get_context()
    except RuntimeError:
        reset_context()
    else:
        await ctx.mcp_lease_manager.close_all()
        await ctx.event_persister.aclose()
        reset_context()
    if engine is not None:
        await engine.dispose()


def parse_sse_events(raw: str) -> list[dict]:
    events: list[dict] = []
    event_type: str | None = None
    for line in raw.splitlines():
        if line.startswith("event:"):
            event_type = line.removeprefix("event:").strip()
        elif line.startswith("data:") and event_type is not None:
            payload = json.loads(line.removeprefix("data:").strip())
            events.append({"type": event_type, **payload})
            event_type = None
    return events


def parse_sse_buffer(buffer: str) -> tuple[list[dict], str]:
    """Parse complete SSE blocks from buffer; return events and trailing partial buffer."""
    events: list[dict] = []
    while True:
        match = None
        for sep in ("\r\n\r\n", "\n\n"):
            idx = buffer.find(sep)
            if idx != -1:
                match = (idx, len(sep))
                break
        if match is None:
            break
        idx, width = match
        block = buffer[:idx]
        buffer = buffer[idx + width :]
        event_type: str | None = None
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_type = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").strip())
        if event_type and data_lines:
            payload = json.loads("\n".join(data_lines))
            events.append({"type": event_type, **payload})
    return events, buffer


@dataclass
class _McpRow:
    name: str
    transport: str
    config: dict[str, Any]
    description: str | None = None
    enabled: bool = True


def echo_mcp_row(url: str) -> _McpRow:
    return _McpRow(
        name="echo",
        transport="url",
        config={"url": url},
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


async def testing_data_stores_async(
    *,
    mcp_rows: list[_McpRow] | None = None,
) -> tuple[DataStores, Any, Any]:
    rows = [] if mcp_rows is None else mcp_rows
    stores, engine, session_factory = await make_sqlite_stores()
    if rows:
        await _seed_mcp_rows(stores, rows)
    return stores, engine, session_factory


def testing_data_stores(
    *,
    mcp_rows: list[_McpRow] | None = None,
) -> tuple[DataStores, Any, Any]:
    rows = [] if mcp_rows is None else mcp_rows
    stores, engine, session_factory = make_sqlite_stores_sync()
    if rows:
        import asyncio

        asyncio.run(_seed_mcp_rows(stores, rows))
    return stores, engine, session_factory


def create_test_app(*, mcp_url: str) -> FastAPI:
    """Build a minimal FastAPI app without production lifespan/DB side effects."""
    test_app = FastAPI()
    test_app.include_router(api_router)
    configure_test_app(test_app, mcp_url=mcp_url)
    return test_app


def configure_test_app(
    app: FastAPI,
    *,
    mcp_url: str,
    stores: DataStores | None = None,
    db_engine: Any | None = None,
    db_session_factory: Any | None = None,
) -> AppContext:
    """Attach sqlite-backed dependencies and MCP rows resolved at use time."""
    if stores is None:
        stores, db_engine, db_session_factory = testing_data_stores(mcp_rows=[echo_mcp_row(mcp_url)])
    ctx = AppContext.for_testing(
        registry=build_registry(),
        stores=stores,
        mcp_lease_manager=McpLeaseManager(),
        db_engine=db_engine,
        db_session_factory=db_session_factory,
    )
    init_context(ctx)
    _disable_title_generation_for_tests()
    return ctx


@asynccontextmanager
async def async_echo_chat_app(
    *,
    record_path: Path | None = None,
) -> AsyncIterator[tuple[FastAPI, Path]]:
    """Async variant of echo_chat_app for IsolatedAsyncioTestCase."""
    record_file = record_path or Path(tempfile.mkstemp(suffix=".json")[1])
    db_engine: Any | None = None
    with streamable_http_echo_server(record_path=record_file) as mcp_url:
        app = FastAPI()
        app.include_router(api_router)
        stores, db_engine, db_session_factory = await testing_data_stores_async(
            mcp_rows=[echo_mcp_row(mcp_url)],
        )
        configure_test_app(
            app,
            mcp_url=mcp_url,
            stores=stores,
            db_engine=db_engine,
            db_session_factory=db_session_factory,
        )
        try:
            yield app, record_file
        finally:
            await aclose_test_context(engine=db_engine)


@contextmanager
def echo_chat_app(
    *,
    record_path: Path | None = None,
    call_tools: list[str] | None = None,
    stores: DataStores | None = None,
) -> Iterator[tuple[FastAPI, Path]]:
    """Minimal app + header-echo MCP; prefer AsyncClient for /chat/stream (TestClient.stream hangs)."""
    record_file = record_path or Path(tempfile.mkstemp(suffix=".json")[1])
    with streamable_http_echo_server(record_path=record_file) as mcp_url:
        app = FastAPI()
        app.include_router(api_router)
        if stores is None:
            stores, db_engine, db_session_factory = testing_data_stores(mcp_rows=[echo_mcp_row(mcp_url)])
        else:
            db_engine = None
            db_session_factory = None
        configure_test_app(
            app,
            mcp_url=mcp_url,
            stores=stores,
            db_engine=db_engine,
            db_session_factory=db_session_factory,
        )
        try:
            yield app, record_file
        finally:
            reset_context()


async def read_chat_stream(
    app: FastAPI,
    *,
    json_body: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> str:
    """Consume SSE from /chat/stream via AsyncClient (TestClient.stream blocks indefinitely)."""
    transport = ASGITransport(app=app)
    with mock_request_disconnect():
        async with AsyncClient(transport=transport, base_url="http://test", timeout=30.0) as client:
            async with client.stream(
                "POST",
                "/api/v1/chat/stream",
                json=json_body,
                headers=headers or {},
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    raise AssertionError(f"stream failed {response.status_code}: {body!r}")
                return "".join([chunk async for chunk in response.aiter_text()])


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
    with streamable_http_echo_server(record_path=record_file) as mcp_url:
        app = FastAPI()
        app.include_router(api_router)
        if stores is None:
            stores, db_engine, db_session_factory = testing_data_stores(mcp_rows=[echo_mcp_row(mcp_url)])
        else:
            db_engine = None
            db_session_factory = None
        configure_test_app(
            app,
            mcp_url=mcp_url,
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


@contextmanager
def sqlite_chat_app() -> Iterator[FastAPI]:
    """Minimal chat app with sqlite stores and no MCP servers."""
    app = FastAPI()
    app.include_router(api_router)
    stores, engine, session_factory = testing_data_stores(mcp_rows=[])
    configure_test_app(
        app,
        mcp_url="http://unused",
        stores=stores,
        db_engine=engine,
        db_session_factory=session_factory,
    )
    try:
        yield app
    finally:
        reset_context()


@contextmanager
def patched_model_client(app: FastAPI, model: Any) -> Iterator[TestClient]:
    with patch("deep_agent.agent.factory._resolve_model", return_value=model):
        with TestClient(app) as client:
            yield client


class DisconnectFlag:
    """Controllable stand-in for Request.is_disconnected in ASGI tests."""

    def __init__(self) -> None:
        self.disconnected = False

    async def __call__(self) -> bool:
        return self.disconnected


@contextmanager
def mock_request_disconnect(
    *,
    disconnected: bool = False,
) -> Iterator[DisconnectFlag]:
    """Patch Starlette disconnect probe; real ASGITransport blocks on is_disconnected."""
    flag = DisconnectFlag()
    flag.disconnected = disconnected

    async def _is_disconnected(self) -> bool:  # noqa: ARG001
        return await flag()

    with patch(
        "starlette.requests.Request.is_disconnected",
        new=_is_disconnected,
    ):
        yield flag


def parse_sse_events_incremental(chunks: list[str]) -> list[dict]:
    """Parse SSE from incremental chunk list (disconnect scenarios)."""
    buffer = ""
    events: list[dict] = []
    for chunk in chunks:
        buffer += chunk
        parsed, buffer = parse_sse_buffer(buffer)
        events.extend(parsed)
    if buffer.strip():
        parsed, _ = parse_sse_buffer(buffer + "\n\n")
        events.extend(parsed)
    return events
