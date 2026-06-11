from __future__ import annotations

from datetime import datetime, timezone

import httpx
from pydantic_ai import FunctionToolset

from app.capabilities.registry import Capability, ToolInfo

_HTTP_BODY_LIMIT = 4000


def build_native_capability() -> Capability:
    """Build the built-in native toolset.

    To add a native tool: define a function below and decorate it. Its type
    hints become the tool's JSON schema automatically. No other file changes.
    """
    toolset = FunctionToolset(
        instructions=(
            "You can call get_current_time for the current UTC time, and "
            "http_get to fetch the text body of a URL."
        )
    )

    @toolset.tool_plain
    def get_current_time() -> str:
        """Return the current date and time in UTC as an ISO 8601 string."""
        return datetime.now(timezone.utc).isoformat()

    @toolset.tool_plain
    async def http_get(url: str, timeout_seconds: float = 10.0) -> str:
        """Fetch a URL via HTTP GET and return its response body.

        The body is truncated to keep tool output from blowing up the context.
        """
        async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text[:_HTTP_BODY_LIMIT]

    return Capability(
        name="native",
        kind="native",
        toolset=toolset,
        description="Built-in utility tools (time, HTTP GET).",
        tools=[
            ToolInfo("get_current_time", "Get the current UTC time."),
            ToolInfo("http_get", "Fetch a URL via HTTP GET (body truncated)."),
        ],
    )
