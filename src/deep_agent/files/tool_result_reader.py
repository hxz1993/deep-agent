"""Bounded, resumable reads of the existing authorized overflow store."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic_ai import ModelRetry
from pydantic_ai_harness.tool_output_limits import OverflowStore

from deep_agent.agent.attachment_extractors import AttachmentParseError, DocumentPage
from deep_agent.files.reading_budget import ReadingBudget, fitting_prefix_count, paginate_items
from deep_agent.agent.tool_errors import tool_error_message


async def read_stored_tool_result(
    store: OverflowStore,
    handle: str,
    *,
    offset: int = 0,
    limit: int | None = None,
    from_end: bool = False,
    pattern: str | None = None,
    char_offset: int = 0,
    end_offset: int | None = None,
    context_lines: int = 0,
    budget: ReadingBudget | None = None,
) -> dict[str, Any]:
    """Preserve line slicing/filtering, with a cursor for partial long lines.

    Offsets count selected lines from zero (matches plus merged context). Returned
    source_line_numbers map each content line to its original one-based position.
    A tail window stays in document order;
    its next_read finishes that window forwards, while previous_read visits older
    lines. end_offset bounds that continuation and is exclusive.
    """
    if offset < 0 or char_offset < 0 or (limit is not None and limit < 1):
        raise ModelRetry("offset/char_offset must be >= 0; limit must be >= 1.")
    if end_offset is not None and (from_end or end_offset < offset):
        raise ModelRetry("end_offset must be >= offset and requires from_end=false.")
    if isinstance(context_lines, bool) or not isinstance(context_lines, int) or not 0 <= context_lines <= 5:
        raise ModelRetry("context_lines must be an integer from 0 to 5.")
    budget = budget or ReadingBudget()
    try:
        data = await store.read(handle)
    except OSError as exc:
        return {"error": tool_error_message(
            "Stored tool result unavailable. Verify the exact authorized overflow handle.", exc,
        )}

    lines = data.decode("utf-8", errors="replace").splitlines()
    source_total_count = len(lines)
    if pattern is None:
        selected_indices = range(source_total_count)
    else:
        selected_indices = []
        # Matches arrive in source order. Extend only the unselected suffix of
        # each context window, merging overlapping and adjacent windows once.
        selected_end = 0
        for index, line in enumerate(lines):
            if pattern in line:
                window_start = max(selected_end, index - context_lines)
                window_end = min(source_total_count, index + context_lines + 1)
                selected_indices.extend(range(window_start, window_end))
                selected_end = window_end
    total = len(selected_indices)
    end = max(0, total - offset) if from_end else min(total, end_offset if end_offset is not None else total)
    def result(start: int, page_limit: int, page: DocumentPage) -> dict[str, Any]:
        next_read = None
        if page.next_offset is not None:
            next_read = {
                "handle": handle, "offset": page.next_offset - 1,
                "char_offset": page.next_char_offset, "limit": limit,
                "from_end": False, "pattern": pattern, "end_offset": end,
                "context_lines": context_lines,
            }
        previous_read = None
        if from_end and start > 0:
            previous_read = {
                "handle": handle, "offset": total - start, "limit": limit,
                "from_end": True, "pattern": pattern,
                "context_lines": context_lines,
            }
        return {
            "handle": handle, "content": page.content,
            "offset": start, "char_offset": char_offset, "limit": page_limit,
            "total_count": total, "returned_count": page.returned_count,
            "source_total_count": source_total_count,
            "source_line_numbers": [index + 1 for index in selected_indices[start : start + page.returned_count]],
            "has_more": page.has_more, "truncated_by": page.truncated_by,
            "next_read": next_read, "previous_read": previous_read,
        }

    if from_end and limit is None:
        # Grow a suffix in source order, measuring its full tail envelope. If
        # even the final item is oversized, select it and finish it forwards.
        checked: dict[int, bool] = {}

        def tail_fits(size: int) -> bool:
            start = end - size
            for index in selected_indices[start:end]:
                if len(lines[index]) > budget.tokens * 2:
                    if index not in checked:
                        checked[index] = budget.item_may_fit(lines[index])
                    if not checked[index]:
                        return False
            page = DocumentPage(
                content="\n".join(lines[index] for index in selected_indices[start:end]),
                returned_count=size, has_more=False, next_offset=None,
                next_char_offset=None, truncated_by=None,
            )
            return budget.fits(result(start, max(1, size), page))

        size = await asyncio.to_thread(fitting_prefix_count, end, tail_fits)
        start = max(0, end - max(1, size))
    else:
        start = max(0, end - limit) if from_end else offset
    page_limit = limit if limit is not None else max(1, end - start)
    try:
        page = await asyncio.to_thread(
            paginate_items,
            [lines[index] for index in selected_indices[start : min(end, start + page_limit)]],
            total_count=end, offset=start + 1, limit=page_limit,
            char_offset=char_offset, budget=budget,
            envelope=lambda page: result(start, page_limit, page),
        )
    except AttachmentParseError as exc:
        raise ModelRetry(str(exc)) from exc
    return result(start, page_limit, page)
