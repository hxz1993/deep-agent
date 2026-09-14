"""Channel-account diagnostics over the durable dispatch ledger.

Counts stay in SQL. Only the current page's latest user events are projected;
no history or JSON payload is scanned for identity or aggregation. Commands and
pre-dispatch failures have no persisted user event and do not count as questions.
"""

from __future__ import annotations

from sqlalchemy import String, and_, case, exists, func, literal, or_, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import aliased
from sqlalchemy.sql.functions import FunctionElement

from deep_agent.channels.metadata import wecom_application_id
from deep_agent.db.models import (
    Agent,
    ChannelAccount,
    ChannelMessageRun,
    ChannelSessionBinding,
    ChatSession,
    ChatSessionEvent,
    WecomApplication,
    WecomUserProfile,
)
from deep_agent.db.stores._session import SessionBoundMixin
from deep_agent.diagnostics.sessions import (
    DiagnosticQueryError,
    question_summary,
)
from deep_agent.time import utc_rfc3339


class _OpaqueIdentity(FunctionElement):
    """Keep opaque IDs case-sensitive even with a MySQL CI table collation."""

    type = String()
    inherit_cache = True


@compiles(_OpaqueIdentity)
def _identity_sql(element, compiler, **kw):
    return compiler.process(next(iter(element.clauses)), **kw)


@compiles(_OpaqueIdentity, "mysql")
def _identity_mysql(element, compiler, **kw):
    value = compiler.process(next(iter(element.clauses)), **kw)
    return f"(CONVERT({value} USING utf8mb4) COLLATE utf8mb4_bin)"


