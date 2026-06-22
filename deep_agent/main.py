from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from deep_agent.api.v1 import api_router
from deep_agent.core.context import AppContext, AppContextDep, reset_context
from deep_agent.core.exceptions import AgentNotFoundError, DeepAgentError
from deep_agent.core.logging import configure_logging
from deep_agent.core.paths import REPO_ROOT


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    ctx = await AppContext.bootstrap()
    yield
    await ctx.aclose()
    reset_context()


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
async def root(ctx: AppContextDep) -> dict[str, object]:
    return {
        "name": ctx.settings.app_name,
        "model": ctx.settings.model,
        "capabilities": ctx.registry.names(),
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
