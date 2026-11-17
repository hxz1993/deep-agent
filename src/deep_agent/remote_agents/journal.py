from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from deep_agent.db.models import ChatSession, ChatSessionEvent
from deep_agent.db.reference_integrity import lock_optional
from deep_agent.db.stores._session import SessionBoundMixin
from deep_agent.db.stores.event import SessionEventStore

from .contracts import RemoteAgentError, RemoteState

EVENT_TYPE = "remote_agent/invocation"


class Invocation(BaseModel):
    id: str
    connection_id: str
    connection_revision: int
    name: str
    protocol: str
    session_id: str | None = None
    agent_id: str | None = None
    user: str = Field(repr=False)
    app_id: str | None = None
    space_id: str | None = None
    turn_id: str | None = None
    run_id: str | None = None
    tool_call_id: str | None = None
    message_id: str | None = None
    state: RemoteState = Field(default_factory=RemoteState)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    title: str = ""
    timeline: list[dict[str, str]] = Field(default_factory=list)
    text: str = ""
    files: dict[str, dict] = Field(default_factory=dict)
    file_message_ids: dict[str, str] = Field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    sequence: int | None = None

    @model_validator(mode="before")
    @classmethod
    def restore_file_message_ids(cls, data):
        if isinstance(data, dict) and data.get("files") and "file_message_ids" not in data:
            message_id = data.get("message_id") or ""
            return {**data, "file_message_ids": dict.fromkeys(data["files"], message_id)}
        return data

    def result_files(self) -> list[dict]:
        return [
            file for key, file in self.files.items()
            if self.file_message_ids.get(key) == (self.message_id or "")
        ]

    def result_payload(self) -> dict:
        return {
            "task_id": self.id, "status": self.state.status, "result": self.text,
            "files": self.result_files(), "warnings": list(self.warnings),
        }

    def summary(self) -> dict:
        return {
            **self.model_dump(mode="json", include={
                "id", "connection_id", "name", "protocol", "session_id", "agent_id", "turn_id",
                "run_id", "created_at", "updated_at", "title", "timeline", "text", "warnings",
            }),
            "status": self.state.status,
            "question_id": self.state.question_id,
            "remote_task_id": self.state.task_id,
            "remote_context_id": self.state.context_id,
            "remote_session_id": self.state.session_id,
            "files": list(self.files.values()),
        }


class InvocationJournal(SessionBoundMixin):
    def __init__(self, session_factory):
        super().__init__(session_factory)
        self.events = SessionEventStore(session_factory)

    @staticmethod
    def _matches(session, user, agent_id, app_id, space_id):
        return session and (session.user_id, session.agent_id, session.app_id, session.space_id) == (
            user, agent_id, app_id, space_id,
        )

    @staticmethod
    def _query(session_id):
        return select(ChatSessionEvent).where(ChatSessionEvent.session_id == session_id,
                                               ChatSessionEvent.event_type == EVENT_TYPE).order_by(ChatSessionEvent.sequence.desc())

    async def list(self, session_id: str, *, user: str, agent_id: str | None,
                   app_id: str | None, space_id: str | None) -> list[Invocation]:
        async with self._read() as db:
            session = await db.get(ChatSession, session_id)
            if not self._matches(session, user, agent_id, app_id, space_id):
                return []
            entries = {}
            for row in await db.scalars(self._query(session_id)):
                key = row.payload["id"]
                if key not in entries:
                    entries[key] = Invocation.model_validate({**row.payload, "sequence": row.sequence})
            return list(entries.values())

    async def save(self, entry: Invocation) -> None:
        async with self._write() as db:
            session = await lock_optional(db, ChatSession, ChatSession.session_id, entry.session_id)
            if not self._matches(session, entry.user, entry.agent_id, entry.app_id, entry.space_id):
                raise RemoteAgentError("当前会话无法访问该远程调用")
            rows = await db.scalars(self._query(entry.session_id).with_for_update())
            previous = next((row.sequence for row in rows if row.payload.get("id") == entry.id), None)
            if previous != entry.sequence:
                raise RemoteAgentError("远程调用已更新，请重新读取状态")
            row = await self.events._append_lifecycle(
                db, session, event_type=EVENT_TYPE, status=entry.state.status,
                turn_id=entry.turn_id, run_id=entry.run_id,
                payload=entry.model_dump(mode="json", exclude={"sequence"}),
            )
            sequence = row.sequence
        entry.sequence = sequence
