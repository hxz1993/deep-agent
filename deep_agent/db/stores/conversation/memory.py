from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime

from pydantic_ai.messages import ModelMessage

from deep_agent.db.session_list import MAX_LIST_LIMIT, SessionListFilters, SessionSummaryRow
from deep_agent.db.session_key import parse_scoped_session_key


class InMemoryConversationStore:
    """Process-local session store for dev and single-process deployments."""

    def __init__(self) -> None:
        self._histories: dict[str, list[ModelMessage]] = defaultdict(list)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def lock(self, session_id: str) -> asyncio.Lock:
        return self._locks[session_id]

    async def get_history(self, session_id: str) -> list[ModelMessage]:
        return list(self._histories.get(session_id, []))

    async def append(
        self,
        session_id: str,
        messages: list[ModelMessage],
        *,
        agent_id: object | None = None,
        user_id: str | None = None,
    ) -> None:
        del agent_id, user_id
        if not messages:
            return
        self._histories[session_id].extend(messages)

    async def list_sessions(self) -> list[str]:
        return list(self._histories.keys())

    async def delete(self, session_id: str) -> None:
        self._histories.pop(session_id, None)
        self._locks.pop(session_id, None)

    async def list_session_summaries(
        self,
        filters: SessionListFilters,
    ) -> tuple[list[SessionSummaryRow], int]:
        from deep_agent.agent.history import first_user_prompt, session_activity_timestamps

        limit = min(max(filters.limit, 1), MAX_LIST_LIMIT)
        offset = max(filters.offset, 0)
        rows: list[SessionSummaryRow] = []

        for session_key in self._histories:
            history = self._histories[session_key]
            parsed_agent_id, _ = parse_scoped_session_key(session_key)
            created_at, updated_at = session_activity_timestamps(history) if history else (None, None)

            if filters.agent_id is not None and parsed_agent_id != filters.agent_id:
                continue
            if filters.user_id is not None:
                continue
            if filters.updated_after is not None and (
                updated_at is None or updated_at < filters.updated_after
            ):
                continue
            if filters.updated_before is not None and (
                updated_at is None or updated_at > filters.updated_before
            ):
                continue
            message_count = len(history)
            if filters.has_messages is True and message_count == 0:
                continue
            if filters.has_messages is False and message_count > 0:
                continue

            rows.append(
                SessionSummaryRow(
                    session_key=session_key,
                    agent_id=parsed_agent_id,
                    agent_name=None,
                    created_at=created_at or datetime.min,
                    updated_at=updated_at or datetime.min,
                    message_count=message_count,
                    user_prompt=first_user_prompt(history),
                )
            )

        reverse = True
        if filters.sort == "updated_at":
            rows.sort(key=lambda row: row.updated_at, reverse=reverse)
        else:
            rows.sort(key=lambda row: row.created_at, reverse=reverse)

        total = len(rows)
        return rows[offset : offset + limit], total
