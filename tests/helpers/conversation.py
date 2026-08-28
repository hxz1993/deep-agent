from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from pydantic_ai.messages import ModelMessage

from deep_agent.conversation.models import ConversationItem
from deep_agent.conversation.projector import project_session_events
from deep_agent.db.event_codec import encode_messages


def project_model_messages(
    messages: Sequence[ModelMessage],
    *,
    session_id: str = "test-session",
    run_id: str = "test-run",
    turn_id: str = "test-run",
    terminal_status: str = "completed",
) -> list[ConversationItem]:
    """Exercise the production Message codec and Conversation projector in tests."""

    now = datetime(2026, 8, 27, tzinfo=timezone.utc)
    drafts = encode_messages(messages, session_id=session_id, run_id=run_id)
    events: list[dict[str, Any]] = []
    for sequence, draft in enumerate(drafts, start=1):
        events.append(
            {
                "event_id": draft.event_id,
                "session_id": session_id,
                "sequence": sequence,
                "schema_version": draft.schema_version,
                "event_type": draft.event_type,
                "status": terminal_status if draft.event_type == "assistant/message" else None,
                "turn_id": turn_id,
                "run_id": run_id,
                "payload": draft.payload,
                "created_at": now,
                "updated_at": now,
            }
        )
    return project_session_events(events, session_id=session_id).items
