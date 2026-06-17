from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic_ai.messages import ModelMessage


@runtime_checkable
class SessionStore(Protocol):
    """Persistence boundary for conversation history.

    History is stored as Pydantic AI ``ModelMessage`` objects so it can be fed
    straight back into an agent run via ``message_history``. Swapping the
    in-memory implementation for Postgres/Redis means implementing this Protocol
    only; nothing else in the app changes.
    """

    async def get_history(self, session_id: str) -> list[ModelMessage]:
        """Return the full message history for a session (empty if new)."""
        ...

    async def append(
        self,
        session_id: str,
        messages: list[ModelMessage],
        *,
        agent_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        """Append the messages produced by a run to the session history."""
        ...

    async def list_sessions(self) -> list[str]:
        """Return all known session ids."""
        ...

    async def delete(self, session_id: str) -> None:
        """Delete a session and its history."""
        ...

