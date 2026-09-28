from __future__ import annotations

from deep_agent.db.reference_integrity import acquire_write_barrier

import asyncio
import contextlib
import hashlib
import json
import sys
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Protocol, runtime_checkable

from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from sqlalchemy import and_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.agent.history import first_user_prompt
from deep_agent.db.event_codec import (
    MESSAGE_EVENT_TYPES,
    attachment_part_references,
    decode_message_events,
    group_message_events,
    event_contains_call,
    message_parts,
    tool_part_references,
)
from deep_agent.db.session_list import SessionListFilters, SessionSummaryRow
from deep_agent.db.session_scope import NO_AGENT_SCOPE, SessionOwner, normalize_agent_scope, runtime_session_key
from deep_agent.db.stores.chat_session.base import ChatSessionStoreBase
from deep_agent.db.stores.event.base import LifecycleStatus, SessionEventSnapshot, SessionEventStore, _compaction_anchor
from deep_agent.db.stores._session import serialized_sqlite_session
from deep_agent.db.models import ChatSession, ChatSessionEvent
from deep_agent.db.serialization import serialize_message

_MESSAGE_TERMINAL_STATUSES = {"completed", "pending", "cancelled", "failed", "interrupted"}

__all__ = ["SessionStore", "SqlConversationCoordinator", "SqlConversationStore"]


