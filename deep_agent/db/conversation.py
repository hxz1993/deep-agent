from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import sys
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from sqlalchemy import and_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.agent.history import first_user_prompt
from deep_agent.db.event_codec import MESSAGE_EVENT_TYPES, decode_message_events
from deep_agent.db.ids import new_turn_id
from deep_agent.db.session_list import SessionListFilters, SessionSummaryRow
from deep_agent.db.session_scope import NO_AGENT_SCOPE, SessionOwner, normalize_agent_scope, runtime_session_key
from deep_agent.db.stores.chat_session.base import ChatSessionStoreBase
from deep_agent.db.stores.event.base import LifecycleStatus, SessionEventSnapshot, SessionEventStore
from deep_agent.db.stores._session import serialized_sqlite_session
from deep_agent.db.models import ChatSession, ChatSessionEvent
from deep_agent.db.serialization import serialize_message

__all__ = ["SessionStore", "SqlConversationCoordinator", "SqlConversationStore"]


@runtime_checkable
class SessionStore(Protocol):
    async def get_history(
        self, session_id: str, *, agent_id: str | None = None
    ) -> list[ModelMessage]: ...

    async def append(
        self, session_id: str, messages: list[ModelMessage], *,
        agent_id: str | None = None, user_id: str | None = None,
        app_id: str | None = None, space_id: str | None = None,
        turn_id: str | None = None, run_id: str | None = None,
        first_message_ordinal: int = 0,
    ) -> list[SessionEventSnapshot]: ...

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
    async def list_session_summaries(
        self, filters: SessionListFilters,
    ) -> tuple[list[SessionSummaryRow], int]: ...


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
        first_message_ordinal: int = 0,
    ) -> list[SessionEventSnapshot]:
        if not messages:
            return []
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
            resolved_turn_id = turn_id
            if resolved_turn_id is None and not creates_turn:
                resolved_turn_id = await self._events._latest_turn_id(db, session_id)
            resolved_turn_id = resolved_turn_id or new_turn_id()
            now = datetime.now(timezone.utc)
            title = first_user_prompt(messages) if not existing_owner.exists else None
            session = await self._chat_sessions._ensure(
                db, session_id, agent_id=scope, user_id=user,
                app_id=app_id, space_id=space_id, title=title, now=now,
            )
            rows = await self._events._append_messages(
                db, session, messages, turn_id=resolved_turn_id, run_id=run_id, now=now,
                first_message_ordinal=first_message_ordinal,
            )
            return [SessionEventSnapshot.from_row(row) for row in rows]

    async def append_lifecycle(
        self, session_id: str, *, event_type: str, status: LifecycleStatus,
        agent_id: str | None, user_id: str | None, turn_id: str | None,
        run_id: str | None, payload: dict[str, Any],
        app_id: str | None = None, space_id: str | None = None,
    ) -> SessionEventSnapshot:
        if event_type not in {"run", "compaction"}:
            raise ValueError("Only run and compaction are lifecycle events")
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

    async def update_lifecycle(
        self, event_id: str, *, status: LifecycleStatus, payload: dict[str, Any],
    ) -> SessionEventSnapshot | None:
        return await self._events.update_lifecycle(event_id, status=status, payload=payload)

    async def persist_compaction_projection(
        self, session_id: str, *, agent_id: str | None, user_id: str | None,
        raw_history: list[ModelMessage], projected_history: list[ModelMessage],
        summary_markdown: str, trigger: str, model_id: str | None,
        estimated_before: int, estimated_after: int, target_tokens: int,
        context_window_tokens: int | None, preserved_tail_tokens: int,
        duration_ms: int,
        compaction_event_id: str | None = None,
        pruned_tool_results: list[dict[str, Any]] | None = None,
        summary_call: dict[str, Any] | None = None,
    ) -> SessionEventSnapshot | None:
        scope, user = self._required_identity(agent_id, user_id)
        suffix = _common_message_suffix(raw_history, projected_history)
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
            message_groups: list[list[ChatSessionEvent]] = []
            for row in message_rows:
                if not message_groups or message_groups[-1][0].message_id != row.message_id:
                    message_groups.append([row])
                else:
                    message_groups[-1].append(row)
            if len(message_groups) != len(raw_history) or covered_messages > len(message_groups):
                return None
            through_sequence = max(row.sequence for row in message_groups[covered_messages - 1])
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
            result_event_ids = {
                row.call_id: row.event_id
                for row in covered_rows
                if row.call_id
                and row.event_type
                in {
                    "tool/result",
                    "tool/search_result",
                    "capability/load_result",
                    "tool/native_result",
                }
            }
            pruned_records = []
            for record in pruned_tool_results or []:
                item = dict(record)
                item["source_event_id"] = result_event_ids.get(item.get("call_id"))
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
                "tool_evidence": tool_evidence,
                "context_evidence": context_evidence,
            }
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
                    "format": "dsh-structured-checkpoint-v1",
                    **rendered,
                    "rendered_hash": _value_hash(rendered),
                },
                "budget": {
                    "context_window_tokens": context_window_tokens,
                    "estimated_before": estimated_before,
                    "target_tokens": target_tokens,
                    "estimated_after": estimated_after,
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

    async def latest_turn_id(self, session_id: str) -> str | None:
        async with self._session_factory() as db:
            return await self._events._latest_turn_id(db, session_id)

    async def latest_pending_run_id(self, session_id: str, turn_id: str) -> str | None:
        return await self._events.latest_pending_run_id(session_id, turn_id)

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
        created_before: datetime | None = None, limit: int = 2_000,
    ) -> list[dict[str, Any]]:
        scope, user = self._required_identity(agent_id, user_id)
        stmt = (
            select(
                ChatSessionEvent.event_id,
                ChatSessionEvent.session_id,
                ChatSessionEvent.sequence,
                ChatSessionEvent.turn_id,
                ChatSessionEvent.run_id,
                ChatSessionEvent.event_type,
                ChatSessionEvent.tool_name,
                ChatSessionEvent.call_id,
                ChatSessionEvent.search_text,
                ChatSessionEvent.created_at,
                ChatSession.title,
            )
            .join(ChatSession, ChatSession.session_id == ChatSessionEvent.session_id)
            .where(
                ChatSession.agent_id == scope,
                ChatSession.user_id == user,
                ChatSessionEvent.search_text.is_not(None),
                ChatSessionEvent.event_type != "assistant/reasoning",
            )
        )
        if session_id is not None:
            stmt = stmt.where(ChatSessionEvent.session_id == session_id)
        if event_types:
            stmt = stmt.where(ChatSessionEvent.event_type.in_(event_types))
        if tool_name:
            stmt = stmt.where(ChatSessionEvent.tool_name == tool_name)
        if call_id:
            stmt = stmt.where(ChatSessionEvent.call_id == call_id)
        if created_after:
            stmt = stmt.where(ChatSessionEvent.created_at >= created_after)
        if created_before:
            stmt = stmt.where(ChatSessionEvent.created_at <= created_before)
        stmt = stmt.order_by(ChatSessionEvent.created_at.desc()).limit(
            min(max(limit, 1), 2_000)
        )
        async with self._session_factory() as db:
            rows = (await db.execute(stmt)).all()
        return [dict(row._mapping) for row in rows]

    async def read_authorized_events(
        self, *, agent_id: str, user_id: str, event_id: str | None = None,
        session_id: str | None = None, call_id: str | None = None,
        before: int = 0, after: int = 0,
    ) -> list[SessionEventSnapshot]:
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
                    return []
                low = max(1, int(target.sequence) - min(max(before, 0), 20))
                high = int(target.sequence) + min(max(after, 0), 20)
                stmt = select(ChatSessionEvent).join(ChatSession, authorized).where(
                    ChatSessionEvent.session_id == target.session_id,
                    ChatSessionEvent.sequence.between(low, high),
                )
            elif session_id is not None and call_id is not None:
                stmt = select(ChatSessionEvent).join(ChatSession, authorized).where(
                    ChatSessionEvent.session_id == session_id,
                    ChatSessionEvent.call_id == call_id,
                )
            else:
                raise ValueError("event_id or session_id + call_id is required")
            rows = list(
                (await db.execute(stmt.order_by(ChatSessionEvent.sequence))).scalars()
            )
        return [SessionEventSnapshot.from_row(row) for row in rows]

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
        self._locks.pop(runtime_session_key(session_id, agent_id=agent_id), None)

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

    async def list_session_summaries(
        self, filters: SessionListFilters,
    ) -> tuple[list[SessionSummaryRow], int]:
        return await self._chat_sessions.list_summaries(
            agent_id=filters.agent_id, user_id=filters.user_id,
            limit=filters.limit, offset=filters.offset,
            updated_after=filters.updated_after, updated_before=filters.updated_before,
            has_messages=filters.has_messages, sort=filters.sort,
        )

    async def interrupt_running(self) -> int:
        return await self._events.interrupt_running()


