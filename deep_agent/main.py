from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from deep_agent.api.v1 import api_router
from deep_agent.capabilities.mcp.resolver import load_mcp_capabilities
from deep_agent.core.context import AppContext, AppContextDep, reset_context
from deep_agent.core.exceptions import AgentNotFoundError, DeepAgentError
from deep_agent.core.logging import configure_logging
from deep_agent.core.paths import REPO_ROOT
from deep_agent.options import OPTION


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


@app.middleware("http")
async def disable_console_cache(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/console" or path.startswith("/console/"):
        response.headers["Cache-Control"] = "no-store"
    return response


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
async def info(ctx: AppContextDep) -> dict[str, object]:
    mcp_caps = await load_mcp_capabilities(ctx.stores)
    capability_names = ctx.registry.names() + [cap.name for cap in mcp_caps]
    return {
        "name": OPTION.agent.app_name,
        "capabilities": capability_names,
        "docs": "/docs",
    }


@app.get("/", include_in_schema=False)
async def console_redirect() -> RedirectResponse:
    return RedirectResponse(url="/console/#/workspace", status_code=307)


@app.get("/console", include_in_schema=False)
async def console_trailing_slash_redirect() -> RedirectResponse:
    # Keep a reverse proxy's public service prefix instead of exposing the
    # internal Pod host used by StaticFiles' automatic slash redirect.
    return RedirectResponse(url="./console/", status_code=307)


_frontend_dir = REPO_ROOT / "frontend"
_console_dir = _frontend_dir / "console"
_vendor_dir = _frontend_dir / "vendor"
if (_console_dir / "index.html").is_file():
    app.mount("/console", StaticFiles(directory=str(_console_dir), html=True), name="console")
if _vendor_dir.is_dir():
    app.mount("/vendor", StaticFiles(directory=str(_vendor_dir)), name="vendor")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
