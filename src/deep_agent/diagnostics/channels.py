"""Channel-account diagnostics over the durable dispatch ledger.

Counts stay in SQL. Only the current page's latest user events are projected;
no history or JSON payload is scanned for identity or aggregation. Commands and
pre-dispatch failures have no persisted user event and do not count as questions.
"""

from __future__ import annotations

from types import SimpleNamespace

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
from deep_agent.services.channel_profiles import user_details_enabled


def _deepfos_application_summary(settings):
    config = settings.get("deepfos") if isinstance(settings, dict) else None
    config = config if isinstance(config, dict) else {}
    return {
        "enabled": config.get("enabled") is True,
        "app_id": config.get("app_id") if isinstance(config.get("app_id"), str) else None,
        "api_key_set": bool(config.get("api_key")),
    }


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
    def __init__(self, session_factory, *, profiles=None):
        super().__init__(session_factory)
        self._profiles = profiles

    _details_enabled = staticmethod(user_details_enabled)

    async def _resolve_profiles(self, account, pairs, *, budget=3):
        raw_ids = sorted({raw for _, raw in pairs if raw})
        enabled = self._details_enabled(account)
        result = {"items": {}, "profiles_complete": not enabled or not raw_ids,
                  "unresolved_count": len(raw_ids) if enabled else 0,
                  "user_details_enabled": enabled}
        if enabled and raw_ids and self._profiles is not None:
            result = await self._profiles.resolve_many(account, raw_ids, budget=budget)
        profiles = result.get("items", {})
        names, aliases = {}, {}
        for canonical, raw in sorted(pairs, key=lambda pair: (pair[0] or "", pair[1] or "")):
            profile = profiles.get(raw) or {}
            userid = profile.get("userid")
            if canonical and isinstance(userid, str) and userid:
                aliases.setdefault(canonical, set()).add(userid)
            name = profile.get("name")
            if canonical and isinstance(name, str) and name.strip():
                names.setdefault(canonical, name.strip())
        flags = {key: result[key] for key in ("profiles_complete", "unresolved_count", "user_details_enabled")}
        return names, flags, aliases

    @staticmethod
    async def _identity_pairs(db, base, conditions=()):
        return (await db.execute(select(base.c.user_id, base.c.sender_id)
                                .where(*conditions).distinct())).all()

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
                            ChannelAccount.bot_id,
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
                        "bot_id": r["bot_id"],
                        "transport": r["transport"],
                        "enabled": r["enabled"],
                        "agent": {"id": r["agent_id"], "name": r["agent_name"]},
                        "user_id": r["session_user_id"],
                        "user_name": None,
                        "deepfos_application": _deepfos_application_summary(r["settings"]),
                        "user_details_enabled": self._details_enabled(SimpleNamespace(**dict(r))),
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
                    ChannelAccount.enabled,
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
        stmt, user_id = self._canonical_identity(stmt, account, r.sender_id)
        return stmt.add_columns(_OpaqueIdentity(user_id).label("user_id")).subquery("channel_questions")

    @staticmethod
    def _canonical_identity(stmt, account, sender_id):
        # Persisted official WeCom ID aliases alone determine grouping. Current
        # external names/mappings never change this query's counts or ordering.
        app_id = wecom_application_id(account)
        if app_id:
            opaque, plain = aliased(WecomUserProfile), aliased(WecomUserProfile)
            stmt = stmt.outerjoin(
                opaque,
                and_(
                    opaque.wecom_application_id == app_id,
                    opaque.open_userid == sender_id,
                ),
            )
            stmt = stmt.outerjoin(
                plain,
                and_(
                    opaque.id.is_(None),
                    plain.wecom_application_id == app_id,
                    plain.userid_key == func.lower(sender_id),
                ),
            )
            return stmt, func.coalesce(opaque.userid, plain.userid, sender_id)
        return stmt, sender_id

    async def overview(self, account_id, f):
        async with self._read() as db:
            account = await self._account(db, account_id)
            base = self._base(account, f)
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
            pairs = await self._identity_pairs(db, base, (base.c.user_id.in_([r["user_id"] for r in top]),)) if top else []
        names, flags, _ = await self._resolve_profiles(account, pairs)
        return {
            **flags,
            "range": f.public_range(),
            "summary": {
                "active_users": users,
                "session_count": sessions,
                "question_count": questions,
            },
            "active_users": [dict(r, name=names.get(r["user_id"])) for r in top],
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
            account = await self._account(db, account_id)
            base = self._base(account, f)
            if kind == "group" and sender_id:
                base = select(base).where(base.c.user_id == sender_id).subquery("sender_questions")
            identity, conditions = self._identity(base, kind)
            if options_only and kind == "user" and include_group_senders:
                conditions = [base.c.user_id.is_not(None), base.c.user_id != ""]
            if peer_id is not None:
                conditions.append(identity == peer_id)
            name = literal(None)
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
            grouped = stmt.subquery()
            searching_users = bool(f.query and kind == "user")
            if searching_users:
                pairs = await self._identity_pairs(db, base, conditions)
                rows = (await db.execute(select(grouped).order_by(
                    grouped.c.last_activity.desc(), grouped.c.peer_id))).mappings().all()
                total = len(rows)
            else:
                if f.query:
                    pattern = "%" + f.query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                    grouped = select(grouped).where(grouped.c.peer_id.ilike(pattern, escape="\\")).subquery()
                total = await db.scalar(select(func.count()).select_from(grouped))
                rows = (await db.execute(select(grouped)
                    .order_by(grouped.c.last_activity.desc(), grouped.c.peer_id)
                    .limit(f.limit).offset(f.offset))).mappings().all()
                ids = [row["peer_id"] for row in rows]
                pairs = await self._identity_pairs(db, base, (*conditions, identity.in_(ids))) if kind == "user" and ids else []

        # Never hold a pool connection or the SQLite session lock while external
        # profile resolution performs I/O or persists its own profile cache.
        names = {}
        flags = {"profiles_complete": True, "unresolved_count": 0,
                 "user_details_enabled": self._details_enabled(account)}
        if searching_users:
            names, flags, aliases = await self._resolve_profiles(account, pairs, budget=10)
            needle = f.query.casefold()
            matched = {canonical for canonical, raw in pairs if any(
                needle in str(value).casefold()
                for value in (canonical, raw, names.get(canonical), *aliases.get(canonical, ())) if value
            )}
            rows = [row for row in rows if row["peer_id"] in matched]
            total = len(rows)
            rows = rows[f.offset:f.offset + f.limit]
        items = [dict(row, kind=kind) for row in rows]
        ids = [row["peer_id"] for row in items]
        if kind == "user" and f.query and ids:
            # Current profile refresh cannot regroup the page. Freeze only
            # its original ID aliases, keeping SQL parameter counts bounded
            # by the displayed users rather than the full search population.
            mappings = {raw: canonical for canonical, raw in pairs if raw and canonical in ids}
            if mappings:
                base = select(
                    *[base.c[key] for key in base.c.keys() if key != "user_id"],
                    case(mappings, value=base.c.sender_id, else_=base.c.user_id).label("user_id"),
                ).subquery("snapshot_questions")
                identity, conditions = self._identity(base, kind)
                if options_only and include_group_senders:
                    conditions = [base.c.user_id.is_not(None), base.c.user_id != ""]
                if peer_id is not None:
                    conditions.append(identity == peer_id)
        if ids and not options_only:
            async with self._read() as db:
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
                session_pairs = (
                    await db.execute(
                        select(candidates.c.peer_id, candidates.c.session_id).where(
                            ~newer_exists
                        )
                    )
                ).all()
                details = await self._session_items(db, base, [sid for _, sid in session_pairs])
                # READ COMMITTED can observe a concurrent deletion between reads.
                by_peer = {pid: details[sid] for pid, sid in session_pairs if sid in details}
                for item in items:
                    item["last_activity"] = utc_rfc3339(item["last_activity"])
                    item["latest_session"] = by_peer.get(item["peer_id"])
        if kind == "user":
            if not searching_users:
                names, flags, _ = await self._resolve_profiles(account, pairs)
            for item in items:
                item["name"] = names.get(item["peer_id"])
        if options_only:
            return {**flags, "items": [{"peer_id": row["peer_id"], "name": row["name"], "kind": kind} for row in items],
                    "total": total, "limit": f.limit, "offset": f.offset}
        return {**flags, "items": items, "total": total,
                "limit": f.limit, "offset": f.offset, "range": f.public_range()}

    async def sessions(self, account_id, f, kind, peer_id, sender_id=None):
        async with self._read() as db:
            account = await self._account(db, account_id)
            base = self._base(account, f)
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
