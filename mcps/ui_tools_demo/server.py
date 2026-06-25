"""Demo MCP server exposing RFC 0001 ui_tools_list discovery."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("ui_tools_demo")


@mcp.tool(name="ui_tools_list", description="Return client-executed UI tool definitions for deep-agent.")
def ui_tools_list() -> str:
    manifest = {
        "protocol_version": "1.0",
        "mcp_server": "ui_tools_demo",
        "tools": [
            {
                "name": "pick_color",
                "description": "Ask the user to pick a color in the client UI.",
                "parameters_json_schema": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string"},
                        "options": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                        },
                    },
                    "required": ["prompt", "options"],
                },
                "widget": "choice",
                "result_schema": {
                    "type": "object",
                    "properties": {"selected": {"type": "string"}},
                    "required": ["selected"],
                },
            }
        ],
    }
    return json.dumps(manifest, ensure_ascii=False)


if __name__ == "__main__":
    import sys
    from pathlib import Path

    _root = Path(__file__).resolve().parents[2]
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
    from mcps._run import run_fastmcp

    run_fastmcp(mcp, prog="ui_tools_demo")
