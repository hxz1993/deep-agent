from __future__ import annotations

from pydantic_ai.messages import ToolCallPart


def deep_agent_file_key(call: ToolCallPart) -> str | None:
    """Map only known, read-only tools whose later result supersedes the old one."""
    try:
        args = call.args_as_dict()
    except Exception:
        return None
    if call.tool_name == "read_file":
        path = args.get("path")
        return f"file:{path}" if isinstance(path, str) and path else None
    if call.tool_name == "read_mcp_resource":
        server = args.get("server_name") or args.get("server")
        uri = args.get("uri")
        if isinstance(server, str) and isinstance(uri, str) and server and uri:
            return f"mcp-resource:{server}:{uri}"
    return None
