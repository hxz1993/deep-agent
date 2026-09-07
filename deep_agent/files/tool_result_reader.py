"""Bounded, resumable reads of the existing authorized overflow store."""

from __future__ import annotations

from typing import Any

from pydantic_ai import ModelRetry
from pydantic_ai_harness.tool_output_limits import OverflowStore

from deep_agent.agent.attachment_extractors import AttachmentParseError, paginate_items

TOOL_RESULT_DEFAULT_LINES = 400
TOOL_RESULT_MAX_LINES = 2_000


async def read_stored_tool_result(
    store: OverflowStore,
    handle: str,
    *,
    offset: int = 0,
    limit: int = TOOL_RESULT_DEFAULT_LINES,
    from_end: bool = False,
    pattern: str | None = None,
    char_offset: int = 0,
    end_offset: int | None = None,
) -> dict[str, Any]:
    """Preserve line slicing/filtering, with a cursor for partial long lines.

    Offsets count filtered lines from zero. A tail window stays in document order;
    its next_read finishes that window forwards, while previous_read visits older
    lines. end_offset bounds that continuation and is exclusive.
    """
    if offset < 0 or char_offset < 0 or limit < 1:
        raise ModelRetry("offset/char_offset must be >= 0; limit must be >= 1.")
    if end_offset is not None and (from_end or end_offset < offset):
        raise ModelRetry("end_offset must be >= offset and requires from_end=false.")
    limit = min(limit, TOOL_RESULT_MAX_LINES)
    try:
        data = await store.read(handle)
    except OSError:
        return {"error": "Stored tool result unavailable. Use the exact authorized overflow handle or rerun the original tool."}

    lines = data.decode("utf-8", errors="replace").splitlines()
    if pattern is not None:
        lines = [line for line in lines if pattern in line]
    total = len(lines)
    end = max(0, total - offset) if from_end else min(total, end_offset if end_offset is not None else total)
    start = max(0, end - limit) if from_end else offset
    try:
        page = paginate_items(
            lines[start : min(end, start + limit)],
            total_count=end,
            offset=start + 1,
            limit=limit,
            char_offset=char_offset,
        )
    except AttachmentParseError as exc:
        raise ModelRetry(str(exc)) from exc

    next_read = None
    if page.next_offset is not None:
        next_read = {
            "handle": handle, "offset": page.next_offset - 1,
            "char_offset": page.next_char_offset, "limit": limit,
            "from_end": False, "pattern": pattern, "end_offset": end,
        }
    previous_read = None
    if from_end and start > 0:
        previous_read = {
            "handle": handle, "offset": total - start, "limit": limit,
            "from_end": True, "pattern": pattern,
        }
    return {
        "handle": handle, "content": page.content,
        "offset": start, "char_offset": char_offset, "limit": limit,
        "total_count": total, "returned_count": page.returned_count,
        "has_more": page.has_more, "truncated_by": page.truncated_by,
        "next_read": next_read, "previous_read": previous_read,
    }
