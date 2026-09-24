from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_oauth2_redirect_html
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from deep_agent import __version__
from deep_agent.api.v1 import api_router
from deep_agent.capabilities.catalog import selectable_tool_package_codes
from deep_agent.core.context import AppContext, AppContextDep, reset_context
from deep_agent.core.exceptions import AgentNotFoundError, DeepAgentError
from deep_agent.core.logging import configure_logging
from deep_agent.db.reference_integrity import ReferenceConflict
from deep_agent.core.paths import CONSOLE_ROOT
from deep_agent.options import OPTION
from deep_agent.diagnostics.runtime import observe_lifespan


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    ctx = await AppContext.bootstrap()
    yield
    await ctx.aclose()
    reset_context()


app = FastAPI(
    title="deep-agent",
    version=__version__,
    description="General-purpose AI agent backend.",
    lifespan=observe_lifespan(lifespan, "api"),
    docs_url=None,
    redoc_url=None,
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


@app.exception_handler(ReferenceConflict)
async def reference_conflict_handler(request: Request, exc: ReferenceConflict) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


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
    tool_package_codes = sorted(
        await selectable_tool_package_codes(ctx.registry, ctx.stores)
    )
    return {
        "name": "DeepAgent",
        "version": __version__,
        # Keep the public key for compatibility; values are product ToolPackage codes.
        "capabilities": tool_package_codes,
        "docs": "/docs",
    }


def _deployment_aware_swagger_html() -> HTMLResponse:
    """Render Swagger UI without assuming that DeepAgent is mounted at `/`."""

    return HTMLResponse(
        f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link type="text/css" rel="stylesheet"
        href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
  <link id="docs-icon" rel="icon" type="image/svg+xml">
  <title>DeepAgent · 接口文档</title>
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>
    const serviceBasePath = window.location.pathname.replace(/\\/docs\\/?$/, "");
    const style = document.createElement("link");
    style.rel = "stylesheet";
    style.href = `${{serviceBasePath}}/console/api-docs.css?v=20260924-1`;
    document.head.appendChild(style);
    document.getElementById("docs-icon").href = `${{serviceBasePath}}/console/favicon.svg`;
    const ui = SwaggerUIBundle({{
      url: `${{serviceBasePath}}{app.openapi_url}`,
      dom_id: "#swagger-ui",
      layout: "BaseLayout",
      deepLinking: true,
      filter: true,
      docExpansion: "list",
      defaultModelsExpandDepth: -1,
      supportedSubmitMethods: [],
      validatorUrl: null,
      showExtensions: false,
      showCommonExtensions: false,
      oauth2RedirectUrl:
        `${{window.location.origin}}${{serviceBasePath}}{app.swagger_ui_oauth2_redirect_url}`,
      presets: [
        SwaggerUIBundle.presets.apis,
        SwaggerUIBundle.SwaggerUIStandalonePreset
      ]
    }});
  </script>
</body>
</html>
"""
    )


@app.get("/docs", include_in_schema=False)
@app.get("/docs/", include_in_schema=False)
async def api_docs() -> HTMLResponse:
    return _deployment_aware_swagger_html()


@app.get(app.swagger_ui_oauth2_redirect_url, include_in_schema=False)
async def swagger_ui_oauth2_redirect() -> HTMLResponse:
    return get_swagger_ui_oauth2_redirect_html()


@app.get("/", include_in_schema=False)
async def console_redirect() -> RedirectResponse:
    return RedirectResponse(url="/console/#/workspace", status_code=307)


@app.get("/console", include_in_schema=False)
async def console_trailing_slash_redirect() -> RedirectResponse:
    # Keep a reverse proxy's public service prefix instead of exposing the
    # internal Pod host used by StaticFiles' automatic slash redirect.
    return RedirectResponse(url="./console/", status_code=307)


_console_dir = CONSOLE_ROOT
_vendor_dir = CONSOLE_ROOT / "vendor"
if (_console_dir / "index.html").is_file():
    app.mount("/console", StaticFiles(directory=str(_console_dir), html=True), name="console")
if _vendor_dir.is_dir():
    app.mount("/vendor", StaticFiles(directory=str(_vendor_dir)), name="vendor")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
