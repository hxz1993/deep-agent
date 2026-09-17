"""Read-only cache browsing: real SQLite queries and isolated ASGI requests."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, insert

from deep_agent.api.v1.admin import require_console_admin
from deep_agent.api.v1.wecom_applications import router
from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.db.models import WecomUserProfile
from deep_agent.security import ConsoleAdminAuthorizer
from tests.helpers.sqlite_stores import make_sqlite_stores


class CacheMembersTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stores, self.engine, self.factory = await make_sqlite_stores()
        self.store = self.stores.wecom
        self.app = await self.store.create_application(name="应用", corp_id="corp", wecom_agent_id="1", app_secret="secret-fixture")
        self.other = await self.store.create_application(name="另一应用", corp_id="corp", wecom_agent_id="2", app_secret="other-secret")
        self.now = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)
        self.clock = patch("deep_agent.db.stores.wecom.datetime", wraps=datetime)
        self.clock.start().now.return_value = self.now
        self.addCleanup(self.clock.stop)
        rows = []
        for i in range(12):
            # Two timestamps tie, including the default page boundary.
            synced = self.now - timedelta(days=i // 2)
            rows.append(dict(id=f"member-{i:02}", wecom_application_id=self.app.id,
                userid=f"member.{i:02}", userid_key=f"member.{i:02}", open_userid=f"opaque-{i:02}",
                name=f"成员{i:02}", profile_synced_at=synced, expires_at=self.now + timedelta(days=1),
                created_at=self.now - timedelta(days=10)))
        rows[0].update(userid="literal%_!\\id", userid_key="literal%_!\\id", name="带符号的姓名")
        rows[1]["expires_at"] = self.now  # Exact expiry boundary is stale.
        rows[2]["expires_at"] = None
        rows[3]["profile_synced_at"] = None
        rows[4]["profile_synced_at"] = self.now - timedelta(days=7)
        rows[5]["profile_synced_at"] = self.now - timedelta(days=7, microseconds=1)
        async with self.engine.begin() as conn:
            await conn.execute(insert(WecomUserProfile), rows)
            await conn.execute(insert(WecomUserProfile), {
                **rows[0], "id": "other-member", "wecom_application_id": self.other.id,
            })
        self.checker = AsyncMock(return_value=True)
        self.ctx = AppContext.for_testing(stores=self.stores, db_engine=self.engine,
            db_session_factory=self.factory, console_admin_authorizer=ConsoleAdminAuthorizer(self.checker))
        init_context(self.ctx)
        self.api = FastAPI()
        self.api.include_router(router, prefix="/api/v1")
        self.http = AsyncClient(transport=ASGITransport(app=self.api), base_url="http://fixture")
        self.headers = {"user": "enterprise-user", "authorization": "Bearer fixture"}
        self.path = f"/api/v1/wecom-applications/{self.app.id}/cache/members"

    async def asyncTearDown(self):
        await self.http.aclose()
        await self.ctx.event_persister.aclose()
        reset_context()
        await self.engine.dispose()

    async def test_sql_pagination_stable_order_null_last_and_application_isolation(self):
        statements = []
        def record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        event.listen(self.engine.sync_engine, "before_cursor_execute", record)
        first = await self.store.list_cache_members(self.app.id)
        second = await self.store.list_cache_members(self.app.id, offset=10)
        self.assertEqual((first["total"], len(first["items"]), len(second["items"])), (12, 10, 2))
        ids = [row["id"] for row in first["items"] + second["items"]]
        self.assertEqual(len(set(ids)), 12)
        self.assertEqual(ids[-1], "member-03")
        self.assertEqual(ids.index("member-10") + 1, ids.index("member-11"))
        self.assertNotIn("other-member", ids)
        self.assertEqual((await self.store.list_cache_members(self.app.id, offset=100))["items"], [])
        asc = await self.store.list_cache_members(self.app.id, sort="asc", limit=100)
        self.assertEqual(asc["items"][0]["id"], "member-05")
        self.assertEqual(asc["items"][-1]["id"], "member-03")
        self.assertTrue(all(statement.lstrip().upper().startswith("SELECT") for statement in statements))
        pages = [statement for statement in statements if "ORDER BY" in statement]
        self.assertTrue(pages)
        self.assertTrue(all("LIMIT" in statement and "OFFSET" in statement for statement in pages))

    async def test_literal_search_all_three_fields_and_combined_filters(self):
        for q, expected in (("带符号", "member-00"), ("MEMBER.06", "member-06"), ("opaque-07", "member-07"),
                            ("%", "member-00"), ("_", "member-00"), ("!", "member-00"), ("\\", "member-00"),
                            ("%_!\\", "member-00")):
            with self.subTest(q=q):
                page = await self.store.list_cache_members(self.app.id, q=q)
                self.assertEqual(page["total"], 1)
                self.assertEqual(page["items"][0]["id"], expected)
        page = await self.store.list_cache_members(self.app.id, q="成员", status="stale", synced="day")
        self.assertEqual([row["id"] for row in page["items"]], ["member-01", "member-02"])
        self.assertEqual((await self.store.list_cache_members(self.app.id, q="missing"))["total"], 0)

    async def test_status_matches_stats_at_expiry_boundary_and_after_invalidation(self):
        stale = await self.store.list_cache_members(self.app.id, status="stale")
        valid = await self.store.list_cache_members(self.app.id, status="valid")
        stats = await self.store.cache_stats(self.app.id)
        self.assertEqual(stale["total"], stats["pending"])
        self.assertEqual(stale["total"] + valid["total"], stats["total"])
        self.assertEqual({r["id"] for r in stale["items"]}, {"member-01", "member-02", "member-03"})
        self.assertTrue(all(row["status"] == "stale" for row in stale["items"]))
        self.assertTrue(all(row["status"] == "valid" for row in valid["items"]))
        await self.store.invalidate_cache(self.app.id)
        self.assertEqual((await self.store.list_cache_members(self.app.id, status="stale"))["total"], 12)
        self.assertEqual((await self.store.list_cache_members(self.other.id, status="valid"))["total"], 1)

    async def test_synced_boundaries(self):
        expected = {"day": {"member-00", "member-01", "member-02"},
            "week": {f"member-{i:02}" for i in range(12)} - {"member-03", "member-05"},
            "older": {"member-05"}, "never": {"member-03"}}
        for synced, ids in expected.items():
            with self.subTest(synced=synced):
                page = await self.store.list_cache_members(self.app.id, synced=synced, limit=100)
                self.assertEqual({row["id"] for row in page["items"]}, ids)

    async def test_api_serialization_filtering_and_read_only_service_boundary(self):
        with patch.object(AppContext, "get_wecom_profiles", side_effect=AssertionError("must not call WeCom")):
            response = await self.http.get(self.path, params={"q": "opaque", "status": "stale", "limit": 1, "offset": 1}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        data = response.json()
        self.assertEqual((data["total"], data["limit"], data["offset"]), (3, 1, 1))
        self.assertEqual(data["items"][0]["id"], "member-02")
        self.assertEqual(set(data["items"][0]), {"id", "name", "userid", "open_userid", "profile_synced_at", "expires_at", "created_at", "last_error_stage", "last_error_code", "status"})
        self.assertTrue(data["items"][0]["profile_synced_at"].endswith("Z"))
        self.assertNotIn("secret", response.text)
        other = await self.http.get(self.path.replace(self.app.id, self.other.id), headers=self.headers)
        self.assertEqual(other.json()["total"], 1)
        missing = await self.http.get(self.path.replace(self.app.id, "missing"), headers=self.headers)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.headers["cache-control"], "no-store")

    async def test_api_rejects_invalid_parameters_without_echoing_input(self):
        for params in ({"status": "bad"}, {"synced": "bad"}, {"sort": "bad"}, {"limit": 0}, {"limit": 101}, {"offset": -1}, {"q": "private" * 100}):
            with self.subTest(params=list(params)):
                response = await self.http.get(self.path, params=params, headers=self.headers)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertNotIn("private", response.text)

    async def test_api_admin_dependency_and_denial_do_not_query_members(self):
        route = next(route for route in router.routes if route.path.endswith("/cache/members"))
        self.assertIn(require_console_admin, {dependency.call for dependency in route.dependant.dependencies})
        with patch.object(self.store, "list_cache_members", side_effect=AssertionError("unauthorized query")):
            missing = await self.http.get(self.path)
            self.assertEqual(missing.status_code, 401)
            self.assertEqual(missing.headers["cache-control"], "no-store")
            self.checker.return_value = False
            denied = await self.http.get(self.path, headers={"user": "ordinary", "authorization": "Bearer ordinary-fixture"})
            self.assertEqual(denied.status_code, 403)
            self.assertEqual(denied.headers["cache-control"], "no-store")