@runtime_checkable
class SessionStore(Protocol):
    async def get_event_head(self, session_id: str, *, agent_id: str | None = None) -> int: ...

    async def get_history(
        self, session_id: str, *, agent_id: str | None = None
    ) -> list[ModelMessage]: ...

    async def append(
        self, session_id: str, messages: list[ModelMessage], *,
        agent_id: str | None = None, user_id: str | None = None,
        app_id: str | None = None, space_id: str | None = None,
        turn_id: str | None = None, run_id: str | None = None,
        first_message_ordinal: int = 0, terminal_status: str | None = None,
        usage_model_id: str | None = None, usage_provider_id: str | None = None,
    ) -> list[SessionEventSnapshot]: ...

    async def append_message_parts(
        self, session_id: str, *, agent_id: str | None, user_id: str | None,
        turn_id: str, run_id: str, messages: list[ModelMessage],
        first_message_ordinal: int = 1, app_id: str | None = None,
        space_id: str | None = None, terminal_status: str | None = None,
        usage_model_id: str | None = None, usage_provider_id: str | None = None,
    ) -> list[SessionEventSnapshot]: ...

    async def append_execution_error(
        self, session_id: str, *, status: LifecycleStatus,
        agent_id: str | None, user_id: str | None, turn_id: str | None,
        run_id: str, payload: dict[str, Any], app_id: str | None = None,
        space_id: str | None = None,
    ) -> SessionEventSnapshot | None: ...

    async def list_sessions(self) -> list[tuple[str, str]]: ...
    async def resolve_ref(
        self, session_id: str, *, agent_id: str | None = None,
        user_id: str | None = None,
    ) -> tuple[str, str] | None: ...
    async def get_session_owner(
        self, session_id: str, *, agent_id: str | None = None,
    ) -> SessionOwner: ...
    async def delete(self, session_id: str, *, agent_id: str | None = None) -> None: ...
    async def get_session_title(
        self, session_id: str, *, agent_id: str | None = None,
    ) -> str | None: ...
    async def update_session_title(
        self, session_id: str, agent_id: str | None, title: str,
    ) -> None: ...
    async def ensure_title_fallback(
        self, session_id: str, *, agent_id: str | None, user_id: str | None,
        title: str, source_event_id: str, turn_id: str | None,
        run_id: str | None, details: dict[str, Any] | None = None,
    ) -> SessionEventSnapshot | None: ...
    async def apply_session_title(
        self, session_id: str, *, agent_id: str | None, user_id: str | None,
        title: str, source: str, source_event_id: str | None,
        turn_id: str | None, run_id: str | None,
        expected_event_id: str | None = None,
        details: dict[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
        usage_model_id: str | None = None,
    ) -> SessionEventSnapshot | None: ...
    async def list_session_summaries(
        self, filters: SessionListFilters,
    ) -> tuple[list[SessionSummaryRow], int]: ...
    async def list_events(
        self, session_id: str, **filters: Any,
    ) -> list[SessionEventSnapshot]: ...
    async def list_all_events(
        self, session_id: str,
    ) -> list[SessionEventSnapshot]: ...


class SqlConversationCoordinator:
    """Single persistence boundary for sessions and their semantic event log."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], *,
        chat_session: ChatSessionStoreBase, events: SessionEventStore,
    ) -> None:
        self._session_factory = session_factory
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._chat_sessions = chat_session
        self._events = events

    @property
    def events(self) -> SessionEventStore:
        return self._events

    def lock(self, session_id: str, *, agent_id: str | None = None) -> asyncio.Lock:
        return self._locks[runtime_session_key(session_id, agent_id=agent_id)]

    @asynccontextmanager
    async def _write(self):
        async with serialized_sqlite_session(self._session_factory):
            db = self._session_factory()
            await db.__aenter__()
            exc_info: tuple[type[BaseException] | None, BaseException | None, Any] = (
                None,
                None,
                None,
            )
            try:
                try:
                    if db.get_bind().dialect.name == "sqlite":
                        await db.execute(text("BEGIN IMMEDIATE"))
                    await acquire_write_barrier(db)
                    yield db
                    await asyncio.shield(db.commit())
                except BaseException:
                    exc_info = sys.exc_info()
                    with contextlib.suppress(Exception):
                        await asyncio.shield(db.rollback())
                    raise
            finally:
                with contextlib.suppress(Exception):
                    await asyncio.shield(db.__aexit__(*exc_info))

    @staticmethod
    def _required_identity(agent_id: str | None, user_id: str | None) -> tuple[str, str]:
        scope = normalize_agent_scope(agent_id)
        if scope == NO_AGENT_SCOPE:
            raise ValueError("New session events require an explicit agent_id")
        user = str(user_id or "").strip()
        if not user:
            raise ValueError("New session events require an explicit user_id")
        return scope, user

    async def get_history(
        self, session_id: str, *, agent_id: str | None = None,
    ) -> list[ModelMessage]:
        scope = normalize_agent_scope(agent_id)
        owner = await self._chat_sessions.get_owner(scope, session_id)
        if not owner.exists:
            return []
        return await self._events.get_history(session_id)

    async def get_raw_history(
        self, session_id: str, *, agent_id: str | None = None,
    ) -> list[ModelMessage]:
        scope = normalize_agent_scope(agent_id)
        owner = await self._chat_sessions.get_owner(scope, session_id)
        if not owner.exists:
            return []
        return await self._events.get_raw_history(session_id)

    async def append(
        self, session_id: str, messages: list[ModelMessage], *,
        agent_id: str | None = None, user_id: str | None = None,
        app_id: str | None = None, space_id: str | None = None,
        turn_id: str | None = None, run_id: str | None = None,
        first_message_ordinal: int = 0, terminal_status: str | None = None,
        usage_model_id: str | None = None, usage_provider_id: str | None = None,
    ) -> list[SessionEventSnapshot]:
        if not messages and terminal_status is None:
            return []
        if terminal_status is not None and terminal_status not in _MESSAGE_TERMINAL_STATUSES:
            raise ValueError("Invalid Run terminal status")
        scope, user = self._required_identity(agent_id, user_id)
        async with self._write() as db:
            existing_owner = await self._chat_sessions._get_owner(
                db, scope, session_id, for_update=True
            )
            if existing_owner.exists and existing_owner.user_id != user:
                raise PermissionError("Session belongs to another user")
            creates_turn = any(
                isinstance(message, ModelRequest)
                and any(isinstance(part, UserPromptPart) for part in message.parts)
                for message in messages
            )
            resolved_turn_id = turn_id or (run_id if creates_turn else None)
            if resolved_turn_id is None and not creates_turn:
                resolved_turn_id = await self._events._latest_turn_id(db, session_id)
            if resolved_turn_id is None:
                raise ValueError(
                    "Message Events require turn_id, or run_id for a new user turn"
                )
            now = datetime.now(timezone.utc)
            title = first_user_prompt(messages) if not existing_owner.exists else None
            session = await self._chat_sessions._ensure(
                db, session_id, agent_id=scope, user_id=user,
                app_id=app_id, space_id=space_id, title=title, now=now,
            )
            part_mode = any(
                "deepagent_message_ordinal" in (getattr(message, "metadata", None) or {})
                for message in messages
            )
            if run_id and not part_mode:
                persisted = list((await db.execute(select(ChatSessionEvent).where(
                    ChatSessionEvent.session_id == session_id,
                    ChatSessionEvent.run_id == run_id,
                    ChatSessionEvent.event_type.in_(MESSAGE_EVENT_TYPES),
                ))).scalars())
                part_mode = any(isinstance(row.payload.get("source"), dict) for row in persisted)
            if part_mode and run_id:
                rows = await self._events._append_message_parts(
                    db, session, messages, turn_id=resolved_turn_id, run_id=run_id,
                    first_message_ordinal=first_message_ordinal, terminal_status=terminal_status,
                    usage_model_id=usage_model_id, usage_provider_id=usage_provider_id,
                )
            else:
                rows = await self._events._append_messages(
                    db, session, messages, turn_id=resolved_turn_id, run_id=run_id, now=now,
                    first_message_ordinal=first_message_ordinal,
                    terminal_status=terminal_status,
                    usage_model_id=usage_model_id, usage_provider_id=usage_provider_id,
                )
            return [SessionEventSnapshot.from_row(row) for row in rows]

    async def append_message_parts(
        self, session_id: str, *, agent_id: str | None, user_id: str | None,
        turn_id: str, run_id: str, messages: list[ModelMessage],
        first_message_ordinal: int = 1, app_id: str | None = None,
        space_id: str | None = None, terminal_status: str | None = None,
        usage_model_id: str | None = None, usage_provider_id: str | None = None,
    ) -> list[SessionEventSnapshot]:
        if not run_id or not turn_id:
            raise ValueError("Message parts require run_id and turn_id")
        if terminal_status is not None and terminal_status not in _MESSAGE_TERMINAL_STATUSES:
            raise ValueError("Invalid message terminal status")
        scope, user = self._required_identity(agent_id, user_id)
        async with self._write() as db:
            owner = await self._chat_sessions._get_owner(db, scope, session_id, for_update=True)
            if owner.exists and owner.user_id != user:
                raise PermissionError("Session belongs to another user")
            session = await self._chat_sessions._ensure(
                db, session_id, agent_id=scope, user_id=user,
                app_id=app_id, space_id=space_id,
                title=first_user_prompt(messages) if not owner.exists else None,
            )
            rows = await self._events._append_message_parts(
                db, session, messages, turn_id=turn_id, run_id=run_id,
                first_message_ordinal=first_message_ordinal, terminal_status=terminal_status,
                usage_model_id=usage_model_id, usage_provider_id=usage_provider_id,
            )
            return [SessionEventSnapshot.from_row(row) for row in rows]

    async def append_lifecycle(
        self, session_id: str, *, event_type: str, status: LifecycleStatus,
        agent_id: str | None, user_id: str | None, turn_id: str | None,
        run_id: str | None, payload: dict[str, Any],
        app_id: str | None = None, space_id: str | None = None,
    ) -> SessionEventSnapshot:
        if event_type != "compaction":
            raise ValueError("Only compaction is a mutable lifecycle event")
        scope, user = self._required_identity(agent_id, user_id)
        async with self._write() as db:
            session = await self._chat_sessions._ensure(
                db, session_id, agent_id=scope, user_id=user,
                app_id=app_id, space_id=space_id,
            )
            row = await self._events._append_lifecycle(
                db, session, event_type=event_type, status=status,
                turn_id=turn_id, run_id=run_id, payload=payload,
            )
            return SessionEventSnapshot.from_row(row)

    async def append_execution_error(
        self, session_id: str, *, status: LifecycleStatus,
        agent_id: str | None, user_id: str | None, turn_id: str | None,
        run_id: str, payload: dict[str, Any], app_id: str | None = None,
        space_id: str | None = None,
    ) -> SessionEventSnapshot | None:
        if status not in {"failed", "interrupted"}:
            raise ValueError("Run terminal status must be exceptional")
        scope, user = self._required_identity(agent_id, user_id)
        async with self._write() as db:
            existing_owner = await self._chat_sessions._get_owner(
                db, scope, session_id, for_update=True
            )
            if existing_owner.exists and existing_owner.user_id != user:
                raise PermissionError("Session belongs to another user")
            session = await self._chat_sessions._ensure(
                db, session_id, agent_id=scope, user_id=user,
                app_id=app_id, space_id=space_id,
            )
            row = await self._events._append_execution_error(
                db, session, status=status, turn_id=turn_id, run_id=run_id,
                payload=payload,
            )
            return SessionEventSnapshot.from_row(row) if row is not None else None

    async def update_lifecycle(
        self, event_id: str, *, status: LifecycleStatus, payload: dict[str, Any],
    ) -> SessionEventSnapshot | None:
        return await self._events.update_lifecycle(event_id, status=status, payload=payload)

    async def attach_usage(self, event_id: str, *, calls: list[dict[str, Any]],
                           usage_model_id: str | None = None) -> SessionEventSnapshot | None:
        return await self._events.attach_usage(event_id, calls=calls, usage_model_id=usage_model_id)

    async def persist_compaction_projection(
        self, session_id: str, *, agent_id: str | None, user_id: str | None,
        raw_history: list[ModelMessage], projected_history: list[ModelMessage],
        summary_markdown: str, trigger: str, model_id: str | None,
        estimated_before: int, estimated_after: int, budget_limit_tokens: int,
        context_window_tokens: int | None, preserved_tail_tokens: int,
        duration_ms: int,
        compaction_event_id: str | None = None,
        pruned_tool_results: list[dict[str, Any]] | None = None,
        summary_call: dict[str, Any] | None = None,
        validate_projection: Callable[[list[ModelMessage]], int | tuple[int, int]] | None = None,
        estimated_tokens_before: int | None = None,
        estimated_tokens_after: int | None = None,
        estimate_scope: str | None = None,
    ) -> SessionEventSnapshot | None:
        scope, user = self._required_identity(agent_id, user_id)
        # The projection must be one summary anchor followed by the entire
        # preserved suffix. A partial match must never move the cutoff forward.
        anchor = projected_history[0] if projected_history else None
        if (not isinstance(anchor, ModelRequest) or len(anchor.parts) != 1
                or not isinstance(anchor.parts[0], UserPromptPart)
                or not isinstance(anchor.parts[0].content, str)
                or not anchor.parts[0].content.startswith(
                    "Summary of previous conversation:\n\n" + summary_markdown.strip())):
            raise ValueError("compaction_anchor_mismatch")
        suffix = _common_message_suffix(raw_history, projected_history)
        if suffix != len(projected_history) - 1:
            raise ValueError("compaction_tail_mismatch")
        from deep_agent.agent.context.handoff import extract_anchor_protected_context, extract_protected_context
        protected_context = extract_protected_context(raw_history, cutoff=len(raw_history) - suffix, raw_history=raw_history)
        supplied_context = extract_anchor_protected_context(projected_history)
        if supplied_context != protected_context:
            raise ValueError("compaction_protected_context_mismatch")
        covered_messages = len(raw_history) - suffix
        if covered_messages <= 0 or not summary_markdown.strip():
            return None
        async with self._write() as db:
            session = await self._chat_sessions._ensure(
                db, session_id, agent_id=scope, user_id=user
            )
            message_rows = list(
                (
                    await db.execute(
                        select(ChatSessionEvent)
                        .where(
                            ChatSessionEvent.session_id == session_id,
                            ChatSessionEvent.event_type.in_(MESSAGE_EVENT_TYPES),
                        )
                        .order_by(ChatSessionEvent.sequence)
                    )
                ).scalars()
            )
            current_raw = decode_message_events(message_rows)
            if [serialize_message(item) for item in current_raw] != [
                serialize_message(item) for item in raw_history
            ]:
                if compaction_event_id is not None:
                    stale = (
                        await db.execute(
                            select(ChatSessionEvent).where(
                                ChatSessionEvent.event_id == compaction_event_id,
                                ChatSessionEvent.session_id == session_id,
                                ChatSessionEvent.event_type == "compaction",
                                ChatSessionEvent.status == "running",
                            ).with_for_update()
                        )
                    ).scalar_one_or_none()
                    if stale is not None:
                        stale.status = "stale"
                        stale.payload = {
                            **dict(stale.payload),
                            "error": {"code": "source_history_changed"},
                            "duration_ms": duration_ms,
                        }
                        stale.updated_at = datetime.now(timezone.utc)
                return None
            grouped_rows = group_message_events(message_rows)
            if len(grouped_rows) != len(raw_history) or covered_messages > len(grouped_rows):
                return None
            def group_end(row):
                return int(row.get("through_sequence", row["sequence"])) if isinstance(row, dict) else int(row.sequence)
            through_sequence = max(group_end(row) for row in grouped_rows[:covered_messages])
            # A compaction cutoff may not bisect an interleaved SDK message.
            if any((int(row["sequence"]) if isinstance(row, dict) else int(row.sequence)) <= through_sequence
                   for row in grouped_rows[covered_messages:]):
                return None
            covered_rows = [row for row in message_rows if row.sequence <= through_sequence]
            previous = (
                await db.execute(
                    select(ChatSessionEvent)
                    .where(
                        ChatSessionEvent.session_id == session_id,
                        ChatSessionEvent.event_type == "compaction",
                        ChatSessionEvent.status == "completed",
                    )
                    .order_by(ChatSessionEvent.sequence.desc()).limit(1)
                )
            ).scalar_one_or_none()
            previous_through = int(
                ((previous.payload.get("source") or {}).get("through_sequence") or 0)
                if previous is not None else 0
            )
            if through_sequence <= previous_through:
                return None
            tool_evidence = _bounded_evidence(_tool_evidence(covered_rows))
            context_evidence = _bounded_evidence(_context_evidence(covered_rows))
            attachment_evidence = _bounded_evidence(
                _attachment_evidence(covered_rows)
            )
            result_locations = {
                ref["call_id"]: {
                    "source_event_id": row.event_id,
                    "part_offset": ref["part_offset"],
                }
                for row in covered_rows
                for ref in tool_part_references(row)
                if ref["role"] == "result"
            }
            pruned_records = []
            for record in pruned_tool_results or []:
                item = dict(record)
                item.update(result_locations.get(item.get("call_id"), {}))
                pruned_records.append(item)
            summary_call_payload = dict(summary_call or {})
            resolved_model_name = summary_call_payload.get("model_id")
            if model_id:
                summary_call_payload["model_id"] = model_id
                if resolved_model_name and resolved_model_name != model_id:
                    summary_call_payload["resolved_model_name"] = resolved_model_name
            summary_call_payload.setdefault("provider_id", None)
            summary_call_payload.setdefault("provider_response_id", None)
            summary_call_payload.setdefault("finish_reason", None)
            summary_call_payload.setdefault("usage", None)
            summary_call_payload.setdefault("max_output_tokens", 4096)
            rendered = {
                "summary_markdown": summary_markdown.strip(),
                "protected_context": protected_context,
                "tool_evidence": tool_evidence,
                "context_evidence": context_evidence,
                "attachment_evidence": attachment_evidence,
            }
            if validate_projection is not None:
                # Persisted evidence carries Event locations and can differ from
                # the runtime index. Validate the exact anchor readers will use
                # before changing the successful checkpoint in this transaction.
                tail = list(raw_history[-suffix:]) if suffix else []
                validated = validate_projection([
                    _compaction_anchor({"replacement": rendered}), *tail,
                ])
                if isinstance(validated, tuple):
                    estimated_after, estimated_tokens_after = validated
                else:
                    estimated_after = validated
            lifecycle_row = None
            if compaction_event_id is not None:
                lifecycle_row = (
                    await db.execute(
                        select(ChatSessionEvent).where(
                            ChatSessionEvent.event_id == compaction_event_id,
                            ChatSessionEvent.session_id == session_id,
                            ChatSessionEvent.event_type == "compaction",
                            ChatSessionEvent.status == "running",
                        ).with_for_update()
                    )
                ).scalar_one_or_none()
                if lifecycle_row is None:
                    return None
            if lifecycle_row is None:
                lifecycle_row = await self._events._append_lifecycle(
                    db, session, event_type="compaction", status="running",
                    turn_id=await self._events._latest_turn_id(db, session_id),
                    run_id=None, payload={"trigger": trigger},
                )
            payload = {
                "trigger": trigger,
                "previous_compaction_event_id": previous.event_id if previous else None,
                "source": {
                    "from_sequence": previous_through + 1,
                    "through_sequence": through_sequence,
                    "head_sequence": int(lifecycle_row.sequence) - 1,
                    "history_hash": _event_rows_hash(covered_rows),
                },
                "replacement": {
                    "format": "handoff-checkpoint-v2",
                    **rendered,
                    "rendered_hash": _value_hash(rendered),
                },
                "budget": {
                    "context_window_tokens": context_window_tokens,
                    "estimated_before": estimated_before,
                    "budget_limit_tokens": budget_limit_tokens,
                    "estimated_after": estimated_after,
                    **({"estimated_tokens_before": estimated_tokens_before,
                        "estimated_tokens_after": estimated_tokens_after,
                        "estimate_scope": estimate_scope}
                       if estimated_tokens_before is not None and estimated_tokens_after is not None else {}),
                    "preserved_tail_tokens": preserved_tail_tokens,
                },
                "pruned_tool_results": pruned_records,
                "summary_call": summary_call_payload,
                "duration_ms": duration_ms,
                "error": None,
            }
            lifecycle_row.payload = payload
            lifecycle_row.status = "completed"
            lifecycle_row.updated_at = datetime.now(timezone.utc)
            await db.flush()
            return SessionEventSnapshot.from_row(lifecycle_row)

    async def list_events(self, session_id: str, **filters: Any) -> list[SessionEventSnapshot]:
        return await self._events.list_events(session_id, **filters)

    async def list_all_events(self, session_id: str) -> list[SessionEventSnapshot]:
        return await self._events.list_all_events(session_id)

    async def latest_turn_id(self, session_id: str) -> str | None:
        async with self._session_factory() as db:
            return await self._events._latest_turn_id(db, session_id)

    async def find_call_origin(
        self, session_id: str, call_ids: list[str],
    ) -> tuple[str, str] | None:
        return await self._events.find_call_origin(session_id, call_ids)

    async def latest_plan_snapshot(
        self, session_id: str, turn_id: str,
    ) -> dict[str, Any] | None:
        return await self._events.latest_plan_snapshot(session_id, turn_id)

    async def latest_completed_compaction(
        self, session_id: str,
    ) -> SessionEventSnapshot | None:
        return await self._events.latest_completed_compaction(session_id)

    async def search_event_candidates(
        self, *, agent_id: str, user_id: str, session_id: str | None,
        event_types: list[str] | None = None, tool_name: str | None = None,
        call_id: str | None = None, created_after: datetime | None = None,
        created_before: datetime | None = None, limit: int = 200,
        offset: int = 0, from_sequence: int | None = None,
        through_sequence: int | None = None,
    ) -> dict[str, Any]:
        scope, user = self._required_identity(agent_id, user_id)
        if (from_sequence is not None or through_sequence is not None) and session_id is None:
            raise ValueError("Sequence ranges require one session")
        stmt = (
            select(
                ChatSessionEvent.event_id,
                ChatSessionEvent.session_id,
                ChatSessionEvent.sequence,
                ChatSessionEvent.turn_id,
                ChatSessionEvent.run_id,
                ChatSessionEvent.event_type,
                ChatSessionEvent.search_text,
                ChatSessionEvent.payload,
                ChatSessionEvent.created_at,
                ChatSession.title,
            )
            .join(ChatSession, ChatSession.session_id == ChatSessionEvent.session_id)
            .where(
                ChatSession.agent_id == scope,
                ChatSession.user_id == user,
                ChatSessionEvent.search_text.is_not(None),
            )
        )
        if session_id is not None:
            stmt = stmt.where(ChatSessionEvent.session_id == session_id)
        if event_types:
            stmt = stmt.where(ChatSessionEvent.event_type.in_(event_types))
        if created_after:
            stmt = stmt.where(ChatSessionEvent.created_at >= created_after)
        if created_before:
            stmt = stmt.where(ChatSessionEvent.created_at <= created_before)
        if from_sequence is not None:
            stmt = stmt.where(ChatSessionEvent.sequence >= from_sequence)
        if through_sequence is not None:
            stmt = stmt.where(ChatSessionEvent.sequence <= through_sequence)
        page_size = min(max(limit, 1), 2_000)
        stmt = stmt.order_by(
            ChatSessionEvent.created_at.desc(), ChatSessionEvent.session_id,
            ChatSessionEvent.sequence.desc(),
        ).offset(max(0, offset)).limit(page_size + 1)
        async with self._session_factory() as db:
            source_head = None
            if session_id is not None:
                next_sequence = (await db.execute(select(ChatSession.next_sequence).where(
                    ChatSession.session_id == session_id,
                    ChatSession.agent_id == scope,
                    ChatSession.user_id == user,
                ))).scalar_one_or_none()
                source_head = max(0, int(next_sequence or 1) - 1)
                if through_sequence is not None:
                    source_head = min(source_head, through_sequence)
                stmt = stmt.where(ChatSessionEvent.sequence <= source_head)
            rows = (await db.execute(stmt)).all()
        has_more = len(rows) > page_size
        rows = rows[:page_size]
        candidates: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row._mapping)
            refs = tool_part_references({"payload": item.get("payload")})
            attachment_refs = attachment_part_references(
                {"payload": item.get("payload")}
            )
            if tool_name and not any(ref["tool_name"] == tool_name for ref in refs):
                continue
            if call_id and not any(ref["call_id"] == call_id for ref in refs):
                continue
            matches = [
                {
                    "part_offset": ref["part_offset"],
                    "role": ref["role"],
                    "call_id": ref["call_id"],
                    "tool_name": ref["tool_name"],
                }
                for ref in refs
                if (tool_name is None or ref["tool_name"] == tool_name)
                and (call_id is None or ref["call_id"] == call_id)
            ]
            if (tool_name or call_id) and not matches:
                continue
            item["tool_matches"] = matches
            item["attachment_matches"] = [
                {
                    key: ref[key]
                    for key in (
                        "part_offset",
                        "item_offset",
                        "file_id",
                        "name",
                        "mime",
                    )
                }
                for ref in attachment_refs
            ]
            item["call_id"] = matches[0]["call_id"] if len(matches) == 1 else None
            item["tool_name"] = matches[0]["tool_name"] if len(matches) == 1 else None
            item.pop("payload", None)
            candidates.append(item)
        return {
            "items": candidates, "scanned_count": len(rows),
            "next_offset": offset + len(rows) if has_more else None,
            "source_head": source_head,
        }

    async def read_authorized_events(
        self, *, agent_id: str, user_id: str, event_id: str | None = None,
        session_id: str | None = None, call_id: str | None = None,
        before: int = 0, after: int = 0,
        from_sequence: int | None = None, through_sequence: int | None = None,
        page_size: int | None = None,
    ) -> list[SessionEventSnapshot] | dict[str, Any]:
        scope, user = self._required_identity(agent_id, user_id)
        async with self._session_factory() as db:
            authorized = and_(
                ChatSession.agent_id == scope,
                ChatSession.user_id == user,
                ChatSession.session_id == ChatSessionEvent.session_id,
            )
            if event_id is not None:
                target = (
                    await db.execute(
                        select(ChatSessionEvent)
                        .join(ChatSession, authorized)
                        .where(ChatSessionEvent.event_id == event_id)
                    )
                ).scalar_one_or_none()
                if target is None:
                    return {"items": [], "next_sequence": None} if page_size is not None else []
                low = max(1, int(target.sequence) - min(max(before, 0), 20))
                high = int(target.sequence) + min(max(after, 0), 20)
                stmt = select(ChatSessionEvent).join(ChatSession, authorized).where(
                    ChatSessionEvent.session_id == target.session_id,
                    ChatSessionEvent.sequence.between(low, high),
                )
            elif session_id is not None and call_id is not None:
                stmt = select(ChatSessionEvent).join(ChatSession, authorized).where(
                    ChatSessionEvent.session_id == session_id,
                    ChatSessionEvent.event_type.in_(("assistant/message", "tool/results")),
                )
            else:
                raise ValueError("event_id or session_id + call_id is required")
            if from_sequence is not None:
                stmt = stmt.where(ChatSessionEvent.sequence >= from_sequence)
            if through_sequence is not None:
                stmt = stmt.where(ChatSessionEvent.sequence <= through_sequence)
            stmt = stmt.order_by(ChatSessionEvent.sequence)
            bounded_size = min(max(page_size, 1), 100) if page_size is not None else None
            if bounded_size is not None:
                stmt = stmt.limit(bounded_size + 1)
            rows = list((await db.execute(stmt)).scalars())
            next_sequence = None
            if bounded_size is not None and len(rows) > bounded_size:
                rows = rows[:bounded_size]
                next_sequence = int(rows[-1].sequence) + 1
            if call_id is not None:
                rows = [row for row in rows if event_contains_call(row, call_id)]
        items = [SessionEventSnapshot.from_row(row) for row in rows]
        return {"items": items, "next_sequence": next_sequence} if page_size is not None else items

    async def list_sessions(self) -> list[tuple[str, str]]:
        return await self._chat_sessions.list_sessions()

    async def resolve_ref(
        self, session_id: str, *, agent_id: str | None = None,
        user_id: str | None = None,
    ) -> tuple[str, str] | None:
        return await self._chat_sessions.resolve_ref(
            session_id, agent_id=agent_id, user_id=user_id
        )

    async def get_session_owner(
        self, session_id: str, *, agent_id: str | None = None,
    ) -> SessionOwner:
        return await self._chat_sessions.get_owner(normalize_agent_scope(agent_id), session_id)

    async def get_event_head(self, session_id: str, *, agent_id: str | None = None) -> int:
        """Read the existing sequence counter without loading message payloads."""
        async with self._session_factory() as db:
            next_sequence = (await db.execute(select(ChatSession.next_sequence).where(
                ChatSession.session_id == session_id,
                ChatSession.agent_id == normalize_agent_scope(agent_id),
            ))).scalar_one_or_none()
        return max(0, int(next_sequence or 1) - 1)

    async def get_session_timestamps(
        self, session_id: str, *, agent_id: str | None = None,
    ) -> tuple[datetime | None, datetime | None]:
        return await self._chat_sessions.get_timestamps(
            normalize_agent_scope(agent_id), session_id
        )

    async def delete(self, session_id: str, *, agent_id: str | None = None) -> None:
        scope = normalize_agent_scope(agent_id)
        async with self._write() as db:
            await self._chat_sessions._delete(db, scope, session_id)
        # Keep the lock identity: waiters may already hold a reference to it.

    async def get_session_title(
        self, session_id: str, *, agent_id: str | None = None,
    ) -> str | None:
        scope = normalize_agent_scope(agent_id)
        async with self._session_factory() as db:
            return await self._chat_sessions.get_session_title(db, scope, session_id)

    async def update_session_title(
        self, session_id: str, agent_id: str | None, title: str,
    ) -> None:
        scope = normalize_agent_scope(agent_id)
        async with self._write() as db:
            await self._chat_sessions.update_session_title(db, scope, session_id, title)

    async def update_session_title_if_current(
        self, session_id: str, agent_id: str | None, *,
        expected_title: str | None, title: str,
    ) -> bool:
        scope = normalize_agent_scope(agent_id)
        async with self._write() as db:
            return await self._chat_sessions.update_session_title_if_current(
                db, scope, session_id, expected_title=expected_title, title=title
            )

    async def ensure_title_fallback(
        self,
        session_id: str,
        *,
        agent_id: str | None,
        user_id: str | None,
        title: str,
        source_event_id: str,
        turn_id: str | None,
        run_id: str | None,
        details: dict[str, Any] | None = None,
    ) -> SessionEventSnapshot | None:
        scope, user = self._required_identity(agent_id, user_id)
        normalized = title.strip()[:80]
        if not normalized:
            raise ValueError("Session title must not be blank")
        async with self._write() as db:
            owner = await self._chat_sessions._get_owner(
                db, scope, session_id, for_update=True
            )
            if not owner.exists or owner.user_id != user:
                raise PermissionError("Session belongs to another Agent or user")
            if await self._events._latest_title(db, session_id) is not None:
                return None
            source_row = await db.get(ChatSessionEvent, source_event_id)
            if (
                source_row is None
                or source_row.session_id != session_id
                or source_row.event_type != "user/message"
            ):
                raise ValueError("Title source must be the first persisted user message")
            session = await db.get(ChatSession, session_id)
            assert session is not None
            row = await self._events._append_title(
                db,
                session,
                title=normalized,
                source="fallback",
                source_event_id=source_event_id,
                turn_id=turn_id,
                run_id=run_id,
                details=details,
            )
            return SessionEventSnapshot.from_row(row)

    async def apply_session_title(
        self,
        session_id: str,
        *,
        agent_id: str | None,
        user_id: str | None,
        title: str,
        source: str,
        source_event_id: str | None,
        turn_id: str | None,
        run_id: str | None,
        expected_event_id: str | None = None,
        details: dict[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
        usage_model_id: str | None = None,
    ) -> SessionEventSnapshot | None:
        if source not in {"model", "user"}:
            raise ValueError("Session title source must be model or user")
        scope, user = self._required_identity(agent_id, user_id)
        normalized = title.strip()[:80]
        if not normalized:
            raise ValueError("Session title must not be blank")
        async with self._write() as db:
            owner = await self._chat_sessions._get_owner(
                db, scope, session_id, for_update=True
            )
            if not owner.exists or owner.user_id != user:
                raise PermissionError("Session belongs to another Agent or user")
            latest = await self._events._latest_title(db, session_id)
            if source == "model" and (
                latest is None
                or latest.event_id != expected_event_id
                or latest.payload.get("source") != "fallback"
            ):
                return None
            session = await db.get(ChatSession, session_id)
            assert session is not None
            row = await self._events._append_title(
                db,
                session,
                title=normalized,
                source=source,
                source_event_id=source_event_id,
                turn_id=turn_id,
                run_id=run_id,
                details=details,
                usage=usage, usage_model_id=usage_model_id,
            )
            return SessionEventSnapshot.from_row(row)

    async def list_session_summaries(
        self, filters: SessionListFilters,
    ) -> tuple[list[SessionSummaryRow], int]:
        return await self._chat_sessions.list_summaries(
            agent_id=filters.agent_id, user_id=filters.user_id,
            limit=filters.limit, offset=filters.offset,
            updated_after=filters.updated_after, updated_before=filters.updated_before,
            has_messages=filters.has_messages, sort=filters.sort,
            query=filters.query, source_type=filters.source_type, source_id=filters.source_id,
        )

    async def interrupt_running(self) -> int:
        return await self._events.interrupt_running()


SqlConversationStore = SqlConversationCoordinator


def _common_message_suffix(raw: list[ModelMessage], projected: list[ModelMessage]) -> int:
    raw_payloads = [serialize_message(message) for message in raw]
    projected_payloads = [serialize_message(message) for message in projected]
    count = 0
    while count < min(len(raw_payloads), len(projected_payloads)):
        original = raw_payloads[-1 - count]
        visible = projected_payloads[-1 - count]
        if original.get("kind") == visible.get("kind") == "request":
            # The SDK adds request-envelope values after tool results were
            # persisted. These do not change the original message parts.
            original, visible = dict(original), dict(visible)
            for key in ("conversation_id", "run_id", "timestamp"):
                original.pop(key, None)
                visible.pop(key, None)
            # after_tool_execute and the SDK stamp the same result separately.
            # Ignore only the envelope timestamp of typed tool-return parts;
            # timestamps inside business content/metadata remain significant.
            for payload in (original, visible):
                payload["parts"] = [
                    {key: value for key, value in part.items() if key != "timestamp"}
                    if part.get("part_kind") == "tool-return" else part
                    for part in payload.get("parts", [])
                ]
            if original.get("instructions") is None:
                original.pop("instructions", None)
                visible.pop("instructions", None)
        if original != visible:
            break
        count += 1
    return count


def _event_rows_hash(rows: list[ChatSessionEvent]) -> str:
    return _value_hash(
        [
            {
                "sequence": int(row.sequence), "event_type": row.event_type,
                "payload": row.payload,
            }
            for row in rows
        ]
    )


def _value_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _tool_evidence(rows: list[ChatSessionEvent]) -> list[dict[str, Any]]:
    from deep_agent.agent.context.handoff import safe_tool_args_preview, project_tool_evidence
    calls: dict[str, tuple[ChatSessionEvent, dict[str, Any]]] = {}
    results: dict[str, tuple[ChatSessionEvent, dict[str, Any]]] = {}
    for row in rows:
        for ref in tool_part_references(row):
            target = calls if ref["role"] == "call" else results
            target[ref["call_id"]] = (row, ref)
    evidence: list[dict[str, Any]] = []
    for call_id in sorted(
        set(calls) | set(results),
        key=lambda item: (results.get(item) or calls[item])[0].sequence,
    ):
        call_entry = calls.get(call_id)
        result_entry = results.get(call_id)
        call = call_entry[0] if call_entry else None
        result = result_entry[0] if result_entry else None
        call_ref = call_entry[1] if call_entry else None
        result_ref = result_entry[1] if result_entry else None
        call_part = call_ref["part"] if call_ref else {}
        result_part = result_ref["part"] if result_ref else {}
        content = result_part.get("content")
        raw_metadata = result_part.get("metadata")
        metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
        factual_content = metadata.get("inline_fallback", content)
        artifact_id = _find_artifact_id(metadata) or _find_artifact_id(result_part)
        outcome = ("failed" if result_part.get("part_kind") == "retry-prompt" or metadata.get("error")
                   else (result_part.get("outcome") or "success") if result else "pending")
        item = project_tool_evidence(
            {
                "_sequence": int((result or call).sequence),
                "_priority": _tool_evidence_priority(
                    tool_name=(call_ref or result_ref or {}).get("tool_name"),
                    result_present=result is not None,
                    outcome=outcome,
                    metadata=metadata,
                ),
                "call_id": call_id,
                "tool_name": (call_ref or result_ref or {}).get("tool_name"),
                "call_event_id": call.event_id if call else None,
                "call_part_offset": call_ref.get("part_offset") if call_ref else None,
                "result_event_id": result.event_id if result else None,
                "result_part_offset": result_ref.get("part_offset") if result_ref else None,
                "args_preview": safe_tool_args_preview(call_part.get("args"), 384) if call else None,
                "result_complete": bool(result)
                and bool(
                    metadata.get("inline_fallback") is not None
                    or artifact_id
                    or not metadata.get("truncated")
                ),
                "artifact_id": artifact_id,
                "outcome": outcome,
                "diagnostic": safe_tool_args_preview(factual_content, 256) if outcome in {"failed", "error", "denied", "rejected"} else None,
            }
        )
        if item is not None:
            evidence.append(item)
    return evidence


def _context_evidence(rows: list[ChatSessionEvent]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for row in rows:
        if row.event_type != "user/message":
            continue
        for part_offset, part in enumerate(message_parts(row)):
            if part.get("part_kind") != "user-prompt":
                continue
            content = part.get("content")
            if not isinstance(content, list):
                continue
            for item in content:
                if not isinstance(item, dict) or item.get("kind") != "context-ref":
                    continue
                payload = item.get("payload") or {}
                entry = {
                    "_sequence": int(row.sequence),
                    "_priority": 1,
                    "source_event_id": row.event_id,
                    "part_offset": part_offset,
                    "key": item.get("key"), "type": item.get("type"),
                    "schema_version": item.get("schema_version"),
                    "label": item.get("label"),
                    "payload_hash": _value_hash(payload),
                    "stable_facts": _stable_facts(payload),
                }
                encoded = json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                )
                if len(encoded) <= 8192:
                    entry["payload"] = payload
                evidence.append(entry)
    return evidence


def _attachment_evidence(rows: list[ChatSessionEvent]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for row in rows:
        if row.event_type != "user/message":
            continue
        for ref in attachment_part_references(row):
            evidence.append(
                {
                    "_sequence": int(row.sequence),
                    "_priority": 1,
                    "source_event_id": row.event_id,
                    "part_offset": ref["part_offset"],
                    "item_offset": ref["item_offset"],
                    "file_id": ref["file_id"],
                    "name": ref["name"],
                    "mime": ref["mime"],
                    "reference_hash": _value_hash(ref["item"]),
                }
            )
    return evidence


def _tool_evidence_priority(
    *,
    tool_name: str | None,
    result_present: bool,
    outcome: Any,
    metadata: dict[str, Any],
) -> int:
    folded = (tool_name or "").casefold()
    if (
        not result_present
        or (
            isinstance(outcome, str)
            and outcome in {"failed", "error", "denied", "rejected", "pending"}
        )
        or metadata.get("error")
        or any(
            verb in folded
            for verb in ("create", "update", "delete", "write", "execute", "save")
        )
    ):
        return 0
    if "plan" in folded or "context" in folded:
        return 1
    return 2


def _bounded_evidence(
    entries: list[dict[str, Any]], *, max_chars: int = 16_384
) -> list[dict[str, Any]]:
    """Keep deterministic, useful evidence within an explicit anchor budget."""

    ranked = sorted(
        entries,
        key=lambda item: (
            int(item.get("_priority", 3)),
            -int(item.get("_sequence", 0)),
        ),
    )
    selected: list[tuple[int, dict[str, Any]]] = []
    used = 2
    for source in ranked:
        sequence = int(source.get("_sequence", 0))
        full = {
            key: value
            for key, value in source.items()
            if key not in {"_sequence", "_priority"}
        }
        candidates = [full]
        if "call_id" in full:
            candidates.append(
                {
                    key: (full[key][:128] if key in {"args_preview", "diagnostic"} and isinstance(full[key], str) else full[key])
                    for key in (
                        "call_id",
                        "tool_name",
                        "call_event_id",
                        "call_part_offset",
                        "result_event_id",
                        "result_part_offset",
                        "args_preview",
                        "diagnostic",
                        "result_complete",
                        "artifact_id",
                        "outcome",
                    ) if full.get(key) is not None
                }
            )
        elif "file_id" in full:
            candidates.append(
                {
                    key: full.get(key)
                    for key in (
                        "source_event_id",
                        "part_offset",
                        "item_offset",
                        "file_id",
                        "name",
                        "mime",
                        "reference_hash",
                    )
                }
            )
        elif "source_event_id" in full:
            candidates.append(
                {
                    key: full.get(key)
                    for key in (
                        "source_event_id",
                        "key",
                        "type",
                        "schema_version",
                        "label",
                        "payload_hash",
                    )
                }
            )
        chosen = None
        for candidate in candidates:
            size = len(
                json.dumps(
                    candidate,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
            ) + 1
            if used + size <= max_chars:
                chosen = candidate
                used += size
                break
        if chosen is not None:
            selected.append((sequence, chosen))
    selected.sort(key=lambda item: item[0])
    result = [item for _, item in selected]
    omitted = len(entries) - len(result)
    if omitted:
        marker = {"omitted_count": omitted, "reason": "evidence_budget"}
        if used + len(json.dumps(marker, separators=(",", ":"))) + 1 <= max_chars:
            result.append(marker)
    return result


def _stable_facts(value: Any) -> dict[str, Any]:
    keys = {"id", "name", "code", "path", "alias", "key", "type", "status", "total", "count"}
    found: dict[str, Any] = {}
    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if key.casefold() in keys and isinstance(child, (str, int, float, bool, type(None))):
                    found.setdefault(key, child)
                elif key.casefold() in keys and isinstance(child, list) and len(child) <= 32:
                    found.setdefault(key, child)
                walk(child)
        elif isinstance(item, list):
            for child in item[:100]:
                walk(child)
    walk(value)
    return found


def _find_artifact_id(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in (
            "artifact_id",
            "overflow_handle",
            "overflow_content_handle",
            "platform_file_id",
            "file_id",
        ):
            if value.get(key):
                return str(value[key])
        for child in value.values():
            if found := _find_artifact_id(child):
                return found
    elif isinstance(value, list):
        for child in value:
            if found := _find_artifact_id(child):
                return found
    return None