SqlConversationStore = SqlConversationCoordinator


def _common_message_suffix(raw: list[ModelMessage], projected: list[ModelMessage]) -> int:
    raw_payloads = [serialize_message(message) for message in raw]
    projected_payloads = [serialize_message(message) for message in projected]
    count = 0
    while count < min(len(raw_payloads), len(projected_payloads)):
        if raw_payloads[-1 - count] != projected_payloads[-1 - count]:
            break
        count += 1
    return count


def _event_rows_hash(rows: list[ChatSessionEvent]) -> str:
    return _value_hash(
        [
            {
                "sequence": int(row.sequence), "event_type": row.event_type,
                "message_id": row.message_id, "part_index": row.part_index,
                "call_id": row.call_id, "tool_name": row.tool_name,
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
    calls: dict[str, ChatSessionEvent] = {}
    results: dict[str, ChatSessionEvent] = {}
    for row in rows:
        if not row.call_id:
            continue
        if row.event_type in {"tool/call", "tool/search_call", "capability/load_call", "tool/native_call"}:
            calls[row.call_id] = row
        elif row.event_type in {"tool/result", "tool/search_result", "capability/load_result", "tool/native_result"}:
            results[row.call_id] = row
    evidence: list[dict[str, Any]] = []
    for call_id in sorted(set(calls) | set(results), key=lambda item: (results.get(item) or calls[item]).sequence):
        call = calls.get(call_id)
        result = results.get(call_id)
        call_part = ((call.payload.get("part") or {}) if call else {})
        result_part = ((result.payload.get("part") or {}) if result else {})
        content = result_part.get("content")
        raw_metadata = result_part.get("metadata")
        metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
        factual_content = metadata.get("inline_fallback", content)
        artifact_id = _find_artifact_id(metadata) or _find_artifact_id(result_part)
        result_hash = (
            metadata.get("inline_fallback_hash")
            or metadata.get("overflow_hash")
            or (_value_hash(factual_content) if result else None)
        )
        evidence.append(
            {
                "_sequence": int((result or call).sequence),
                "_priority": _tool_evidence_priority(
                    tool_name=(call.tool_name if call else result.tool_name if result else None),
                    result_present=result is not None,
                    outcome=result_part.get("outcome") if result else "pending",
                    metadata=metadata,
                ),
                "call_id": call_id,
                "tool_name": (call.tool_name if call else result.tool_name if result else None),
                "call_event_id": call.event_id if call else None,
                "result_event_id": result.event_id if result else None,
                "args_hash": _value_hash(call_part.get("args")) if call else None,
                "result_hash": result_hash,
                "result_complete": bool(result)
                and bool(
                    metadata.get("inline_fallback") is not None
                    or artifact_id
                    or not metadata.get("truncated")
                ),
                "artifact_id": artifact_id,
                "outcome": result_part.get("outcome") if result else "pending",
                "stable_facts": _stable_facts(factual_content),
            }
        )
    return evidence


def _context_evidence(rows: list[ChatSessionEvent]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for row in rows:
        if row.event_type != "user/message":
            continue
        part = row.payload.get("part") or {}
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
                "key": item.get("key"), "type": item.get("type"),
                "schema_version": item.get("schema_version"), "label": item.get("label"),
                "payload_hash": _value_hash(payload),
                "stable_facts": _stable_facts(payload),
            }
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
            if len(encoded) <= 8192:
                entry["payload"] = payload
            evidence.append(entry)
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
                    key: full.get(key)
                    for key in (
                        "call_id",
                        "tool_name",
                        "call_event_id",
                        "result_event_id",
                        "result_complete",
                        "artifact_id",
                        "outcome",
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
