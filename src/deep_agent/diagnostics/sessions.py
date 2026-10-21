"""Read-only diagnostic queries over the existing Session Event Store.

No model history is loaded for aggregation. Local-calendar days are represented
as UTC bounds in SQL CASE expressions; this works on all supported SQL dialects
without depending on database timezone tables (including DST transitions).
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any
from weakref import WeakKeyDictionary
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, case, exists, func, literal, or_, select
from sqlalchemy.orm import aliased

from deep_agent.agent.observability import token_usage_available
from deep_agent.agent.cache_usage import summarize_cache_usage
from deep_agent.conversation.projector import ConversationProjectionError, project_session_events
from deep_agent.db.models import Agent, ChannelAccount, ChatSession, ChatSessionEvent
from deep_agent.db.session_scope import agent_scope_to_api
from deep_agent.db.stores._session import SessionBoundMixin
from deep_agent.time import ensure_utc, utc_rfc3339

REAL_EVENT_TYPES = ("user/message", "assistant/message", "tool/results", "model/request", "session/title", "compaction", "execution/error", "output/attempt")
EXECUTION_EVENT_TYPES = ("user/message", "assistant/message", "tool/results", "model/request", "execution/error")
STATUSES = ("running", "waiting", "completed", "stopped", "error", "unknown", "not_started")
_SLOTS: WeakKeyDictionary = WeakKeyDictionary()


class DiagnosticQueryError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


@asynccontextmanager
async def query_budget(*, timeout_seconds=8):
    """Bound concurrent heavy reads, queue wait and total read duration."""
    loop = asyncio.get_running_loop()
    slots = _SLOTS.setdefault(loop, asyncio.Semaphore(2))
    try:
        await asyncio.wait_for(slots.acquire(), timeout=0.1)
    except TimeoutError as exc:
        raise DiagnosticQueryError("诊断查询繁忙，请稍后重试", 503) from exc
    try:
        try:
            async with asyncio.timeout(timeout_seconds):
                yield
        except TimeoutError as exc:
            raise DiagnosticQueryError("诊断查询超时，请缩小时间范围或筛选条件", 503) from exc
    finally:
        slots.release()


@dataclass(frozen=True)
class SessionFilters:
    start: datetime
    end: datetime
    timezone: str = "UTC"
    agent_id: str | None = None
    user_id: str | None = None
    app_id: str | None = None
    source_type: str | None = None
    source_id: str | None = None
    query: str | None = None
    has_questions: bool | None = None
    has_execution_error: bool | None = None
    latest_status: str | None = None
    limit: int = 50
    offset: int = 0

    def __post_init__(self):
        object.__setattr__(self, "start", ensure_utc(self.start))
        object.__setattr__(self, "end", ensure_utc(self.end))
        if self.end <= self.start or self.end - self.start > timedelta(days=31):
            raise DiagnosticQueryError("请选择起止时间，查询跨度不能超过 31 天")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise DiagnosticQueryError("无效的时区") from exc
        if self.latest_status not in (None, *STATUSES):
            raise DiagnosticQueryError("无效的会话状态")
        if not 1 <= self.limit <= 200 or self.offset < 0:
            raise DiagnosticQueryError("无效的分页参数")
        for name in ("query", "agent_id", "user_id", "app_id", "source_type", "source_id"):
            value = getattr(self, name)
            if value is not None:
                value = value.strip()
                if len(value) > 256:
                    raise DiagnosticQueryError("筛选值过长")
                object.__setattr__(self, name, value or None)

    def public_range(self):
        return {"start": utc_rfc3339(self.start), "end": utc_rfc3339(self.end), "timezone": self.timezone}

    def days(self):
        zone = ZoneInfo(self.timezone)
        day = self.start.astimezone(zone).date()
        while True:
            beginning = datetime.combine(day, time.min, zone).astimezone(UTC)
            following = datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(UTC)
            if beginning >= self.end:
                return
            yield (day.isoformat(), max(beginning, self.start), min(following, self.end))
            day += timedelta(days=1)


def _range(column, filters):
    return and_(column >= filters.start, column < filters.end)


def _active_condition(entries):
    return or_(*(and_(ChatSession.session_id == entry.session_id,
                     ChatSession.agent_id == (entry.agent_id or ""),
                     ChatSession.user_id == entry.user_id) for entry in entries)) if entries else literal(False)


def _status_case(kind, state, has_execution, active_entries=()):
    return case(
        (_active_condition(active_entries), "running"),
        (kind == "execution/error", "error"),
        (and_(kind == "assistant/message", state == "pending"), "waiting"),
        (and_(kind == "assistant/message", state == "completed"), "completed"),
        (and_(kind == "assistant/message", state.in_(("interrupted", "cancelled"))), "stopped"),
        (and_(kind == "assistant/message", state == "failed"), "error"),
        (~has_execution, "not_started"), else_="unknown")


def execution_summary_statement(session_ids=None, active_entries=(), *, include_run_id=True):
    """Derive each latest Run/terminal once, without loading event payloads.

    The latest execution may have a NULL run ID; null-safe matching is required
    for historical records. The public run_id separately retains the newest
    non-NULL execution Run, matching the detail contract.
    """
    event = ChatSessionEvent
    heads = select(event.session_id,
        func.max(event.sequence).label("sequence"),
        func.max(case((event.run_id.is_not(None), event.sequence))).label("run_sequence"),
    ).where(event.event_type.in_(EXECUTION_EVENT_TYPES))
    if session_ids is not None:
        heads = heads.where(event.session_id.in_(session_ids))
    heads = heads.group_by(event.session_id).subquery()
    latest, terminal, public_run = (aliased(event) for _ in range(3))
    terminal_head = select(terminal.session_id, func.max(terminal.sequence).label("sequence"))\
        .select_from(heads).join(latest, and_(latest.session_id == heads.c.session_id,
            latest.sequence == heads.c.sequence))\
        .join(terminal, and_(terminal.session_id == latest.session_id,
            terminal.run_id.is_not_distinct_from(latest.run_id),
            terminal.event_type.in_(("user/message", "assistant/message", "execution/error"))))\
        .group_by(terminal.session_id).subquery()
    state = _status_case(terminal.event_type, terminal.status, heads.c.sequence.is_not(None), active_entries)
    columns = [ChatSession.session_id, state.label("latest_status")]
    if include_run_id:
        columns.insert(1, public_run.run_id)
    statement = select(*columns)\
        .outerjoin(heads, heads.c.session_id == ChatSession.session_id)\
        .outerjoin(terminal_head, terminal_head.c.session_id == ChatSession.session_id)\
        .outerjoin(terminal, and_(terminal.session_id == terminal_head.c.session_id,
            terminal.sequence == terminal_head.c.sequence))
    if include_run_id:
        statement = statement.outerjoin(public_run, and_(public_run.session_id == heads.c.session_id,
            public_run.sequence == heads.c.run_sequence))
    if session_ids is not None:
        statement = statement.where(ChatSession.session_id.in_(session_ids))
    return statement


def question_summary(event) -> str:
    """Reuse the existing server Conversation projection for original input."""
    try:
        projected = project_session_events([event], session_id=event.session_id)
    except (ConversationProjectionError, KeyError, TypeError, ValueError):
        # A malformed historical message must not block the Event inspector.
        # The full /history endpoint still reports its explicit 409 failure.
        return "输入内容无法读取"
    question = next((item for item in projected.items if item.kind == "user_message"), None)
    if question is None:
        return "输入内容未记录"
    if question.text:
        return " ".join(question.text.split())[:320]
    if question.attachments:
        return "附件输入"
    if question.context_refs:
        return "上下文输入"
    return "输入内容未记录"


def event_link(event, session, agent_name=None, *, error=False):
    value = {"session_id": session.session_id, "run_id": event.run_id,
        "event_id": event.event_id, "sequence": event.sequence,
        "created_at": utc_rfc3339(event.created_at), "user_id": session.user_id,
        "agent_id": agent_scope_to_api(session.agent_id), "agent_name": agent_name}
    if error:
        payload = event.payload or {}
        value.update(message=payload.get("message"), code=payload.get("code"), phase=payload.get("phase"))
        value["summary"] = value["message"] or value["code"] or "执行异常"
    else:
        value["summary"] = question_summary(event)
    return value


class DiagnosticSessionQueries(SessionBoundMixin):
    def __init__(self, session_factory, active_entries=()):
        super().__init__(session_factory)
        self.active_entries = active_entries

    async def user_option_ids(self, f, *, after=None):
        # One bounded identity page, independent of the session table's page and user filter.
        scoped = SessionFilters(f.start, f.end, timezone=f.timezone,
                                agent_id=f.agent_id, source_type=f.source_type, source_id=f.source_id)
        identity = ChatSession.user_id
        stmt = select(identity).where(*self._filters(scoped), identity != "")
        if after is not None:
            stmt = stmt.where(identity > after)
        stmt = stmt.distinct().order_by(identity).limit(101)
        async with self._read() as db:
            ids = list((await db.scalars(stmt)).all())
        return {"ids": ids[:100], "next_after": ids[99] if len(ids) > 100 else None}

    async def _agent_options(self, db):
        has_channel = exists(select(ChannelAccount.id).where(ChannelAccount.agent_id == Agent.id))
        rows = (await db.execute(select(Agent.id, Agent.name, has_channel).order_by(Agent.name, Agent.id))).all()
        return [{"agent_id": agent_scope_to_api(row[0]), "agent_name": row[1], "has_channel": bool(row[2])} for row in rows]

    async def _app_options(self, db):
        # Keep choices independent of the selected app and current table page.
        identity = ChatSession.app_id
        return list((await db.scalars(select(identity).where(
            identity.is_not(None), func.trim(identity) != ""
        ).distinct().order_by(identity).limit(200))).all())

    def _filters(self, f, *, activity=True, status=True):
        from deep_agent.db.session_source import session_source_conditions
        conditions = session_source_conditions(f.source_type, f.source_id)
        if f.agent_id:
            conditions.append(ChatSession.agent_id == f.agent_id)
        if f.user_id:
            conditions.append(ChatSession.user_id == f.user_id)
        if f.app_id:
            conditions.append(ChatSession.app_id == f.app_id)
        if activity:
            event = aliased(ChatSessionEvent)
            real = exists(select(event.event_id).where(event.session_id == ChatSession.session_id,
                event.event_type.in_(REAL_EVENT_TYPES), _range(event.created_at, f)))
            conditions.append(or_(real, _range(ChatSession.created_at, f)))
        if f.query:
            # Exact IDs identify one object, not every title mentioning it.
            # This uncorrelated PK lookup does not add a per-row DB round trip.
            exact = aliased(ChatSession)
            exact_exists = exists(select(exact.session_id).where(exact.session_id == f.query))
            conditions.append(or_(ChatSession.session_id == f.query,
                and_(~exact_exists, ChatSession.title.icontains(f.query, autoescape=True))))
        for value, kind in ((f.has_questions, "user/message"), (f.has_execution_error, "execution/error")):
            if value is not None:
                event = aliased(ChatSessionEvent)
                found = exists(select(event.event_id).where(event.session_id == ChatSession.session_id,
                    event.event_type == kind, _range(event.created_at, f)))
                conditions.append(found if value else ~found)
        if status and f.latest_status:
            # Keep status before COUNT/pagination, but derive it with grouped
            # event heads instead of repeating nested LIMIT subqueries in CASE.
            # The SQL subquery also avoids an unbounded Python list of IDs.
            candidates = select(ChatSession.session_id).where(*conditions).correlate(None)
            execution = execution_summary_statement(
                candidates, self.active_entries, include_run_id=False,
            ).subquery()
            conditions.append(ChatSession.session_id.in_(
                select(execution.c.session_id).where(execution.c.latest_status == f.latest_status)))
        return conditions

    async def overview(self, f: SessionFilters):
        async with self._read() as db:
            sessions = (await db.execute(select(func.count()).select_from(ChatSession).where(*self._filters(f)))).scalar_one()
            # A ranged question/error already establishes real activity. Avoid
            # repeating an event-exists lookup for every matching event below.
            scope = self._filters(f, activity=False)
            events = select(ChatSessionEvent).join(ChatSession, ChatSession.session_id == ChatSessionEvent.session_id).where(
                *scope, _range(ChatSessionEvent.created_at, f))
            facts = events.with_only_columns(ChatSessionEvent.event_id, ChatSessionEvent.session_id,
                ChatSessionEvent.created_at, ChatSessionEvent.event_type, ChatSession.agent_id, ChatSession.user_id).subquery()
            is_question = facts.c.event_type == "user/message"
            aggregate = (await db.execute(select(
                func.count(case((is_question, facts.c.event_id))),
                func.count(func.distinct(case((is_question, facts.c.user_id)))),
                func.count(func.distinct(case((facts.c.event_type == "execution/error", facts.c.session_id)))),
            ).select_from(facts))).one()
            days = list(f.days())
            day_case = case(*[(and_(facts.c.created_at >= start, facts.c.created_at < end), date) for date, start, end in days])
            counts = dict((await db.execute(select(day_case.label("day"), func.count()).select_from(facts)
                .where(is_question).group_by(day_case))).all())
            agents = (await db.execute(select(facts.c.agent_id, Agent.name, func.count().label("count"))
                .select_from(facts).outerjoin(Agent, Agent.id == facts.c.agent_id).where(is_question)
                .group_by(facts.c.agent_id, Agent.name).order_by(func.count().desc(), facts.c.agent_id))).all()
            users = (await db.execute(select(facts.c.user_id, func.count().label("count"))
                .where(is_question).group_by(facts.c.user_id).order_by(func.count().desc(), facts.c.user_id).limit(20))).all()
            # Aggregate narrow keys within the selected scope first. Only read
            # payloads for the final ten rows, never all inputs or ten rows per
            # session. Sequence is the Event Store's authoritative input order.
            first_questions = events.with_only_columns(ChatSessionEvent.session_id,
                func.min(ChatSessionEvent.sequence).label("sequence"))\
                .where(ChatSessionEvent.event_type == "user/message")\
                .group_by(ChatSessionEvent.session_id).subquery("first_questions")
            question_page = select(ChatSessionEvent.event_id, ChatSessionEvent.created_at)\
                .join(first_questions, and_(ChatSessionEvent.session_id == first_questions.c.session_id,
                    ChatSessionEvent.sequence == first_questions.c.sequence))\
                .order_by(ChatSessionEvent.created_at.desc(), ChatSessionEvent.event_id.desc())\
                .limit(10).subquery("question_page")
            questions = (await db.execute(select(ChatSessionEvent, ChatSession, Agent.name)
                .join(question_page, question_page.c.event_id == ChatSessionEvent.event_id)
                .join(ChatSession, ChatSession.session_id == ChatSessionEvent.session_id)
                .outerjoin(Agent, Agent.id == ChatSession.agent_id)
                .order_by(question_page.c.created_at.desc(), question_page.c.event_id.desc()))).all()
            errors = (await db.execute(select(ChatSessionEvent, ChatSession, Agent.name)
                .join(ChatSession, ChatSession.session_id == ChatSessionEvent.session_id)
                .outerjoin(Agent, Agent.id == ChatSession.agent_id).where(*scope,
                    ChatSessionEvent.event_type == "execution/error", _range(ChatSessionEvent.created_at, f))
                .order_by(ChatSessionEvent.created_at.desc(), ChatSessionEvent.event_id.desc()).limit(5))).all()
            return {"range": f.public_range(), "generated_at": utc_rfc3339(datetime.now(UTC)),
                "agent_options": await self._agent_options(db),
                "metrics": {"session_count": sessions, "question_count": aggregate[0],
                    "active_user_count": aggregate[1], "error_session_count": aggregate[2]},
                "trend": [{"date": day, "start": utc_rfc3339(start), "end": utc_rfc3339(end),
                    "question_count": counts.get(day, 0)} for day, start, end in days],
                "agents": [{"agent_id": agent_scope_to_api(row[0]), "agent_name": row[1], "question_count": row[2]} for row in agents],
                "active_users": [{"user_id": row[0], "question_count": row[1]} for row in users],
                "recent_questions": [event_link(event, session, name) for event, session, name in questions],
                "recent_errors": [event_link(event, session, name, error=True) for event, session, name in errors]}

    def _activity(self, session_ids):
        event = ChatSessionEvent
        summary = select(event.session_id, func.max(event.created_at).label("last_activity"))\
            .where(event.session_id.in_(session_ids), event.event_type.in_(REAL_EVENT_TYPES))\
            .group_by(event.session_id).subquery()
        # Preserve creation-time fallback, including sessions without events.
        value = case((summary.c.last_activity > ChatSession.created_at, summary.c.last_activity),
                     else_=ChatSession.created_at)
        return summary, value

    async def list(self, f: SessionFilters):
        async with self._read() as db:
            scope = self._filters(f)
            total = (await db.execute(select(func.count()).select_from(ChatSession).where(*scope))).scalar_one()
            candidates = select(ChatSession.session_id).where(*self._filters(f, status=False)).correlate(None)
            activity_head, activity = self._activity(candidates)
            # Display-only states are derived in one page-bounded batch below.
            # State filtering remains in scope, before count/order/pagination.
            rows = (await db.execute(select(ChatSession, Agent.name, literal(None), activity)
                .outerjoin(activity_head, activity_head.c.session_id == ChatSession.session_id)
                .outerjoin(Agent, Agent.id == ChatSession.agent_id).where(*scope)
                .order_by(activity.desc(), ChatSession.session_id.desc()).limit(f.limit).offset(f.offset))).all()
            items = await self._items(db, rows, f)
            agent_options = await self._agent_options(db)
            app_options = await self._app_options(db)
        return {"items": items, "total": total, "limit": f.limit, "offset": f.offset,
            "range": f.public_range(), "agent_options": agent_options, "app_options": app_options}

    async def detail(self, session_id):
        async with self._read() as db:
            activity_head, activity = self._activity([session_id])
            rows = (await db.execute(select(ChatSession, Agent.name, literal(None), activity)
                .outerjoin(activity_head, activity_head.c.session_id == ChatSession.session_id)
                .outerjoin(Agent, Agent.id == ChatSession.agent_id).where(ChatSession.session_id == session_id))).all()
            if not rows:
                raise DiagnosticQueryError("会话不存在或已删除", 404)
            items = await self._items(db, rows, None)
            row = rows[0][0]
            result = items[0]
            result.update(app_id=row.app_id, space_id=row.space_id, language=None)
            return result

    async def identity(self, session_id):
        """Resolve the target directly after admin authorization, no owner spoof."""
        async with self._read() as db:
            row = (await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))).scalar_one_or_none()
            if row is None:
                raise DiagnosticQueryError("会话不存在或已删除", 404)
            return {"session_id": row.session_id, "agent_id": agent_scope_to_api(row.agent_id), "user_id": row.user_id}

    async def _items(self, db, rows, f):
        ids = [row[0].session_id for row in rows]
        if not ids:
            return []
        event = ChatSessionEvent
        in_range = _range(event.created_at, f) if f else literal(True)
        # Only the current page's newest input bodies are fetched. Counts and
        # latest input selection stay in SQL, with no N+1 history projection.
        q = select(event.session_id, func.max(event.sequence).label("sequence"), func.count().label("count")).where(
            event.session_id.in_(ids), event.event_type == "user/message", in_range).group_by(event.session_id).subquery()
        questions = {row[0].session_id: (row[0], row[1]) for row in (await db.execute(select(event, q.c.count)
            .join(q, and_(event.session_id == q.c.session_id, event.sequence == q.c.sequence)))).all()}
        errors = dict((await db.execute(select(event.session_id, func.count()).where(event.session_id.in_(ids),
            event.event_type == "execution/error", in_range).group_by(event.session_id))).all())
        execution = {sid: (run, state) for sid, run, state in (await db.execute(
            execution_summary_statement(ids, self.active_entries))).all()}
        from deep_agent.db.stores.channel import ChannelStore
        origins = await ChannelStore._get_session_origins(db, ids)
        items = []
        for session, name, state, activity in rows:
            question = questions.get(session.session_id)
            active = next((entry for entry in self.active_entries if entry.session_id == session.session_id
                and (entry.agent_id or "") == session.agent_id and entry.user_id == session.user_id), None)
            items.append({"session_id": session.session_id, "title": session.title,
                "app_id": session.app_id,
                "agent_id": agent_scope_to_api(session.agent_id), "agent_name": name, "user_id": session.user_id,
                "source_type": session.source_type or "internal", "source_id": session.source_id,
                "source": "channel" if (session.source_type or "").startswith("channel:") else "direct", "channel": origins.get(session.session_id),
                "created_at": utc_rfc3339(session.created_at), "updated_at": utc_rfc3339(activity),
                "last_activity_at": utc_rfc3339(activity), "latest_status": execution[session.session_id][1] if state is None else state,
                "run_id": active.run_id if active else execution[session.session_id][0],
                "question_count": question[1] if question else 0, "error_count": errors.get(session.session_id, 0),
                "recent_question": event_link(question[0], session, name) if question else None})
        return items


def detail_event_metadata(events, active_run_id=None):
    """Full detail only: grouped messages prevent per-part usage duplication."""
    from deep_agent.db.event_codec import MESSAGE_EVENT_TYPES, group_message_events
    runs: dict[str, dict] = {}
    for event in events:
        if not event.run_id or event.event_type not in EXECUTION_EVENT_TYPES:
            continue
        run = runs.setdefault(event.run_id, {"run_id": event.run_id, "turn_id": event.turn_id,
            "start": utc_rfc3339(event.created_at), "end": utc_rfc3339(event.created_at),
            "status": "unknown", "event_ids": []})
        run["start"] = min(run["start"], utc_rfc3339(event.created_at))
        run["end"] = max(run["end"], utc_rfc3339(event.created_at))
        run["event_ids"].append(event.event_id)
        if event.event_type == "execution/error":
            run["status"] = "error"
        elif event.event_type == "assistant/message" and event.status:
            run["status"] = {"pending": "waiting", "completed": "completed", "interrupted": "stopped",
                "cancelled": "stopped", "failed": "error"}.get(event.status, "unknown")
    if active_run_id in runs:
        runs[active_run_id]["status"] = "running"
    totals: dict[str, int] = {}
    cache_messages: list[dict] = []
    run_cache_messages: dict[str, list[dict]] = {}
    usage_missing_responses = 0
    last_model = last_provider = conversation_id = None
    message_count = attachment_count = tool_call_count = 0
    try:
        grouped = group_message_events(events)
    except (KeyError, ValueError, TypeError):
        return {"runs": list(runs.values()), "usage": None, "model_name": None, "provider_name": None,
            "conversation_id": None, "message_count": None, "turn_count": None, "attachment_count": None,
            "tool_call_count": None, "cache_usage": summarize_cache_usage([]), "metadata_error": "部分历史消息无法解析，请切换轨迹查看已保存事件"}
    for event in grouped:
        kind = event.get("event_type") if isinstance(event, dict) else event.event_type
        if kind not in MESSAGE_EVENT_TYPES:
            continue
        payload = event.get("payload", {}) if isinstance(event, dict) else event.payload
        message = payload.get("message") or {}
        if not message:
            continue
        message_count += 1
        metadata = message.get("metadata") or {}
        output_name = (metadata.get("deepagent_output") or {}).get("name")
        dispatch_rejected = (metadata.get("deepagent_dispatch") or {}).get("rejected")
        for part in message.get("parts") or []:
            if (part.get("part_kind") in ("tool-call", "builtin-tool-call")
                    and not dispatch_rejected and part.get("tool_name") != output_name):
                tool_call_count += 1
            if part.get("part_kind") == "user-prompt" and isinstance(part.get("content"), list):
                attachment_count += sum(isinstance(item, dict) and item.get("kind") == "attachment-ref"
                    for item in part["content"])
        if message.get("kind") != "response":
            continue
        run_id = event.get("run_id") if isinstance(event, dict) else event.run_id
        run = runs.get(run_id)
        cache_messages.append(message)
        if run is not None:
            run_cache_messages.setdefault(run_id, []).append(message)
        last_model = message.get("model_name") or last_model
        last_provider = message.get("provider_name") or last_provider
        conversation_id = message.get("conversation_id") or conversation_id
        if run is not None:
            for name in ("model_name", "provider_name", "conversation_id", "finish_reason"):
                if message.get(name):
                    run[name] = message[name]
            run["recorded_model_responses"] = run.get("recorded_model_responses", 0) + 1
        if not token_usage_available(message.get("usage")):
            usage_missing_responses += 1
            if run is not None:
                run["usage_missing_responses"] = run.get("usage_missing_responses", 0) + 1
            continue
        for key, value in (message.get("usage") or {}).items():
            if key in ("input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens", "input_audio_tokens", "output_audio_tokens") and isinstance(value, int) and not isinstance(value, bool):
                # Cache defaults of zero do not prove provider measurement.
                if key.startswith("cache_") and value == 0:
                    continue
                totals[key] = totals.get(key, 0) + value
                if run is not None:
                    usage = run.setdefault("usage", {})
                    usage[key] = usage.get(key, 0) + value
    if "input_tokens" in totals and "output_tokens" in totals:
        totals["total_tokens"] = totals["input_tokens"] + totals["output_tokens"]
    for run in runs.values():
        run["cache_usage"] = summarize_cache_usage(run_cache_messages.get(run["run_id"], []))
        run["usage_missing_responses"] = run.get("usage_missing_responses", 0)
        run["usage_partial"] = bool(run.get("usage") and run["usage_missing_responses"])
        usage = run.get("usage") or {}
        if "input_tokens" in usage and "output_tokens" in usage:
            usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    return {"runs": list(runs.values()), "usage": totals or None,
        "cache_usage": summarize_cache_usage(cache_messages),
        "usage_missing_responses": usage_missing_responses,
        "usage_partial": bool(totals and usage_missing_responses), "model_name": last_model,
        "provider_name": last_provider, "conversation_id": conversation_id, "message_count": message_count,
        "attachment_count": attachment_count, "tool_call_count": tool_call_count,
        "turn_count": len({event.turn_id for event in events if event.event_type == "user/message" and event.turn_id})}


def project_diagnostic_history(events, session_id):
    """Share the ordinary projector while reporting corrupt history explicitly."""
    from deep_agent.db.event_codec import MESSAGE_EVENT_TYPES
    try:
        for event in events:
            if event.event_type in MESSAGE_EVENT_TYPES and not isinstance(event.payload.get("message"), dict):
                raise ConversationProjectionError("Message Event is missing payload.message")
        return project_session_events(events, session_id=session_id)
    except (KeyError, TypeError, ValueError) as exc:
        raise ConversationProjectionError("历史消息无法完整投影") from exc
