import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from pydantic_ai.messages import ModelRequest, UserPromptPart
from sqlalchemy import event

from deep_agent.api.v1.admin import resolve_console_admin_access
from deep_agent.api.v1.diagnostic_channels import router
from deep_agent.core.context import get_app_context
from deep_agent.db.models import (
    ChannelAccount,
    ChannelMessageRun,
    ChannelSessionBinding,
    WecomApplication,
    WecomUserProfile,
)
from deep_agent.diagnostics.channels import DiagnosticChannelQueries
from deep_agent.diagnostics.sessions import DiagnosticQueryError, SessionFilters
from deep_agent.security import ConsoleAdminAccess
from tests.unit import test_session_event_store as fixture


class DiagnosticChannelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.SessionEventStoreTests.asyncSetUp(self)
        self.now = datetime(2026, 9, 14, tzinfo=UTC)
        self.f = SessionFilters(self.now - timedelta(days=7), self.now)
        self.queries = DiagnosticChannelQueries(self.factory)
        self.n = 0
        async with self.factory() as db:
            db.add(
                WecomApplication(
                    id="app",
                    name="Members",
                    corp_id="corp",
                    wecom_agent_id="100",
                    app_secret="DO-NOT-RETURN",
                )
            )
            db.add(
                WecomUserProfile(
                    id="profile",
                    wecom_application_id="app",
                    userid="Alice",
                    userid_key="alice",
                    open_userid="opaque-A",
                    name="Alice Name",
                )
            )
            db.add_all(
                [
                    ChannelAccount(
                        id=a,
                        name=a,
                        bot_id=a,
                        secret="DO-NOT-RETURN",
                        agent_id=self.agent_id,
                        session_user_id="platform-owner",
                        settings={
                            "wecom": {
                                "user_details_enabled": a == "a",
                                "application_id": "app",
                            }
                        },
                    )
                    for a in ["a", "b"]
                ]
            )
            db.add_all(
                [
                    ChannelSessionBinding(
                        id=k, account_id=a, peer_type=kind, peer_id=peer
                    )
                    for k, a, kind, peer in [
                        ("single", "a", "single", "opaque-A"),
                        ("group", "a", "group", "group-1"),
                        ("single-b", "b", "single", "opaque-A"),
                    ]
                ]
            )
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def question(
        self,
        sid,
        sender="opaque-A",
        binding="single",
        days=1,
        text="Hello",
        account="a",
        persisted=True,
        agent_run=True,
    ):
        self.n += 1
        run = f"run-{self.n}"
        if persisted:
            await self.store.append(
                sid,
                [ModelRequest(parts=[UserPromptPart(text)], run_id=run)],
                agent_id=self.agent_id,
                user_id="platform-owner",
                run_id=run,
                turn_id=run,
            )
        async with self.factory() as db:
            db.add(
                ChannelMessageRun(
                    id=f"msg-{self.n}",
                    account_id=account,
                    binding_id=binding,
                    external_message_id=f"ext-{self.n}",
                    sender_id=sender,
                    session_id=sid,
                    agent_run_id=run if agent_run else None,
                    payload_hash="hash",
                    created_at=self.now - timedelta(days=days),
                )
            )
            await db.commit()

    async def test_account_scope_commands_time_range_and_group_sender_dedupe(self):
        await self.question("private", days=3)
        await self.question("shared", binding="group", days=2, text="Alice asks")
        await self.question(
            "shared", binding="group", sender="bob", days=1, text="Bob asks"
        )
        await self.question("private", sender="ALICE", days=2)
        await self.question("foreign", account="b", binding="single-b")
        await self.question("old", days=9)
        await self.question("private", agent_run=False)  # /new style command
        await self.question("private", persisted=False)  # preflight failure
        result = await self.queries.overview("a", self.f)
        self.assertEqual(
            result["summary"],
            {"active_users": 2, "session_count": 2, "question_count": 4},
        )
        self.assertEqual(
            result["active_users"][0],
            {"user_id": "Alice", "name": "Alice Name", "question_count": 3},
        )
        peers = await self.queries.peers("a", self.f)
        alice = next(r for r in peers["items"] if r["peer_id"] == "Alice")
        self.assertEqual(peers["total"], 1)  # Group-only senders are not private peers.
        self.assertEqual(alice["session_count"], 1)
        self.assertEqual(alice["question_count"], 2)
        self.assertEqual(alice["latest_session"]["session_id"], "private")
        self.assertEqual(alice["latest_session"]["latest_question"], "Hello")
        history = await self.queries.sessions("a", self.f, "user", "Alice")
        self.assertEqual(
            [r["session_id"] for r in history["items"]], ["private"]
        )
        self.assertEqual([r["is_latest"] for r in history["items"]], [True])
        self.assertEqual(history["items"][0]["question_count"], 2)
        groups = await self.queries.peers("a", self.f, "group")
        self.assertEqual(groups["total"], 1)
        self.assertEqual(groups["items"][0]["peer_id"], "group-1")
        self.assertIsNone(groups["items"][0]["name"])
        group_history = await self.queries.sessions("a", self.f, "group", "group-1")
        self.assertEqual([r["session_id"] for r in group_history["items"]], ["shared"])
        self.assertEqual(group_history["items"][0]["latest_question"], "Bob asks")
        self.assertEqual(group_history["items"][0]["question_count"], 2)
        self.assertEqual((await self.queries.sessions("a", self.f, "user", "bob"))["total"], 0)

    async def test_group_sender_filters_counts_latest_and_individual_questions(self):
        await self.question("private", sender="opaque-A", text="Private question")
        await self.question("shared", binding="group", sender="opaque-A", days=3, text="Alice first")
        await self.question("shared", binding="group", sender="ALICE", days=2, text="Alice second")
        await self.question("shared", binding="group", sender="bob", days=1, text="Bob latest")
        groups = await self.queries.peers("a", self.f, "group", sender_id="Alice")
        self.assertEqual(groups["total"], 1)
        item = groups["items"][0]
        self.assertEqual(item["question_count"], 2)
        self.assertEqual(item["session_count"], 1)
        self.assertEqual(item["latest_session"]["latest_question"], "Alice second")
        f = SessionFilters(self.f.start, self.f.end, limit=1)
        history = await self.queries.sessions("a", f, "group", "group-1", sender_id="Alice")
        self.assertEqual(history["mode"], "questions")
        self.assertEqual(history["total"], 2)
        self.assertEqual(history["items"][0]["latest_question"], "Alice second")
        next_page = await self.queries.sessions("a", SessionFilters(self.f.start, self.f.end, limit=1, offset=1), "group", "group-1", sender_id="Alice")
        self.assertEqual(next_page["items"][0]["latest_question"], "Alice first")
        self.assertFalse(next_page["items"][0]["is_latest"])
        self.assertEqual((await self.queries.sessions("a", self.f, "group", "other-group", sender_id="Alice"))["total"], 0)
        users = await self.queries.peers("a", self.f, "user", options_only=True, include_group_senders=True)
        self.assertEqual({p["peer_id"] for p in users["items"]}, {"Alice", "bob"})
        all_groups = await self.queries.sessions("a", self.f, "group", "group-1")
        self.assertEqual(all_groups["items"][0]["latest_question"], "Bob latest")
        self.assertEqual(all_groups["items"][0]["question_count"], 3)

    async def test_picker_resolves_aliases_dedupes_and_skips_event_payloads(self):
        await self.question("private", sender="opaque-A")
        await self.question("private-2", sender="ALICE")
        await self.question("unknown", sender="unmapped")
        statements = []
        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        event.listen(self.engine.sync_engine, "before_cursor_execute", capture)
        try:
            f = SessionFilters(self.f.start, self.f.end, query="opaque-A", limit=25)
            result = await self.queries.peers("a", f, options_only=True)
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", capture)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"], [{"peer_id": "Alice", "name": "Alice Name", "kind": "user"}])
        self.assertLessEqual(len(statements), 3)
        self.assertFalse(any("payload" in q.lower() for q in statements))
        selected = await self.queries.peers("a", self.f, peer_id="Alice")
        self.assertEqual(selected["total"], 1)
        self.assertEqual(selected["items"][0]["question_count"], 2)
        raw = await self.queries.peers("a", self.f, options_only=True, peer_id="unmapped")
        self.assertEqual(raw["items"], [{"peer_id":"unmapped", "name":None, "kind":"user"}])

    async def test_deleted_session_between_page_and_detail_reads_is_tolerated(self):
        from sqlalchemy import delete
        from deep_agent.db.models import ChatSession, ChatSessionEvent

        await self.question("disappearing")

        class DeleteBeforeDetails(DiagnosticChannelQueries):
            async def _session_items(inner, db, base, ids):
                # Advance the visible database state between SQL statements,
                # as a concurrent delete does under READ COMMITTED isolation.
                await db.execute(delete(ChatSessionEvent).where(ChatSessionEvent.session_id.in_(ids)))
                await db.execute(delete(ChatSession).where(ChatSession.session_id.in_(ids)))
                return await super()._session_items(db, base, ids)

        queries = DeleteBeforeDetails(self.factory)
        peers = await queries.peers("a", self.f)
        self.assertEqual(peers["total"], 1)
        self.assertIsNone(peers["items"][0]["latest_session"])
        # The read transaction above rolls back the injected state change.
        sessions = await queries.sessions("a", self.f, "user", "Alice")
        self.assertEqual(sessions["total"], 1)
        self.assertEqual(sessions["items"], [])

    async def test_accounts_project_deepfos_application_without_secret(self):
        async with self.factory() as db:
            account = await db.get(ChannelAccount, "a")
            account.settings = {"deepfos": {"enabled": True, "app_id": "demo001", "api_key": "SECRET-NOT-FOR-DIAGNOSTICS"}}
            await db.commit()
        result = await self.queries.accounts()
        account = next(item for item in result["items"] if item["id"] == "a")
        self.assertEqual(account["deepfos_application"], {"enabled": True, "app_id": "demo001", "api_key_set": True})
        self.assertNotIn("SECRET-NOT-FOR-DIAGNOSTICS", str(result))

    async def test_accounts_accept_null_wecom_settings(self):
        # Null is accepted by the channel settings validator as disabled.
        from deep_agent.db.stores.wecom import validate_channel_wecom_settings
        async with self.factory() as db:
            await validate_channel_wecom_settings(db, {"wecom": None})
            account = await db.get(ChannelAccount, "a")
            account.settings = {"wecom": None}
            await db.commit()
        account = next(item for item in (await self.queries.accounts())["items"] if item["id"] == "a")
        self.assertFalse(account["user_details_enabled"])
        self.assertIsNone(account["application"])

    async def test_disabled_lookup_does_not_reveal_cached_name(self):
        await self.question("foreign", account="b", binding="single-b")
        result = await self.queries.peers("b", self.f)
        self.assertIsNone(result["items"][0]["name"])
        self.assertEqual(result["items"][0]["peer_id"], "opaque-A")
        accounts = await self.queries.accounts()
        self.assertNotIn("DO-NOT-RETURN", str(accounts))
        self.assertEqual(accounts["items"][0]["application"]["agent_id"], "100")

    async def test_search_pagination_empty_and_latest_page(self):
        await self.question("first", days=1)
        await self.question("second", days=2)
        f = SessionFilters(self.f.start, self.f.end, query="Alice Name", limit=1)
        self.assertEqual((await self.queries.peers("a", f))["total"], 1)
        f = SessionFilters(self.f.start, self.f.end, query="%")
        self.assertEqual((await self.queries.peers("a", f))["total"], 0)
        f = SessionFilters(self.f.start, self.f.end, limit=1, offset=1)
        history = await self.queries.sessions("a", f, "user", "Alice")
        self.assertFalse(history["items"][0]["is_latest"])
        self.assertEqual(history["total"], 2)
        self.assertEqual(
            (await self.queries.sessions("a", self.f, "group", "unknown"))["items"], []
        )
        with self.assertRaises(DiagnosticQueryError):
            await self.queries.overview("missing", self.f)

    async def test_queries_are_bounded_and_only_page_event_payloads_loaded(self):
        for i in range(12):
            await self.question(f"session-{i}", sender=f"user-{i}")
        statements = []

        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(self.engine.sync_engine, "before_cursor_execute", capture)
        try:
            result = await self.queries.peers(
                "a", SessionFilters(self.f.start, self.f.end, limit=3)
            )
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", capture)
        self.assertEqual(len(result["items"]), 3)
        self.assertLessEqual(len(statements), 6)
        payload_reads = [s for s in statements if "chat_session_events.payload" in s]
        self.assertEqual(len(payload_reads), 1)
        self.assertIn("max(chat_session_events.sequence)", payload_reads[0])
        self.assertNotIn("channel_accounts.secret", " ".join(statements))

    async def test_opaque_identity_case_and_mysql_57_sql(self):
        await self.question("upper", sender="Opaque", account="b", binding="single-b")
        await self.question("lower", sender="opaque", account="b", binding="single-b")
        self.assertEqual(
            (await self.queries.overview("b", self.f))["summary"]["active_users"], 2
        )
        peers = await self.queries.peers("b", self.f)
        self.assertEqual(
            {row["peer_id"] for row in peers["items"]}, {"Opaque", "opaque"}
        )
        for peer, sid in [("Opaque", "upper"), ("opaque", "lower")]:
            sessions = await self.queries.sessions("b", self.f, "user", peer)
            self.assertEqual([row["session_id"] for row in sessions["items"]], [sid])
        from sqlalchemy.dialects import mysql

        base = self.queries._base(
            SimpleNamespace(id="b", channel_type="wecom", settings={}), self.f
        )
        sql = str(base.select().compile(dialect=mysql.dialect()))
        self.assertIn("COLLATE utf8mb4_bin", sql)
        self.assertNotIn("WITH ", sql)
        self.assertNotIn("row_number", sql)

    async def test_search_uses_aggregate_output_where_on_mysql(self):
        await self.question("first")
        statements = []

        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(self.engine.sync_engine, "before_cursor_execute", capture)
        try:
            for query, expected in [
                ("Alice", 1),
                ("Alice Name", 1),
                ("中文", 0),
                ("", 1),
                ("%", 0),
                ("_", 0),
                ("__no_such_channel_user__", 0),
            ]:
                f = SessionFilters(self.f.start, self.f.end, query=query)
                self.assertEqual((await self.queries.peers("a", f))["total"], expected)
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", capture)
        self.assertFalse(any("HAVING" in sql for sql in statements))
        self.assertTrue(any(".peer_id) LIKE" in sql for sql in statements))

    async def test_admin_api_and_batched_platform_owner_names(self):
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[get_app_context] = lambda: SimpleNamespace(
            db_session_factory=self.factory
        )
        from deep_agent.api.v1.request_identity import require_request_user

        app.dependency_overrides[require_request_user] = lambda: "admin"

        async def access():
            return ConsoleAdminAccess(allowed=True)

        app.dependency_overrides[resolve_console_admin_access] = access
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            with patch(
                "deep_agent.security.diagnostic_users.resolve_diagnostic_users",
                AsyncMock(return_value={"platform-owner": {"display_name": "Owner"}}),
            ) as resolver:
                response = await client.get("/api/v1/diagnostics/channels/accounts")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["items"][0]["user_name"], "Owner")
                self.assertEqual(resolver.await_count, 1)
            result = await client.get("/api/v1/diagnostics/channels/missing/overview")
            self.assertEqual(result.status_code, 404)
            self.assertEqual(result.headers["cache-control"], "no-store")
            response = await client.get(
                "/api/v1/diagnostics/channels/a/peers?kind=invalid"
            )
            self.assertEqual(response.status_code, 422)
            app.dependency_overrides[resolve_console_admin_access] = lambda: (
                ConsoleAdminAccess(allowed=False)
            )
            for path in [
                "accounts",
                "a/overview",
                "a/peers",
                "a/sessions?peer_id=Alice",
            ]:
                denied = await client.get("/api/v1/diagnostics/channels/" + path)
                self.assertEqual(denied.status_code, 403)
