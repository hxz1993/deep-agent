from __future__ import annotations

from pydantic_ai.messages import ToolCallPart


def deep_agent_file_key(call: ToolCallPart) -> str | None:
    """Map only known, read-only tools whose later result supersedes the old one."""
    try:
        args = call.args_as_dict()
    except Exception:
        return None
    if call.tool_name == "read_file":
        resource = args.get("resource")
        if not isinstance(resource, dict):
            return None
        source = resource.get("type")
        if source == "attachment":
            identity = resource.get("file_id")
        elif source == "skill":
            identity = f"{resource.get('skill_code')}:{resource.get('path')}"
        else:
            return None
        if not isinstance(identity, str) or not identity:
            return None
        return (
            f"file:{source}:{identity}:{args.get('sheet') or ''}:"
            f"{args.get('offset') or 1}:{args.get('limit') or ''}:"
            f"{args.get('char_offset') or 0}"
        )
    # History created before v0.3.23 can still contain the retired attachment
    # tool names.  Keep recognizing their pagination keys during compaction,
    # without re-registering those tools for new model runs.
    if call.tool_name in {
        "read_text_attachment",
        "read_docx",
        "read_xlsx",
    }:
        file_id = args.get("file_id")
        if not isinstance(file_id, str) or not file_id:
            return None
        sheet = args.get("sheet") if call.tool_name == "read_xlsx" else None
        return (
            f"legacy-attachment:{call.tool_name}:{file_id}:{sheet or ''}:"
            f"{args.get('offset') or 1}:{args.get('limit') or ''}"
        )
    if call.tool_name == "read_mcp_resource":
        server = args.get("server_name") or args.get("server")
        uri = args.get("uri")
        if isinstance(server, str) and isinstance(uri, str) and server and uri:
            return f"mcp-resource:{server}:{uri}"
    if call.tool_name == "read_tool_result":
        handle = args.get("handle")
        if not isinstance(handle, str) or not handle:
            return None
        return (
            f"tool-result:{handle}:{args.get('offset') or 0}:"
            f"{args.get('limit') or ''}:{bool(args.get('from_end', False))}:"
            f"{args.get('pattern') or ''}:{args.get('char_offset') or 0}:"
            f"{args.get('end_offset')}"
        )
    return None
