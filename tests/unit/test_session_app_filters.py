from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelRequest, UserPromptPart
from sqlalchemy import event, select, update
from sqlalchemy.dialects import mysql, postgresql, sqlite

from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.db.models import ChatSession
from deep_agent.db.stores.chat_session.base import ChatSessionStoreBase
from deep_agent.schemas.agent import AgentCreate
from tests.helpers.sqlite_stores import make_sqlite_stores_sync


class SessionAppFilterTests(unittest.TestCase):
    def setUp(self):
        self.stores, self.engine, self.factory = make_sqlite_stores_sync()
        self.ctx = AppContext.for_testing(
            registry=build_registry(), stores=self.stores, mcp_lease_manager=McpLeaseManager(),
            db_engine=self.engine, db_session_factory=self.factory,
        )
        init_context(self.ctx)
        self.name_resolver = AsyncMock(return_value=SimpleNamespace(
            apps={"budget001": "预算应用", "budget002": "预算应用"}, spaces={"budget": "预算空间"},
        ))
        self.name_patch = patch("deep_agent.api.v1.sessions.resolve_scope_directory", self.name_resolver)
        self.name_patch.start()
        self.agent_a, self.agent_b, self.empty_agent = asyncio.run(self._seed())
        app = FastAPI()
        app.include_router(api_router)
        self.client = TestClient(app, headers={"user": "owner"})

    def tearDown(self):
        self.name_patch.stop()
        reset_context()
        asyncio.run(self.engine.dispose())

    async def _seed(self):
        model = (await self.stores.llm_model.list_all())[0]
        agents = [await self.stores.agent.create_unique(AgentCreate(name=name, model_id=model.id, prompt="test"))
                  for name in ("App test A", "App test B", "No sessions")]
        now = datetime(2026, 11, 4, tzinfo=timezone.utc)
        rows = [
            ("a-one-old", agents[0].id, "owner", "budget001", 1),
            ("a-two", agents[0].id, "owner", "budget002", 6),
            ("a-one-new", agents[0].id, "owner", "budget001", 7),
            ("a-unassigned-null", agents[0].id, "owner", None, 2),
            ("a-unassigned-empty", agents[0].id, "owner", "", 3),
            ("b-one", agents[1].id, "owner", "budget001", 5),
            ("a-other-user", agents[0].id, "another", "budget001", 8),
        ]
        for sid, agent, user, app, order in rows:
            await self.ctx.store.append(sid, [ModelRequest(parts=[UserPromptPart("scope test")])],
                agent_id=agent, user_id=user, app_id=app, space_id="budget" if app else None,
                turn_id=sid, run_id=sid)
            async with self.factory() as db:
                await db.execute(update(ChatSession).where(ChatSession.session_id == sid).values(
                    title="Needle " + sid, updated_at=now + timedelta(minutes=order),
                    # Preserve a genuine legacy empty value instead of normalizing it.
                    app_id=app,
                ))
                await db.commit()
        await self.ctx.store.append_lifecycle("empty", event_type="compaction", status="completed",
            agent_id=agents[0].id, user_id="owner", app_id="budget003", space_id="budget",
            run_id="empty", turn_id="empty", payload={})
        async with self.factory() as db:
            await db.execute(update(ChatSession).where(ChatSession.session_id == "empty").values(
                updated_at=now + timedelta(minutes=9)))
            await db.commit()
        return tuple(agent.id for agent in agents)

    def _list(self, params=None, headers=None):
        response = self.client.get("/api/v1/sessions", params=params or {}, headers=headers or {})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_old_client_defaults_follow_effective_app_header(self):
        self.assertEqual(self._list()["total"], 7)
        canonical = self._list(headers={"app": "budget001", "space": "budget"})
        self.assertEqual(canonical["total"], 3)
        self.assertTrue(all(row["app_id"] == "budget001" for row in canonical["items"]))
        overridden = self._list(headers={"app": "budget001", "space": "budget",
                                          "X-Deep-Agent-App": "budget002"})
        self.assertEqual([row["session_id"] for row in overridden["items"]], ["a-two"])
        self.assertEqual(self._list({"app_scope": "auto"}, {"app": "budget001"})["total"], 3)

    def test_explicit_all_specific_and_unassigned_preserve_owner(self):
        headers = {"app": "budget002"}
        self.assertEqual(self._list({"app_scope": "all"}, headers)["total"], 7)
        one = self._list({"app_scope": "all", "app_id": "budget001", "user_id": "another"}, headers)
        self.assertEqual(one["total"], 3)
        self.assertNotIn("a-other-user", [row["session_id"] for row in one["items"]])
        unassigned = self._list({"app_scope": "unassigned"}, headers)
        self.assertEqual(unassigned["total"], 2)
        self.assertEqual({row["session_id"] for row in unassigned["items"]},
                         {"a-unassigned-null", "a-unassigned-empty"})
        self.assertEqual(self._list({"app_scope": "all", "app_id": "missing001"})["total"], 0)

    def test_paging_and_existing_filters_apply_before_page_and_count(self):
        params = {"app_scope": "all", "app_id": "budget001", "agent_id": self.agent_a,
                  "query": "Needle", "limit": 1}
        first = self._list(params)
        second = self._list({**params, "offset": 1})
        self.assertEqual((first["total"], second["total"]), (2, 2))
        self.assertEqual([first["items"][0]["session_id"], second["items"][0]["session_id"]],
                         ["a-one-new", "a-one-old"])
        self.assertEqual(self._list({**params, "has_messages": "false"})["total"], 0)
        self.assertEqual(self._list({**params, "source_type": "channel:wecom"})["total"], 0)
        self.assertEqual(self._list({**params, "updated_before": "2026-11-04T00:03:00Z"})["total"], 1)
        self.assertEqual(self._list({**params, "query": "a-one-new"})["total"], 1)

    def test_invalid_combinations_and_effective_headers_are_rejected(self):
        for params in ({"app_id": "budget001"}, {"app_scope": "auto", "app_id": "budget001"},
                       {"app_scope": "unassigned", "app_id": "budget001"},
                       {"app_scope": "unknown"}, {"app_scope": "all", "app_id": ""}):
            with self.subTest(params=params):
                self.assertEqual(self.client.get("/api/v1/sessions", params=params).status_code, 422)
        for headers in ({"app": "bad"}, {"app": "budget001", "space": "other"},
                        {"app": "budget001", "X-Deep-Agent-App": "bad"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.client.get("/api/v1/sessions", params={"app_scope": "all"},
                                                 headers=headers).status_code, 422)
        self.assertEqual(self.client.get("/api/v1/sessions", headers={"user": ""}).status_code, 401)

    def test_filter_options_count_full_scope_with_no_history_reads_or_writes(self):
        statements, compiled = [], []
        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)
            if context.compiled is not None:
                compiled.append(context.compiled.statement)
        event.listen(self.engine.sync_engine, "before_cursor_execute", capture)
        try:
            response = self.client.get("/api/v1/sessions/filter-options", params={"agent_id": self.agent_a},
                                       headers={"app": "budget002"})
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", capture)
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual({row["agent_id"]: row["session_count"] for row in payload["agents"]},
                         {self.agent_a: 6, self.agent_b: 1})
        self.assertEqual(payload["unassigned_count"], 2)
        self.assertEqual(payload["total"], 6)
        self.assertEqual({row["app_id"]: row["session_count"] for row in payload["apps"]},
                         {"budget001": 2, "budget002": 1, "budget003": 1})
        self.assertEqual(len(statements), 2, statements)
        self.assertTrue(all("GROUP BY" in sql for sql in statements))
        self.assertTrue(all("chat_session_events" not in sql for sql in statements))
        self.assertTrue(all(sql.lstrip().upper().startswith("SELECT") for sql in statements))
        for dialect in (sqlite.dialect(), mysql.dialect(), postgresql.dialect()):
            for statement in compiled:
                self.assertIn("GROUP BY", str(statement.compile(dialect=dialect)))
        self.name_resolver.assert_awaited_once()

    def test_option_order_names_and_agent_switch_do_not_depend_on_pagination_or_keyword(self):
        all_options = self.client.get("/api/v1/sessions/filter-options").json()
        # Empty sessions participate in both the list and options. Their later
        # timestamp makes budget003 the first option.
        self.assertEqual([row["app_id"] for row in all_options["apps"]],
                         ["budget003", "budget001", "budget002"])
        self.assertEqual(all_options["apps"][1]["app_name"], "预算应用")
        self.assertEqual(all_options["apps"][2]["app_name"], "预算应用")
        self.assertIsNone(all_options["apps"][0]["app_name"])
        other = self.client.get("/api/v1/sessions/filter-options", params={"agent_id": self.agent_b,
                                "query": "does not match", "offset": 100}).json()
        self.assertEqual(other["total"], 1)
        self.assertEqual(other["unassigned_count"], 0)
        self.assertEqual([row["app_id"] for row in other["apps"]], ["budget001"])
        empty = self.client.get("/api/v1/sessions/filter-options", params={"agent_id": self.empty_agent}).json()
        self.assertEqual((empty["apps"], empty["total"], empty["unassigned_count"]), ([], 0, 0))
        self.assertEqual(self.client.get("/api/v1/sessions/filter-options", headers={"user": ""}).status_code, 401)

    def test_names_enrich_list_and_detail_without_changing_persisted_scope_or_access(self):
        info = self._list({"app_scope": "all", "app_id": "budget001"})["items"][0]
        self.assertEqual((info["app_name"], info["space_name"]), ("预算应用", "预算空间"))
        response = self.client.get("/api/v1/sessions/a-one-old", headers={"app": "budget002"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((response.json()["app_id"], response.json()["app_name"]), ("budget001", "预算应用"))
        self.assertEqual(self.client.get("/api/v1/sessions/a-other-user").status_code, 404)
        self.name_resolver.return_value = SimpleNamespace(apps={}, spaces={})
        missing = self._list({"app_scope": "all", "app_id": "budget001"})["items"][0]
        self.assertIsNone(missing["app_name"])
        self.assertIsNone(missing["space_name"])
        self.assertEqual(missing["app_id"], "budget001")

    def test_equal_last_activity_uses_app_code_as_stable_tie_break(self):
        async def tie():
            async with self.factory() as db:
                await db.execute(update(ChatSession).where(ChatSession.user_id == "owner").values(
                    updated_at=datetime(2026, 11, 4, tzinfo=timezone.utc)))
                await db.commit()
        asyncio.run(tie())
        result = self.client.get("/api/v1/sessions/filter-options").json()
        self.assertEqual([row["app_id"] for row in result["apps"]], ["budget001", "budget002", "budget003"])

    def test_store_app_condition_compiles_for_supported_databases(self):
        for kwargs in ({"app_id": "budget001"}, {"unassigned_app": True}):
            statement = ChatSessionStoreBase._apply_session_filters(
                select(ChatSession.session_id), agent_id=self.agent_a, user_id="owner",
                updated_after=None, updated_before=None, **kwargs)
            for dialect in (sqlite.dialect(), mysql.dialect(), postgresql.dialect()):
                sql = str(statement.compile(dialect=dialect))
                self.assertIn("app_id", sql)
                self.assertIn("user_id", sql)
