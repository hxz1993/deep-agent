"""WeCom application configuration and rebuildable member identity cache."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from deep_agent.db.models import ChannelAccount, WecomApplication, WecomUserProfile
from deep_agent.db.reference_integrity import ReferenceConflict, is_unique_conflict, lock_optional, lock_reference
from deep_agent.db.stores._session import SessionBoundMixin
from deep_agent.schemas.llm_provider import mask_api_key


def userid_key(value: str) -> str:
    return value.strip().casefold()


def wecom_settings(settings: dict | None) -> tuple[bool, str | None]:
    value = (settings or {}).get("wecom")
    if value is None:
        return False, None
    if not isinstance(value, dict):
        raise ValueError("企微设置必须为对象")
    enabled = value.get("user_details_enabled", False)
    app_id = value.get("application_id")
    if not isinstance(enabled, bool):
        raise ValueError("查询用户详情必须为布尔值")
    if app_id is not None and (not isinstance(app_id, str) or not app_id.strip()):
        raise ValueError("企微应用引用无效")
    if enabled and not app_id:
        raise ValueError("开启查询用户详情时必须选择企微应用")
    return enabled, app_id


async def validate_channel_wecom_settings(db, settings: dict | None) -> None:
    _, app_id = wecom_settings(settings)
    if app_id:
        await lock_reference(db, WecomApplication, WecomApplication.id, app_id, "企微应用")


def _stale_profile(now: datetime):
    p = WecomUserProfile
    return or_(p.profile_synced_at.is_(None), p.expires_at.is_(None), p.expires_at <= now)


class WecomStore(SessionBoundMixin):
    async def list_applications(self):
        async with self._read() as db:
            return list((await db.scalars(select(WecomApplication).order_by(WecomApplication.name, WecomApplication.id))).all())

    async def get_application(self, application_id: str):
        async with self._read() as db:
            return await db.get(WecomApplication, application_id)

    async def create_application(self, *, name: str, corp_id: str, wecom_agent_id: str, app_secret: str, settings: dict | None = None):
        if not all(isinstance(v, str) and v.strip() for v in (name, corp_id, wecom_agent_id, app_secret)):
            raise ValueError("应用名称、企业 ID、企微 Agent ID 和 App Secret 必填")
        try:
            async with self._write(exclusive=True) as db:
                row = WecomApplication(id=uuid4().hex, name=name.strip(), corp_id=corp_id.strip(), wecom_agent_id=wecom_agent_id.strip(), app_secret=app_secret.strip(), settings=settings or {}, revision=1)
                db.add(row)
                await db.flush()
                await db.refresh(row)
                return row
        except IntegrityError as exc:
            if not is_unique_conflict(exc):
                raise
            raise ValueError("同一企业应用已存在") from exc

    async def update_application(self, application_id: str, **changes):
        async with self._write(exclusive=True) as db:
            row = await lock_optional(db, WecomApplication, WecomApplication.id, application_id)
            if row is None:
                return None
            for key in ("corp_id", "wecom_agent_id"):
                if key in changes and changes[key] != getattr(row, key):
                    raise ValueError("企业 ID 和企微 Agent ID 创建后不可修改")
            clear_secret = changes.get("clear_app_secret") is True
            if clear_secret and changes.get("app_secret") not in (None, ""):
                raise ValueError("清空凭据与设置新凭据不能同时提交")
            changed = False
            if clear_secret and row.app_secret:
                row.app_secret = ""
                changed = True
            for key in ("name", "settings", "app_secret"):
                if key not in changes:
                    continue
                value = changes[key]
                if key == "app_secret" and (not value or value == mask_api_key(row.app_secret)):
                    continue
                if key in ("name", "app_secret"):
                    if not isinstance(value, str) or not value.strip():
                        raise ValueError("应用名称及 App Secret 不能为空")
                    value = value.strip()
                if value != getattr(row, key):
                    setattr(row, key, value)
                    changed = True
            if changed:
                row.revision += 1
                row.updated_at = datetime.now(timezone.utc)
            await db.flush()
            await db.refresh(row)
            return row

    @staticmethod
    async def _references(db, application_id: str):
        rows = (await db.execute(select(ChannelAccount.id, ChannelAccount.settings))).all()
        return [row.id for row in rows if wecom_settings(row.settings)[1] == application_id
                or (row.settings or {}).get("wecom_app", {}).get("application_id") == application_id]

    async def application_channel_count(self, application_id: str) -> int:
        async with self._read() as db:
            return len(await self._references(db, application_id))

    async def delete_application(self, application_id: str) -> bool:
        async with self._write(exclusive=True) as db:
            row = await lock_optional(db, WecomApplication, WecomApplication.id, application_id)
            if row is None:
                return False
            if await self._references(db, application_id):
                raise ReferenceConflict("企微应用仍被渠道引用，请先解除关联")
            await db.execute(delete(WecomUserProfile).where(WecomUserProfile.wecom_application_id == application_id))
            await db.delete(row)
            return True

    async def cache_stats(self, application_id: str):
        p = WecomUserProfile
        now = datetime.now(timezone.utc)
        async with self._read() as db:
            total, created, synced = (await db.execute(select(func.count(), func.min(p.created_at), func.min(p.profile_synced_at)).where(p.wecom_application_id == application_id))).one()
            pending = await db.scalar(select(func.count()).select_from(p).where(p.wecom_application_id == application_id, _stale_profile(now)))
            return dict(total=total, pending=pending, earliest_created_at=created, earliest_synced_at=synced)

    async def list_cache_members(
        self, application_id: str, *, q: str = "", status: str = "all",
        synced: str = "all", sort: str = "desc", limit: int = 10, offset: int = 0,
    ):
        """Read a bounded page of known members without refreshing their profiles."""
        p = WecomUserProfile
        now = datetime.now(timezone.utc)
        stale = _stale_profile(now)
        predicates = [p.wecom_application_id == application_id]
        if q.strip():
            # Treat %, _ and the escape character as literal search text on all DBs.
            pattern = "%" + q.strip().replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
            predicates.append(or_(*(column.ilike(pattern, escape="!") for column in (p.name, p.userid, p.open_userid))))
        if status == "valid":
            predicates.append(~stale)
        elif status == "stale":
            predicates.append(stale)
        if synced == "day":
            predicates.append(p.profile_synced_at >= now - timedelta(days=1))
        elif synced == "week":
            predicates.append(p.profile_synced_at >= now - timedelta(days=7))
        elif synced == "older":
            predicates.append(p.profile_synced_at < now - timedelta(days=7))
        elif synced == "never":
            predicates.append(p.profile_synced_at.is_(None))
        columns = (
            p.id, p.name, p.userid, p.open_userid, p.profile_synced_at, p.expires_at,
            p.created_at, p.last_error_stage, p.last_error_code,
            case((stale, "stale"), else_="valid").label("status"),
        )
        # CASE keeps null timestamps last on SQLite, PostgreSQL and MySQL alike.
        order = (
            case((p.profile_synced_at.is_(None), 1), else_=0),
            p.profile_synced_at.asc() if sort == "asc" else p.profile_synced_at.desc(),
            p.id.asc(),
        )
        async with self._read() as db:
            total = await db.scalar(select(func.count()).select_from(p).where(*predicates))
            rows = (await db.execute(select(*columns).where(*predicates).order_by(*order).limit(limit).offset(offset))).mappings().all()
            return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}

    async def invalidate_cache(self, application_id: str):
        async with self._write() as db:
            row = await lock_optional(db, WecomApplication, WecomApplication.id, application_id)
            if row is None:
                return None
            row.revision += 1
            now = datetime.now(timezone.utc)
            row.updated_at = now
            await db.execute(update(WecomUserProfile).where(WecomUserProfile.wecom_application_id == application_id).values(expires_at=now, next_retry_at=None, last_error_stage=None, last_error_code=None, updated_at=now))
            await db.flush()
            await db.refresh(row)
            return row

    async def find_profile(self, application_id: str, raw_id: str):
        async with self._read() as db:
            # Prefer exact opaque identity when a plaintext account happens to look similar.
            row = await db.scalar(select(WecomUserProfile).where(WecomUserProfile.wecom_application_id == application_id, WecomUserProfile.open_userid == raw_id))
            if row is not None:
                return row
            return await db.scalar(select(WecomUserProfile).where(WecomUserProfile.wecom_application_id == application_id, WecomUserProfile.userid_key == userid_key(raw_id)))

    async def list_profiles(self, application_id: str, *, offset: int = 0, limit: int = 100, created_before: datetime | None = None):
        stmt = select(WecomUserProfile).where(WecomUserProfile.wecom_application_id == application_id)
        if created_before is not None:
            stmt = stmt.where(WecomUserProfile.created_at <= created_before)
        async with self._read() as db:
            return list((await db.scalars(stmt.order_by(WecomUserProfile.id).offset(offset).limit(min(limit, 500)))).all())

    async def snapshot_profile_ids(self, application_id: str) -> list[str]:
        async with self._read() as db:
            return list((await db.scalars(select(WecomUserProfile.id).where(WecomUserProfile.wecom_application_id == application_id).order_by(WecomUserProfile.id))).all())

    async def get_profiles_by_ids(self, application_id: str, profile_ids: list[str]):
        if not profile_ids:
            return []
        async with self._read() as db:
            return list((await db.scalars(select(WecomUserProfile).where(WecomUserProfile.wecom_application_id == application_id, WecomUserProfile.id.in_(profile_ids)).order_by(WecomUserProfile.id))).all())

    @staticmethod
    async def _current(db, application_id, revision):
        app = await lock_optional(db, WecomApplication, WecomApplication.id, application_id)
        return app is not None and app.revision == revision

    async def upsert_identity(self, application_id: str, revision: int, userid: str, open_userid: str | None = None):
        key = userid_key(userid)
        if not key:
            raise ValueError("成员 User ID 不能为空")
        async with self._write() as db:
            if not await self._current(db, application_id, revision):
                return None
            base = select(WecomUserProfile).where(WecomUserProfile.wecom_application_id == application_id)
            row = await db.scalar(base.where(WecomUserProfile.userid_key == key).with_for_update())
            if open_userid:
                opaque = await db.scalar(base.where(WecomUserProfile.open_userid == open_userid).with_for_update())
                if opaque is not None and opaque.userid_key != key:
                    raise ValueError("Open User ID 对应的成员身份冲突")
                if row is not None and row.open_userid not in (None, open_userid):
                    raise ValueError("成员已关联不同的 Open User ID")
            if row is None:
                now = datetime.now(timezone.utc)
                row = WecomUserProfile(id=uuid4().hex, wecom_application_id=application_id, userid=userid, userid_key=key, open_userid=open_userid, created_at=now, updated_at=now)
                db.add(row)
            elif open_userid:
                row.open_userid = open_userid
            await db.flush()
            await db.refresh(row)
            return row

    async def save_profile(self, application_id: str, revision: int, userid: str, name: str):
        if not name:
            raise ValueError("成员姓名缺失，不能标记资料已更新")
        return await self._update_profile(application_id, revision, userid, name=name)

    async def record_failure(self, application_id: str, revision: int, userid: str, stage: str, code: str, retry_seconds: int = 300):
        return await self._update_profile(application_id, revision, userid, stage=stage, code=code, retry_seconds=retry_seconds)

    async def _update_profile(self, application_id, revision, userid, *, name=None, stage=None, code=None, retry_seconds=300):
        async with self._write() as db:
            if not await self._current(db, application_id, revision):
                return None
            row = await db.scalar(select(WecomUserProfile).where(WecomUserProfile.wecom_application_id == application_id, WecomUserProfile.userid_key == userid_key(userid)).with_for_update())
            if row is None:
                return None
            now = datetime.now(timezone.utc)
            row.last_attempt_at = row.updated_at = now
            if name is not None:
                row.name = name
                row.profile_synced_at = now
                row.expires_at = now + timedelta(days=7)
                row.next_retry_at = row.last_error_stage = row.last_error_code = None
            else:
                row.last_error_stage = str(stage)[:64]
                row.last_error_code = str(code)[:64]
                row.next_retry_at = now + timedelta(seconds=retry_seconds)
            await db.flush()
            await db.refresh(row)
            return row
