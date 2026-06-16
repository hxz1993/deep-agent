from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from deep_agent.api.v1.router import api_router
from deep_agent.core.paths import REPO_ROOT
from deep_agent.capabilities import build_registry, load_and_register_mcp
from deep_agent.core.config import get_settings
from deep_agent.core.exceptions import AgentNotFoundError, DeepAgentError
from deep_agent.core.logging import configure_logging, get_logger
from deep_agent.db.engine import close_db, create_engine
from deep_agent.memory import InMemorySessionStore, PostgresSessionStore


logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    app.state.settings = settings
    app.state.registry = build_registry(settings)

    if settings.database_url:
        engine, session_factory = create_engine(settings.database_url)
        app.state.db_engine = engine
        app.state.db_session_factory = session_factory
        app.state.store = PostgresSessionStore(session_factory)
        try:
            async with session_factory() as session:
                await load_and_register_mcp(app.state.registry, session)
        except Exception:
            logger.exception(
                "Failed to load MCP servers from database. "
                "Run `alembic upgrade head` if mcp_servers is missing."
            )
        logger.info("%s started (model=%s, database=postgres)", settings.app_name, settings.model)
    else:
        app.state.db_engine = None
        app.state.db_session_factory = None
        app.state.store = InMemorySessionStore()
        logger.warning("Database not configured; MCP servers will not be loaded.")
        logger.info("%s started (model=%s, database=in-memory)", settings.app_name, settings.model)

    yield

    if app.state.db_engine is not None:
        await close_db(app.state.db_engine)


app = FastAPI(
    title="deep-agent",
    version="0.1.0",
    description="General-purpose AI agent backend.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.exception_handler(AgentNotFoundError)
async def agent_not_found_handler(request: Request, exc: AgentNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": exc.__class__.__name__, "detail": str(exc)})


@app.exception_handler(DeepAgentError)
async def deep_agent_error_handler(request: Request, exc: DeepAgentError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": exc.__class__.__name__, "detail": str(exc)})


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/info", tags=["meta"])
async def root(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    registry = request.app.state.registry
    return {
        "name": settings.app_name,
        "model": settings.model,
        "capabilities": registry.names(),
        "docs": "/docs",
    }

_frontend_dir = REPO_ROOT / "frontend"
_index_html = _frontend_dir / "index.html"
if _index_html.is_file():
    # mount index.html as root
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
