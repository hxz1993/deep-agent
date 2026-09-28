"""Administrator usage projections over the existing event ledger."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select

from deep_agent.db.models import Agent, ChatSession, ChatSessionEvent, LlmModel, LlmProvider
from deep_agent.db.session_source import session_source_conditions
from deep_agent.db.stores._session import SessionBoundMixin
from deep_agent.diagnostics.sessions import DiagnosticQueryError, SessionFilters

MAX_USAGE_ROWS = 100_000
PAGE = 500
DIMENSIONS = ("model", "user", "agent", "purpose", "date")
METRICS = ("input_tokens", "output_tokens")


def _empty():
    return {"requests": 0, "input_tokens": 0, "output_tokens": 0,
            "total_tokens": 0, "missing_requests": 0,
            "input_reported_requests": 0, "output_reported_requests": 0}


def _add(target, call):
    target["requests"] += 1
    missing = False
    for field in METRICS:
        value = call.get(field)
        if type(value) is int and value >= 0:
            target[field] += value
            target[field.replace("_tokens", "_reported_requests")] += 1
        else:
            missing = True
    target["total_tokens"] = target["input_tokens"] + target["output_tokens"]
    target["missing_requests"] += int(missing)


def _key(dimension, call, event):
    if dimension == "date": return event["date"]
    if dimension == "user": return event["user_id"] or ""
    if dimension == "agent": return event["agent_id"] or ""
    if dimension == "purpose": return call.get("purpose") or "chat"
    return call.get("model_id") or "legacy:" + "|".join(
        (str(call.get("provider_name") or ""), str(call.get("model_name") or "")))


def _project(calls, *, dimension):
    groups = defaultdict(_empty)
    for event, call in calls:
        _add(groups[_key(dimension, call, event)], call)
    return [{"key": key, **value} for key, value in groups.items()]


class UsageQueries(SessionBoundMixin):
    async def collect(self, filters: SessionFilters):
        """Fetch narrow indexed rows, then de-duplicate before any aggregation."""
        seen = {}
        cursor = None
        scanned = 0
        async with self._read() as db:
            while True:
                statement = (select(ChatSessionEvent.event_id, ChatSessionEvent.session_id,
                    ChatSessionEvent.created_at, ChatSessionEvent.usage,
                    ChatSession.user_id, ChatSession.agent_id)
                    .join(ChatSession, ChatSession.session_id == ChatSessionEvent.session_id)
                    .where(ChatSessionEvent.created_at >= filters.start,
                           ChatSessionEvent.created_at < filters.end,
                           ChatSessionEvent.usage.is_not(None))
                    .order_by(ChatSessionEvent.created_at, ChatSessionEvent.event_id).limit(PAGE))
                statement = statement.where(*session_source_conditions(filters.source_type, filters.source_id))
                if filters.agent_id: statement = statement.where(ChatSession.agent_id == filters.agent_id)
                if filters.user_id: statement = statement.where(ChatSession.user_id == filters.user_id)
                if filters.app_id: statement = statement.where(ChatSession.app_id == filters.app_id)
                if cursor:
                    statement = statement.where(or_(ChatSessionEvent.created_at > cursor[0],
                        and_(ChatSessionEvent.created_at == cursor[0], ChatSessionEvent.event_id > cursor[1])))
                batch = (await db.execute(statement)).all()
                if not batch: break
                scanned += len(batch)
                if scanned > MAX_USAGE_ROWS:
                    raise DiagnosticQueryError("用量记录过多，请缩小时间范围或选择 Agent", 503)
                for event_id, session_id, created_at, document, user_id, agent_id in batch:
                    if not isinstance(document, dict) or document.get("version") != 1:
                        continue
                    event = {"event_id": event_id, "session_id": session_id,
                        "created_at": created_at, "date": created_at.astimezone(ZoneInfo(filters.timezone)).date().isoformat(),
                        "user_id": user_id, "agent_id": agent_id}
                    for call in document.get("calls") or []:
                        if not isinstance(call, dict) or call.get("kind") != "model": continue
                        request_id = call.get("request_id")
                        if not isinstance(request_id, str) or not request_id: continue
                        key = (session_id, request_id)
                        previous = seen.get(key)
                        rank = {"unreported": 0, "partial": 1, "reported": 2}
                        if previous is None or rank.get(call.get("state"), 0) > rank.get(previous[1].get("state"), 0):
                            seen[key] = (event, call)
                cursor = (batch[-1][2], batch[-1][0])
                if len(batch) < PAGE: break
            calls = list(seen.values())
            model_ids = {call.get("model_id") for _, call in calls if call.get("model_id")}
            models = {}
            if model_ids:
                rows = (await db.execute(select(LlmModel.id, LlmModel.name, LlmModel.model,
                    LlmProvider.provider_type, LlmProvider.name.label("provider_name"), LlmProvider.id.label("provider_id"))
                    .outerjoin(LlmProvider, LlmProvider.id == LlmModel.provider_id)
                    .where(LlmModel.id.in_(model_ids)))).all()
                models = {row.id: {"name": row.name, "model_name": row.model,
                    "provider_type": row.provider_type, "provider_name": row.provider_name, "provider_id": row.provider_id} for row in rows}
            agents = dict((await db.execute(select(Agent.id, Agent.name))).all())
        snapshots = {}
        for _, call in calls:
            key = call.get("model_id") or _key("model", call, {})
            if key not in snapshots:
                snapshots[key] = {"name": call.get("model_name"),
                    "provider_name": call.get("provider_name"), "provider_id": call.get("provider_id"),
                    "deleted": bool(call.get("model_id") and call.get("model_id") not in models)}
        return calls, models, snapshots, agents

    async def report(self, filters: SessionFilters):
        calls, models, snapshots, agents = await self.collect(filters)
        summary = _empty()
        for _, call in calls: _add(summary, call)
        groups = {dimension: _project(calls, dimension=dimension) for dimension in DIMENSIONS}
        dates = [day for day, _, _ in filters.days()]
        trend = {}
        series_order = {}
        for dimension in ("agent", "purpose", "model", "user"):
            ranks = sorted(groups[dimension], key=lambda row: (-row["total_tokens"], row["key"]))
            leaders = {row["key"] for row in ranks[:4]}
            series_order[dimension] = [row["key"] for row in ranks[:4]] + (["__other__"] if len(ranks) > 4 else [])
            bins = defaultdict(_empty)
            for event, call in calls:
                key = _key(dimension, call, event)
                _add(bins[(event["date"], key if key in leaders else "__other__")], call)
            trend[dimension] = [{"date": day, "key": key, **value}
                for (day, key), value in sorted(bins.items())]
        return {"range": filters.public_range(), "summary": summary, "dates": dates,
            "trend": trend, "series_order": series_order, "groups": groups, "models": models,
            "model_snapshots": snapshots, "agents": agents,
            "coverage": {"requests": summary["requests"], "missing_requests": summary["missing_requests"]}}

    async def sessions(self, filters: SessionFilters, *, dimension: str, key: str,
                       offset: int = 0, limit: int = 10):
        if dimension not in DIMENSIONS: raise DiagnosticQueryError("无效的明细维度")
        calls, _, _, _ = await self.collect(filters)
        groups = defaultdict(_empty)
        metadata = {}
        for event, call in calls:
            if _key(dimension, call, event) != key: continue
            session_id = event["session_id"]
            _add(groups[session_id], call)
            metadata[session_id] = {"session_id": session_id,
                "user_id": event["user_id"], "agent_id": event["agent_id"],
                "last_activity": max(event["created_at"], metadata.get(session_id, {}).get("last_activity", event["created_at"]))}
        result = sorted(({**metadata[session_id], **value} for session_id, value in groups.items()),
            key=lambda row: (-row["total_tokens"], row["session_id"]))
        total = len(result)
        result = result[offset:offset + limit]
        if result:
            async with self._read() as db:
                titles = {r.session_id: r for r in (await db.execute(select(ChatSession.session_id, ChatSession.title, ChatSession.source_type, ChatSession.source_id)
                    .where(ChatSession.session_id.in_([row["session_id"] for row in result])))).all()}
            for row in result:
                item = titles.get(row["session_id"])
                row.update(title=item.title if item else None, source_type=(item.source_type or "internal") if item else "internal", source_id=item.source_id if item else None)
        return result, total
