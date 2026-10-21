"""Disposable real HTTP server used against current and frozen old source trees.

No production profile or process is loaded. The explicit root owns all data;
the caller terminates the process and deletes that root in its finally block.
"""
from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.mcp import MCPToolset  # Initialize the SDK integration before product plugin imports.
import uvicorn

from deep_agent.api.v1 import api_router
from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.files.storage import LocalFileStore
from deep_agent.security import ConsoleAdminAuthorizer
from deep_agent.skills.runtime import SkillRuntimeLibrary
from tests.helpers.sqlite_stores import make_sqlite_stores


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--console")
    args = parser.parse_args()
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    ready = {}

    @asynccontextmanager
    async def lifespan(app):
        stores, engine, factory = await make_sqlite_stores(f'sqlite+aiosqlite:///{root / "config.sqlite"}')
        files = LocalFileStore(root / "files")
        await files.initialize()
        context = AppContext.for_testing(stores=stores, db_engine=engine,
            db_session_factory=factory, file_store=files,
            skill_runtime=SkillRuntimeLibrary(root / "runtime", file_store=files),
            console_admin_authorizer=ConsoleAdminAuthorizer(AsyncMock(return_value=True)))
        ready["model_id"] = (await stores.llm_model.list_all())[0].id
        init_context(context)
        def answer(messages, info):
            if info.output_tools:
                return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,
                    {"text": "structured compatibility proof"}, tool_call_id="compatibility-output")])
            return ModelResponse(parts=[TextPart("compatibility plain reply")])
        model = FunctionModel(answer)
        try:
            with patch("deep_agent.agent.factory._resolve_model", return_value=model), patch(
                    "deep_agent.agent.runner.AgentRunner._generate_model_title", new_callable=AsyncMock):
                yield
        finally:
            reset_context()
            await context.aclose()

    app = FastAPI(lifespan=lifespan)
    app.include_router(api_router)
    if args.console:
        app.mount("/console", StaticFiles(directory=args.console, html=True), name="console")

    @app.get("/_test/ready")
    async def health():
        return ready

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="error", access_log=False)


if __name__ == "__main__":
    main()
