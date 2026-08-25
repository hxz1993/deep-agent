from __future__ import annotations

from typing import Any

from deep_agent.agent.history import model_messages_to_history
from deep_agent.db.event_codec import decode_message_events


def project_event_response(response: dict[str, Any]) -> dict[str, Any]:
    """Project the public Event API into user-visible history for assertions.

    This is deliberately test-only. Production consumers read semantic events
    directly and the removed v0.5 History API is not recreated.
    """
    items = list(response.get("items") or [])
    messages = decode_message_events(items)
    history = [item.model_dump(mode="json") for item in model_messages_to_history(messages)]
    return {
        "events": items,
        "messages": history,
        "model_message_count": len(messages),
    }
