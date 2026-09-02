from __future__ import annotations

from deep_agent.conversation.models import (
    ConversationHistoryResponse,
    PlanConversationItem,
    ToolConversationItem,
)


def decorate_history_display_names(
    history: ConversationHistoryResponse,
    mapping: dict[str, str],
) -> ConversationHistoryResponse:
    """Add current presentation names to a projection without mutating it."""

    decorated = history.model_copy(deep=True)
    for item in decorated.items:
        if not isinstance(item, (ToolConversationItem, PlanConversationItem)):
            continue
        tool_name = item.tool_name or "tool"
        item.display_name = mapping.get(tool_name) or tool_name
    return decorated
