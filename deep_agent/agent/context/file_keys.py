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
    if call.tool_name in {
        "read_text_attachment",
        "read_docx",
        "read_xlsx",
    }:
        file_id = args.get("file_id")
        if not isinstance(file_id, str) or not file_id:
            return None
        offset = args.get("offset", 1)
        limit = args.get("limit")
        sheet = args.get("sheet") if call.tool_name == "read_xlsx" else None
        return (
            f"attachment:{call.tool_name}:{file_id}:"
            f"{sheet or ''}:{offset or 1}:{limit or ''}"
        )
    return None
