from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from deep_agent.conversation.projector import (
    affected_conversation_items,
    project_session_events,
)


async def conversation_item_upserts(
    store: Any,
    session_id: str,
    changed_rows: Sequence[Any],
) -> list[dict[str, Any]]:
    """Build canonical persisted SSE items from the same projection as /history."""

    if not changed_rows:
        return []
    reader = getattr(store, "list_all_events", None)
    if not callable(reader):
        return []
    events = await reader(session_id)
    projection = project_session_events(events, session_id=session_id)
    changed_ids = {str(row.event_id) for row in changed_rows}
    return [
        {
            "type": "conversation_item_upsert",
            "persisted": True,
            "item": item.model_dump(mode="json"),
        }
        for item in affected_conversation_items(projection, changed_ids)
    ]
