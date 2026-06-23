from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic_ai.messages import ModelMessage

from deep_agent.db.session_list import SessionListFilters, SessionSummaryRow


@runtime_checkable
class SessionStore(Protocol):
    """Persistence boundary for conversation history."""

    async def get_history(self, session_id: str) -> list[ModelMessage]:
        ...

    async def append(
        self,
        session_id: str,
        messages: list[ModelMessage],
        *,
        agent_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        ...

    async def list_sessions(self) -> list[str]:
        ...

    async def delete(self, session_id: str) -> None:
        ...

    async def list_session_summaries(
        self,
        filters: SessionListFilters,
    ) -> tuple[list[SessionSummaryRow], int]:
        ...