class DiagnosticChannelQueries(SessionBoundMixin):
    async def accounts(self):
        async with self._read() as db:
            # Explicit projections deliberately never load channel/app secrets.
            rows = (
                (
                    await db.execute(
                        select(
                            ChannelAccount.id,
                            ChannelAccount.name,
                            ChannelAccount.channel_type,
                            ChannelAccount.transport,
                            ChannelAccount.enabled,
                            ChannelAccount.agent_id,
                            ChannelAccount.session_user_id,
                            ChannelAccount.settings,
                            Agent.name.label("agent_name"),
                        )
                        .outerjoin(Agent, Agent.id == ChannelAccount.agent_id)
                        .order_by(ChannelAccount.created_at, ChannelAccount.id)
                    )
                )
                .mappings()
                .all()
            )
            # A null wecom value is valid configuration (lookup disabled).
            # Normalize it once before projecting the public account facts.
            settings = {
                r["id"]: (r["settings"].get("wecom") or {})
                if isinstance(r["settings"], dict)
                and isinstance(r["settings"].get("wecom"), (dict, type(None)))
                else {}
                for r in rows
            }
            ids = {
                value["application_id"] for value in settings.values()
                if isinstance(value.get("application_id"), str) and value["application_id"]
            }
            applications = (
                {
                    r.id: dict(r._mapping)
                    for r in (
                        await db.execute(
                            select(
                                WecomApplication.id,
                                WecomApplication.name,
                                WecomApplication.corp_id,
                                WecomApplication.wecom_agent_id.label("agent_id"),
                            ).where(WecomApplication.id.in_(ids))
                        )
                    ).all()
                }
                if ids
                else {}
            )
            return {
                "items": [
                    {
                        "id": r["id"],
                        "name": r["name"],
                        "channel_type": r["channel_type"],
                        "transport": r["transport"],
                        "enabled": r["enabled"],
                        "agent": {"id": r["agent_id"], "name": r["agent_name"]},
                        "user_id": r["session_user_id"],
                        "user_name": None,
                        "user_details_enabled": settings[r["id"]].get("user_details_enabled") is True,
                        "application": applications.get(settings[r["id"]].get("application_id")),
                    }
                    for r in rows
                ]
            }

    async def _account(self, db, account_id):
        row = (
            await db.execute(
                select(
                    ChannelAccount.id,
                    ChannelAccount.channel_type,
                    ChannelAccount.settings,
                ).where(ChannelAccount.id == account_id)
            )
        ).first()
        if row is None:
            raise DiagnosticQueryError("渠道账户不存在", 404)
        return row

    def _base(self, account, f):
        r, b, e = ChannelMessageRun, ChannelSessionBinding, ChatSessionEvent
        # Account index bounds the ledger; existing session/run/sequence index
        # supports the existence probe and avoids joining potentially split events.
        stmt = select(
            r.id.label("message_id"),
            r.session_id,
            r.agent_run_id,
            r.sender_id,
            _OpaqueIdentity(b.peer_id).label("group_id"),
            b.peer_type,
            r.created_at.label("activity"),
        ).join(b, and_(b.id == r.binding_id, b.account_id == r.account_id))
        stmt = stmt.join(ChatSession, ChatSession.session_id == r.session_id).where(
            r.account_id == account.id,
            r.created_at >= f.start,
            r.created_at < f.end,
            r.agent_run_id.is_not(None),
            exists(
                select(1).where(
                    e.session_id == r.session_id,
                    e.run_id == r.agent_run_id,
                    e.event_type == "user/message",
                )
            ),
        )
        app_id = wecom_application_id(account)
        if app_id:
            opaque, plain = aliased(WecomUserProfile), aliased(WecomUserProfile)
            stmt = stmt.outerjoin(
                opaque,
                and_(
                    opaque.wecom_application_id == app_id,
                    opaque.open_userid == r.sender_id,
                ),
            )
            stmt = stmt.outerjoin(
                plain,
                and_(
                    opaque.id.is_(None),
                    plain.wecom_application_id == app_id,
                    plain.userid_key == func.lower(r.sender_id),
                ),
            )
            user_id = func.coalesce(opaque.userid, plain.userid, r.sender_id)
            name = func.coalesce(opaque.name, plain.name)
        else:
            user_id, name = r.sender_id, literal(None)
        return stmt.add_columns(
            _OpaqueIdentity(user_id).label("user_id"), name.label("name")
        ).subquery("channel_questions")

    async def overview(self, account_id, f):
        async with self._read() as db:
            base = self._base(await self._account(db, account_id), f)
            users, sessions, questions = (
                await db.execute(
                    select(
                        func.count(func.distinct(func.nullif(base.c.user_id, ""))),
                        func.count(func.distinct(base.c.session_id)),
                        func.count(),
                    ).select_from(base)
                )
            ).one()
            top = (
                (
                    await db.execute(
                        select(
                            base.c.user_id,
                            func.max(base.c.name).label("name"),
                            func.count().label("question_count"),
                        )
                        .where(base.c.user_id.is_not(None), base.c.user_id != "")
                        .group_by(base.c.user_id)
                        .order_by(func.count().desc(), base.c.user_id)
                        .limit(5)
                    )
                )
                .mappings()
                .all()
            )
            return {
                "range": f.public_range(),
                "summary": {
                    "active_users": users,
                    "session_count": sessions,
                    "question_count": questions,
                },
                "active_users": [dict(r) for r in top],
            }

    @staticmethod
    def _identity(base, kind):
        if kind == "user":
            return base.c.user_id, [
                base.c.peer_type.in_(("single", "userid")),
                base.c.user_id.is_not(None),
                base.c.user_id != "",
            ]
        return base.c.group_id, [base.c.peer_type.in_(("group", "chatid"))]

    async def peers(self, account_id, f, kind="user", *, options_only=False, peer_id=None, sender_id=None, include_group_senders=False):
        async with self._read() as db:
            base = self._base(await self._account(db, account_id), f)
            if kind == "group" and sender_id:
                base = select(base).where(base.c.user_id == sender_id).subquery("sender_questions")
            identity, conditions = self._identity(base, kind)
            if options_only and kind == "user" and include_group_senders:
                conditions = [base.c.user_id.is_not(None), base.c.user_id != ""]
            if peer_id is not None:
                conditions.append(identity == peer_id)
            name = func.max(base.c.name) if kind == "user" else literal(None)
            stmt = select(
                identity.label("peer_id"),
                name.label("name"),
                func.count(func.distinct(base.c.session_id)).label("session_count"),
                func.count().label("question_count"),
                func.max(base.c.activity).label("last_activity"),
            )
            if options_only:
                stmt = select(identity.label("peer_id"), name.label("name"), func.max(base.c.activity).label("last_activity"))
            stmt = stmt.where(*conditions).group_by(identity)
            pattern = None
            if f.query:
                pattern = (
                    "%"
                    + f.query.replace("\\", "\\\\")
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                    + "%"
                )
                if kind == "user":
                    stmt = stmt.add_columns(func.max(case((base.c.sender_id.ilike(pattern, escape="\\"), 1), else_=0)).label("alias_match"))
            grouped = stmt.subquery()
            if pattern is not None:
                search = grouped.c.peer_id.ilike(pattern, escape="\\")
                if kind == "user":
                    search = or_(search, grouped.c.name.ilike(pattern, escape="\\"), grouped.c.alias_match == 1)
                # Filter aggregate output columns outside GROUP BY. MySQL 5.7
                # cannot resolve the original derived identity in HAVING.
                grouped = select(grouped).where(search).subquery()
            total = await db.scalar(select(func.count()).select_from(grouped))
            rows = (
                (
                    await db.execute(
                        select(grouped)
                        .order_by(grouped.c.last_activity.desc(), grouped.c.peer_id)
                        .limit(f.limit)
                        .offset(f.offset)
                    )
                )
                .mappings()
                .all()
            )
            items = [dict(r, kind=kind) for r in rows]
            for item in items:
                item.pop("alias_match", None)
            ids = [r["peer_id"] for r in items]
            if options_only:
                return {"items": [{"peer_id": r["peer_id"], "name": r["name"], "kind": kind} for r in items], "total": total, "limit": f.limit, "offset": f.offset}
            if ids:
                session_activity = (
                    select(
                        base.c.session_id, func.max(base.c.activity).label("activity")
                    )
                    .group_by(base.c.session_id)
                    .subquery()
                )
                participation = (
                    select(identity.label("peer_id"), base.c.session_id)
                    .where(*conditions, identity.in_(ids))
                    .distinct()
                    .subquery()
                )
                candidates = (
                    select(
                        participation.c.peer_id,
                        participation.c.session_id,
                        session_activity.c.activity,
                    )
                    .join(
                        session_activity,
                        session_activity.c.session_id == participation.c.session_id,
                    )
                    .subquery()
                )
                newer = candidates.alias("newer")
                # MySQL 5.7 has neither CTEs nor window functions. Anti-existence
                # chooses one latest session per paged identity, with a stable tie.
                newer_exists = exists(
                    select(1)
                    .select_from(newer)
                    .where(
                        newer.c.peer_id == candidates.c.peer_id,
                        or_(
                            newer.c.activity > candidates.c.activity,
                            and_(
                                newer.c.activity == candidates.c.activity,
                                newer.c.session_id > candidates.c.session_id,
                            ),
                        ),
                    )
                )
                pairs = (
                    await db.execute(
                        select(candidates.c.peer_id, candidates.c.session_id).where(
                            ~newer_exists
                        )
                    )
                ).all()
                details = await self._session_items(db, base, [sid for _, sid in pairs])
                # READ COMMITTED can observe a concurrent deletion between reads.
                by_peer = {pid: details[sid] for pid, sid in pairs if sid in details}
                for item in items:
                    item["last_activity"] = utc_rfc3339(item["last_activity"])
                    item["latest_session"] = by_peer.get(item["peer_id"])
            return {
                "items": items,
                "total": total,
                "limit": f.limit,
                "offset": f.offset,
                "range": f.public_range(),
            }

    async def sessions(self, account_id, f, kind, peer_id, sender_id=None):
        async with self._read() as db:
            base = self._base(await self._account(db, account_id), f)
            if kind == "group" and sender_id:
                base = select(base).where(
                    base.c.user_id == sender_id,
                    base.c.peer_type.in_(("group", "chatid")),
                    base.c.group_id == peer_id,
                ).subquery("matching_questions")
                return await self._matching_questions(db, base, f)
            identity, conditions = self._identity(base, kind)
            related = (
                select(base.c.session_id)
                .where(*conditions, identity == peer_id)
                .distinct()
            )
            grouped = (
                select(
                    base.c.session_id, func.max(base.c.activity).label("last_activity")
                )
                .where(base.c.session_id.in_(related))
                .group_by(base.c.session_id)
                .subquery()
            )
            total = await db.scalar(select(func.count()).select_from(grouped))
            rows = (
                await db.execute(
                    select(grouped)
                    .order_by(
                        grouped.c.last_activity.desc(), grouped.c.session_id.desc()
                    )
                    .limit(f.limit)
                    .offset(f.offset)
                )
            ).all()
            details = await self._session_items(db, base, [r.session_id for r in rows])
            items = [
                dict(details[r.session_id], is_latest=f.offset == 0 and i == 0)
                for i, r in enumerate(rows) if r.session_id in details
            ]
            return {
                "items": items,
                "total": total,
                "limit": f.limit,
                "offset": f.offset,
                "range": f.public_range(),
            }

    async def _matching_questions(self, db, base, f):
        # Page ledger identities first. Only this page's user events are loaded.
        total = await db.scalar(select(func.count()).select_from(base))
        rows = (await db.execute(
            select(base.c.message_id, base.c.session_id, base.c.agent_run_id, base.c.activity)
            .order_by(base.c.activity.desc(), base.c.message_id.desc())
            .limit(f.limit).offset(f.offset)
        )).all()
        items = []
        if rows:
            event = ChatSessionEvent
            latest = (select(base.c.message_id, event.session_id, func.max(event.sequence).label("sequence"))
                .join(event, and_(event.session_id == base.c.session_id, event.run_id == base.c.agent_run_id))
                .where(base.c.message_id.in_([r.message_id for r in rows]), event.event_type == "user/message")
                .group_by(base.c.message_id, event.session_id).subquery())
            events = (await db.execute(select(latest.c.message_id, event).join(event,
                and_(event.session_id == latest.c.session_id, event.sequence == latest.c.sequence)))).all()
            summaries = {mid: question_summary(e) for mid, e in events}
            items = [{"session_id": r.session_id, "message_id": r.message_id,
                "question_count": 1, "latest_question": summaries.get(r.message_id),
                "last_activity": utc_rfc3339(r.activity), "is_latest": f.offset == 0 and i == 0,
                "is_question": True} for i, r in enumerate(rows)]
        return {"items": items, "total": total, "limit": f.limit, "offset": f.offset,
            "mode": "questions", "range": f.public_range()}

    async def _session_items(self, db, base, ids):
        if not ids:
            return {}
        e = ChatSessionEvent
        rows = (
            (
                await db.execute(
                    select(
                        base.c.session_id,
                        func.count().label("question_count"),
                        func.max(base.c.activity).label("last_activity"),
                    )
                    .where(base.c.session_id.in_(ids))
                    .group_by(base.c.session_id)
                )
            )
            .mappings()
            .all()
        )
        # Fetch just one latest event per displayed session, never full histories.
        latest = (
            select(e.session_id, func.max(e.sequence).label("sequence"))
            .join(
                base,
                and_(
                    base.c.session_id == e.session_id, base.c.agent_run_id == e.run_id
                ),
            )
            .where(e.session_id.in_(ids), e.event_type == "user/message")
            .group_by(e.session_id)
            .subquery()
        )
        events = (
            await db.scalars(
                select(e).join(
                    latest,
                    and_(
                        latest.c.session_id == e.session_id,
                        latest.c.sequence == e.sequence,
                    ),
                )
            )
        ).all()
        summaries = {e.session_id: question_summary(e) for e in events}
        return {
            r["session_id"]: {
                "session_id": r["session_id"],
                "question_count": r["question_count"],
                "last_activity": utc_rfc3339(r["last_activity"]),
                "latest_question": summaries.get(r["session_id"]),
            }
            for r in rows
        }
