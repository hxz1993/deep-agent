"""API-owned, bounded identity resolution and application cache maintenance."""
from __future__ import annotations

import asyncio
import uuid
from collections import OrderedDict
from datetime import datetime, timezone

from deep_agent.agent.observability import log_event
from deep_agent.services.wecom_client import WecomClient, WecomError
from deep_agent.channels.metadata import wecom_application_id


def application_for_account(account):
    return wecom_application_id(account)


class WecomProfileService:
    def __init__(self, store, channel_store, *, client=None):
        self.store = store
        self.channels = channel_store
        self.client = client or WecomClient()
        self._queries: dict[tuple, asyncio.Task] = {}
        self._jobs: OrderedDict[str, dict] = OrderedDict()
        self._bulk_tasks: dict[str, asyncio.Task] = {}
        self._refresh_lock = asyncio.Lock()
        self.wait_seconds = 5
        self.task_seconds = 20
        self._closed = False
        self._close_task: asyncio.Task | None = None

    async def close(self):
        self._closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    async def _close(self):
        tasks = set(self._queries.values()) | set(self._bulk_tasks.values())
        for task in tasks:
            task.cancel()
        await self.client.close()
        await asyncio.gather(*tasks, return_exceptions=True)

    def invalidate_runtime(self, app_id):
        self.client.invalidate(app_id)

    def _query(self, app, raw_id, profile=None, *, plaintext=False):
        if self._closed:
            raise WecomError("resolve", "service_closed")
        # Once an identity is known, both incoming ID forms share the same job.
        identity = ("userid", profile.userid_key) if profile else ("plaintext" if plaintext else "raw", raw_id)
        key = (app.id, app.revision, *identity)
        task = self._queries.get(key)
        if task is None:
            task = asyncio.create_task(self._bounded_query(app, raw_id, profile, plaintext=plaintext))
            self._queries[key] = task
            def done(t):
                # A raw-ID task acquires a userid alias after conversion. Drop
                # both keys when it finishes so future expiry refreshes can run.
                for alias, query in list(self._queries.items()):
                    if query is t:
                        self._queries.pop(alias, None)
                if not t.cancelled():
                    t.exception()
            task.add_done_callback(done)
        return task

    def _identity_query(self, app, profile):
        """Join an in-flight query once a formerly opaque ID is confirmed."""
        key = (app.id, app.revision, "userid", profile.userid_key)
        current = asyncio.current_task()
        existing = self._queries.get(key)
        if existing is not None and existing is not current:
            return existing
        self._queries[key] = current
        return None

    async def _bounded_query(self, app, raw_id, profile, *, plaintext=False):
        userid = profile.userid if profile else None
        try:
            async with asyncio.timeout(self.task_seconds):
                if userid is None:
                    # Application callbacks contain the authenticated plain UserID.
                    userid, explicitly_invalid = (None, True) if plaintext else await self.client.convert(app, raw_id)
                    if userid:
                        profile = await self.store.upsert_identity(app.id, app.revision, userid, open_userid=raw_id)
                    elif explicitly_invalid:
                        userid, name = await self.client.member(app, raw_id)
                        profile = await self.store.upsert_identity(app.id, app.revision, userid)
                        if profile is None:
                            return None
                        existing = self._identity_query(app, profile)
                        if existing is not None:
                            return await asyncio.shield(existing)
                        if not name:
                            raise WecomError("profile", "name_unavailable")
                        return await self.store.save_profile(app.id, app.revision, userid, name)
                    else:
                        raise WecomError("convert", "identity_unresolved")
                    if profile is None:
                        return None
                existing = self._identity_query(app, profile)
                if existing is not None:
                    return await asyncio.shield(existing)
                userid, name = await self.client.member(app, userid)
                if not name:
                    raise WecomError("profile", "name_unavailable")
                return await self.store.save_profile(app.id, app.revision, userid, name)
        except (WecomError, TimeoutError, ValueError) as exc:
            stage = exc.stage if isinstance(exc, WecomError) else "resolve"
            code = exc.code if isinstance(exc, WecomError) else ("timeout" if isinstance(exc, TimeoutError) else "mapping_conflict")
            log_event("wecom_profile_failed", wecom_application_id=app.id, phase=stage, error_code=code)
            if userid and profile is not None:
                await self.store.record_failure(app.id, app.revision, userid, stage, code)
            return None

    async def _account_matches(self, account_id, app_id, revision):
        if self._closed:
            return False
        account = await self.channels.get_account(account_id)
        app = await self.store.get_application(app_id)
        return bool(not self._closed and account and account.enabled and application_for_account(account) == app_id
                    and app and app.revision == revision)

    async def resolve(self, account, raw_id, *, await_refresh=False):
        app_id = application_for_account(account)
        empty = {"status": "disabled", "userid": None, "name": None, "application_id": None}
        if self._closed or not app_id:
            return empty
        app = await self.store.get_application(app_id)
        result = {**empty, "status": "unresolved", "application_id": app_id,
                  "tenant_id": app.corp_id if app else None, "application_revision": app.revision if app else None}
        if app is None:
            return result
        profile = await self.store.find_profile(app.id, raw_id)
        now = datetime.now(timezone.utc)
        fresh = profile and profile.expires_at and profile.expires_at > now
        retry_wait = profile and profile.next_retry_at and profile.next_retry_at > now
        task = None
        if self._closed:
            return empty
        if not fresh and not retry_wait:
            task = self._query(app, raw_id, profile, plaintext=account.channel_type == "wecom_app")
            if await_refresh or not profile or not profile.name:
                try:
                    await asyncio.wait_for(asyncio.shield(task), self.wait_seconds)
                except TimeoutError:
                    result["status"] = "pending"
                except asyncio.CancelledError:
                    current = asyncio.current_task()
                    if self._closed and current is not None and not current.cancelling():
                        return empty
                    raise
                profile = await self.store.find_profile(app.id, raw_id)
        if not await self._account_matches(account.id, app.id, app.revision):
            return empty
        if profile:
            name = profile.name
            if await_refresh and (not profile.expires_at or profile.expires_at <= datetime.now(timezone.utc)):
                name = None
            result.update(userid=profile.userid, name=name)
            result["status"] = "resolved" if name else "partial"
        return result

    async def start_refresh(self, app_id):
        async with self._refresh_lock:
            return await self._start_refresh(app_id)

    async def _start_refresh(self, app_id):
        if self._closed:
            raise WecomError("refresh", "service_closed")
        for task_id, task in self._bulk_tasks.items():
            job = self._jobs.get(task_id)
            if job and job["application_id"] == app_id and not task.done():
                return dict(job)
        app = await self.store.get_application(app_id)
        if app is None:
            raise ValueError("企微应用不存在")
        # Refreshing names keeps the valid token and joins in-flight member queries.
        task_id = uuid.uuid4().hex
        job = {"task_id": task_id, "application_id": app_id, "status": "running",
               "total": 0, "completed": 0, "succeeded": 0, "failed": 0}
        self._jobs[task_id] = job
        while len(self._jobs) > 100:
            removable = next((key for key, value in self._jobs.items() if value["status"] != "running"), None)
            if removable is None:
                break
            self._jobs.pop(removable, None)
        profile_ids = await self.store.snapshot_profile_ids(app.id)
        if self._closed:
            job["status"] = "unknown"
            raise WecomError("refresh", "service_closed")
        job["total"] = len(profile_ids)
        task = asyncio.create_task(self._refresh(app, job, profile_ids))
        self._bulk_tasks[task_id] = task
        task.add_done_callback(lambda t: self._bulk_tasks.pop(task_id, None))
        return dict(job)

    async def _refresh(self, app, job, profile_ids):
        try:
            for offset in range(0, len(profile_ids), 100):
                profiles = await self.store.get_profiles_by_ids(app.id, profile_ids[offset:offset+100])
                for start in range(0, len(profiles), 4):
                    batch = profiles[start:start+4]
                    results = await asyncio.gather(*(
                        asyncio.shield(self._query(app, p.open_userid or p.userid, p)) for p in batch
                    ), return_exceptions=True)
                    for result in results:
                        job["completed"] += 1
                        if result is not None and not isinstance(result, BaseException) and result.name:
                            job["succeeded"] += 1
                        else:
                            job["failed"] += 1
            job["status"] = "completed"
        except asyncio.CancelledError:
            job["status"] = "unknown"
            raise
        except Exception:
            job["status"] = "failed"

    def refresh_status(self, app_id, task_id):
        job = self._jobs.get(task_id)
        return dict(job) if job and job["application_id"] == app_id else None
