from __future__ import annotations

from datetime import UTC, datetime, timedelta
from time import perf_counter
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ThinkingPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.usage import RequestUsage
from sqlalchemy import event as sql_event, select, update

from deep_agent.agent.active_runs import ActiveRunRegistry
from deep_agent.api.v1.admin import resolve_console_admin_access
from deep_agent.api.v1.diagnostic_sessions import router
from deep_agent.api.v1.sessions import router as ordinary_router
from deep_agent.capabilities.registry import ToolPackageRegistry
from deep_agent.core.context import get_app_context
from deep_agent.db.models import Agent, ChannelAccount, ChatSession, ChatSessionEvent
from deep_agent.diagnostics.sessions import DiagnosticQueryError, DiagnosticSessionQueries, SessionFilters, detail_event_metadata
from deep_agent.security import ConsoleAdminAccess
from tests.unit import test_session_event_store as fixture


class DiagnosticSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.SessionEventStoreTests.asyncSetUp(self)
        self.now = datetime(2026, 9, 9, tzinfo=UTC)
        self.filters = SessionFilters(self.now - timedelta(days=7), self.now, timezone="Asia/Shanghai")
        self.active = ActiveRunRegistry()
        self.queries = DiagnosticSessionQueries(self.factory)
        self.sequence = 0

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def question(self, sid, content="question", *, user="user-one", agent=None, run=None, at=None, terminal=None):
        self.sequence += 1
        run = run or f"run-{self.sequence}"
        rows = await self.store.append(sid, [ModelRequest(parts=[UserPromptPart(content)], run_id=run)],
            agent_id=agent or self.agent_id, user_id=user, run_id=run, turn_id=f"turn-{self.sequence}")
        if terminal:
            await self.store.append_message_parts(sid, messages=[ModelResponse(parts=[TextPart("answer")], run_id=run)],
                agent_id=agent or self.agent_id, user_id=user, run_id=run, turn_id=f"turn-{self.sequence}", terminal_status=terminal)
        async with self.factory() as db:
            timestamp = at or self.now - timedelta(hours=1)
            await db.execute(update(ChatSessionEvent).where(ChatSessionEvent.session_id == sid,
                ChatSessionEvent.run_id == run).values(created_at=timestamp, updated_at=timestamp))
            await db.execute(update(ChatSession).where(ChatSession.session_id == sid).values(created_at=self.now - timedelta(days=9)))
            await db.commit()
        return rows[0]

    async def error(self, sid, run, at=None):
        saved = await self.store.append_execution_error(sid, agent_id=self.agent_id, user_id="user-one",
            turn_id="turn", run_id=run, status="failed", payload={"code": "provider_error", "message": "request failed", "phase": "model"})
        async with self.factory() as db:
            await db.execute(update(ChatSessionEvent).where(ChatSessionEvent.event_id == saved.event_id)
                .values(created_at=at or self.now - timedelta(minutes=20)))
            await db.commit()
        return saved

    async def test_stats_platform_owner_cross_agent_and_channel_dedupe(self):
        other_agent = "00000000-0000-4000-8000-000000000002"
        async with self.factory() as db:
            original = (await db.execute(select(Agent))).scalar_one()
            db.add(Agent(id=other_agent, name="Another Agent", model_id=original.model_id, prompt="test"))
            await db.commit()
        await self.question("one")
        await self.question("two", agent=other_agent)
        await self.question("channel")
        await self.question("other-owner", user="user-two")
        async with self.stores.events._write() as db:
            session = (await db.execute(select(ChatSession).where(ChatSession.session_id == "channel"))).scalar_one()
            await self.stores.events._append_title(db, session, title="Channel", source="channel", source_event_id=None,
                turn_id=None, run_id=None, details={"channel": {"platform": "wecom", "account_id": "account", "peer_type": "group", "peer_id": "group"}})
        overview = await self.queries.overview(self.filters)
        self.assertEqual(overview["metrics"], {"session_count": 4, "question_count": 4, "active_user_count": 2, "error_session_count": 0})
        self.assertEqual(sum(day["question_count"] for day in overview["trend"]), 4)
        self.assertEqual(overview["active_users"][0], {"user_id": "user-one", "question_count": 3})
        listing = await self.queries.list(self.filters)
        self.assertEqual(listing["total"], 4)
        channel = next(item for item in listing["items"] if item["session_id"] == "channel")
        self.assertEqual(channel["user_id"], "user-one")
        self.assertEqual(channel["source"], "channel")

    async def test_nested_or_quoted_source_does_not_create_channel_origin(self):
        await self.question("direct")
        async with self.stores.events._write() as db:
            session = (await db.execute(select(ChatSession).where(ChatSession.session_id == "direct"))).scalar_one()
            await self.stores.events._append_title(db, session, title='Question "source":"channel"',
                source="user", source_event_id=None, turn_id=None, run_id=None,
                details={"channel": {"source": "channel", "source_event_id": None}})
        direct = await self.queries.list(self.filters)
        self.assertEqual(direct["total"], 1)
        self.assertEqual(direct["items"][0]["source"], "direct")

    async def test_agent_options_include_configured_agents_without_activity(self):
        overview = await self.queries.overview(self.filters)
        listing = await self.queries.list(self.filters)
        self.assertEqual(overview["metrics"]["session_count"], 0)
        self.assertEqual(overview["agents"], [])
        self.assertEqual(overview["agent_options"], [{"agent_id": self.agent_id, "agent_name": "Agent", "has_channel": False}])
        self.assertEqual(listing["agent_options"], overview["agent_options"])

    async def test_agent_options_mark_channel_bindings_without_exposing_account_secrets(self):
        async with self.factory() as db:
            db.add(ChannelAccount(id="binding", name="WeCom", bot_id="bot", secret="never-expose",
                agent_id=self.agent_id, session_user_id="owner", enabled=False))
            await db.commit()
        options = (await self.queries.overview(self.filters))["agent_options"]
        self.assertEqual(options, [{"agent_id": self.agent_id, "agent_name": "Agent", "has_channel": True}])
        self.assertNotIn("never-expose", str(options))

    async def test_app_filter_metadata_and_total_are_independent_of_pagination(self):
        for sid, app_id in (("app-one-a", "app-one"), ("app-one-b", "app-one"),
                            ("app-two", "app-two"), ("unscoped", None)):
            await self.question(sid)
            async with self.factory() as db:
                await db.execute(update(ChatSession).where(ChatSession.session_id == sid).values(app_id=app_id))
                await db.commit()
        ctx = SimpleNamespace(db_session_factory=self.factory, active_runs=self.active)
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[get_app_context] = lambda: ctx
        app.dependency_overrides[resolve_console_admin_access] = lambda: ConsoleAdminAccess(allowed=True, roles=("enterprise_admin",))
        async def profiles(_request, _caller, ids):
            return {uid: {"id": uid, "display_name": uid} for uid in ids}
        bounds = {"start": self.filters.start.isoformat(), "end": self.filters.end.isoformat()}
        with patch("deep_agent.security.diagnostic_users.resolve_diagnostic_users", profiles):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                pages = []
                for offset in (0, 1, 2):
                    response = await client.get("/api/v1/diagnostics/sessions",
                        params={**bounds, "app_id": "app-one", "limit": 1, "offset": offset}, headers={"user": "admin-user"})
                    self.assertEqual(response.status_code, 200, response.text)
                    page = response.json()
                    self.assertEqual(page["total"], 2)
                    self.assertEqual(page["app_options"], ["app-one", "app-two"])
                    self.assertTrue(all(item["app_id"] == "app-one" for item in page["items"]))
                    pages.extend(item["session_id"] for item in page["items"])
                self.assertEqual(pages, ["app-one-b", "app-one-a"])
                response = await client.get("/api/v1/diagnostics/sessions",
                    params={**bounds, "app_id": "app"}, headers={"user": "admin-user"})
                self.assertEqual(response.json()["total"], 0)

    async def test_event_first_record_time_empty_and_nonquestions(self):
        old = await self.question("old", at=self.now - timedelta(days=20))
        async with self.factory() as db:
            await db.execute(update(ChatSessionEvent).where(ChatSessionEvent.event_id == old.event_id).values(updated_at=self.now))
            db.add(ChatSession(session_id="empty", agent_id=self.agent_id, user_id="empty-owner",
                created_at=self.now - timedelta(hours=2), updated_at=self.now))
            await db.commit()
        await self.question("attachment", [{"kind": "attachment-ref", "id": "file", "name": "test.txt", "mime": "text/plain"}])
        await self.question("context", [{"kind": "context-ref", "type": "test.context", "key": "key", "schema_version": "1", "label": "Context", "payload": {}}])
        await self.store.append_lifecycle("old", event_type="compaction", status="completed", agent_id=self.agent_id,
            user_id="user-one", turn_id=None, run_id=None, payload={})
        async with self.factory() as db:
            await db.execute(update(ChatSessionEvent).where(ChatSessionEvent.event_type == "compaction")
                .values(created_at=self.now - timedelta(minutes=1)))
            await db.commit()
        result = await self.queries.overview(self.filters)
        self.assertEqual(result["metrics"]["session_count"], 4)
        self.assertEqual(result["metrics"]["question_count"], 2)
        self.assertEqual(result["metrics"]["active_user_count"], 1)
        self.assertEqual({item["summary"] for item in result["recent_questions"]}, {"附件输入", "上下文输入"})
        items = {row["session_id"]: row for row in (await self.queries.list(self.filters))["items"]}
        self.assertIsNone(items["old"]["recent_question"])
        self.assertEqual(items["empty"]["latest_status"], "not_started")

    async def test_error_scope_not_latest_error_and_replay_does_not_double_count(self):
        first = await self.question("recover", run="first", terminal="failed")
        await self.error("recover", "first")
        await self.error("recover", "second")
        await self.question("recover", run="third", terminal="completed")
        await self.question("stopped", terminal="interrupted")
        await self.question("unknown")
        overview = await self.queries.overview(self.filters)
        self.assertEqual(overview["metrics"]["error_session_count"], 1)
        self.assertEqual(len(overview["recent_errors"]), 2)
        f = SessionFilters(self.filters.start, self.filters.end, has_execution_error=True)
        selected = await self.queries.list(f)
        self.assertEqual(selected["total"], 1)
        self.assertEqual(selected["items"][0]["latest_status"], "completed")
        self.assertEqual((await self.queries.list(SessionFilters(f.start, f.end, latest_status="error")))["total"], 0)
        rows = {row["session_id"]: row for row in (await self.queries.list(self.filters))["items"]}
        self.assertEqual(rows["stopped"]["latest_status"], "stopped")
        self.assertEqual(rows["unknown"]["latest_status"], "unknown")
        active = self.active.register(agent_id=self.agent_id, session_id="unknown", user_id="user-one", run_id="active")
        selected = await DiagnosticSessionQueries(self.factory, self.active.snapshot()).list(SessionFilters(f.start, f.end, latest_status="running"))
        self.assertEqual([row["session_id"] for row in selected["items"]], ["unknown"])
        self.active.unregister(active)

    async def test_tool_result_and_deferred_resume_are_not_user_questions_or_old_terminal(self):
        await self.question("deferred", run="waiting", terminal="pending")
        self.assertEqual((await self.queries.detail("deferred"))["latest_status"], "waiting")
        await self.store.append_message_parts("deferred", messages=[ModelRequest(parts=[ToolReturnPart("confirm", "yes", "call")])],
            agent_id=self.agent_id, user_id="user-one", run_id="resumed", turn_id="turn-1")
        self.assertEqual((await self.queries.detail("deferred"))["latest_status"], "unknown")
        await self.question("tool-failure", run="tool-run")
        await self.store.append_message_parts("tool-failure", messages=[
            ModelResponse(parts=[ToolCallPart("tool", {}, "call")]),
            ModelRequest(parts=[ToolReturnPart("tool", "failed", "call", outcome="failed")]),
            ModelResponse(parts=[TextPart("Handled failure")]),
        ], agent_id=self.agent_id, user_id="user-one", run_id="tool-run", turn_id="turn-2", terminal_status="completed")
        self.assertEqual((await self.queries.detail("tool-failure"))["latest_status"], "completed")
        overview = await self.queries.overview(self.filters)
        self.assertEqual(overview["metrics"]["question_count"], 2)
        self.assertEqual(overview["metrics"]["error_session_count"], 0)

    async def test_literal_search_stable_page_and_exact_id_respects_filters(self):
        for sid in ("a", "b", "c"):
            await self.question(sid)
        async with self.factory() as db:
            await db.execute(update(ChatSession).where(ChatSession.session_id == "a").values(title="Report 100%_done"))
            await db.execute(update(ChatSession).where(ChatSession.session_id == "b").values(title="Report 100xxdone"))
            await db.commit()
        base = (self.filters.start, self.filters.end)
        self.assertEqual((await self.queries.list(SessionFilters(*base, query="%_")))["total"], 1)
        self.assertEqual((await self.queries.list(SessionFilters(*base, query="a", user_id="not-owner")))["total"], 0)
        pages = [await self.queries.list(SessionFilters(*base, limit=1, offset=index)) for index in range(3)]
        self.assertEqual([p["items"][0]["session_id"] for p in pages], ["c", "b", "a"])

    async def test_half_open_dst_calendar_days_and_zero_bins(self):
        f = SessionFilters(datetime(2026, 3, 7, 5, tzinfo=UTC), datetime(2026, 3, 10, 4, tzinfo=UTC), timezone="America/New_York")
        days = list(f.days())
        self.assertEqual(len(days), 3)
        self.assertEqual(days[1][2] - days[1][1], timedelta(hours=23))
        await self.question("at-start", at=f.start)
        await self.question("at-end", at=f.end)
        result = await self.queries.overview(f)
        self.assertEqual(result["metrics"]["question_count"], 1)
        self.assertEqual([row["question_count"] for row in result["trend"]], [1, 0, 0])

    async def test_exact_session_id_does_not_also_match_another_title(self):
        sid = "a" * 32
        await self.question(sid)
        await self.question("another-session")
        async with self.factory() as db:
            await db.execute(update(ChatSession).where(ChatSession.session_id == "another-session")
                .values(title=f"Mentioning session {sid}"))
            await db.commit()
        result = await self.queries.list(SessionFilters(self.filters.start, self.filters.end, query=sid))
        self.assertEqual([item["session_id"] for item in result["items"]], [sid])
        self.assertEqual(result["total"], 1)

    async def test_bounded_bulk_queries_and_readonly_projection(self):
        await self.question("one")
        statements = []
        def capture(_conn, _cursor, statement, _params, _context, _many):
            statements.append(statement)
        sql_event.listen(self.engine.sync_engine, "before_cursor_execute", capture)
        try:
            await self.queries.list(self.filters)
            self.assertLessEqual(len(statements), 8)
            self.assertTrue(all(sql.lstrip().upper().startswith("SELECT") for sql in statements))
        finally:
            sql_event.remove(self.engine.sync_engine, "before_cursor_execute", capture)

    async def test_isolated_scale_query_plan_and_bulk_read_count(self):
        """1,000 sessions / 10,000 valid events, no live database or new index."""
        from deep_agent.db.event_codec import encode_messages
        count, rounds = 1000, 5
        timestamp = self.now - timedelta(hours=1)
        sessions, events = [], []
        messages = [ModelRequest(parts=[UserPromptPart("Synthetic scale question")]), ModelResponse(parts=[TextPart("Synthetic answer")])]
        for number in range(count):
            sid = f"scale-{number:04d}"
            sessions.append(dict(session_id=sid, agent_id=self.agent_id, user_id=f"platform-{number % 20}",
                created_at=timestamp, updated_at=timestamp, title=f"Synthetic {number}", next_sequence=11))
            for index in range(rounds):
                for offset, draft in enumerate(encode_messages(messages, session_id=sid, run_id=f"run-{index}")):
                    events.append(dict(event_id=draft.event_id, session_id=sid, sequence=index * 2 + offset + 1,
                        schema_version=1, event_type=draft.event_type, status="completed" if offset else None,
                        turn_id=f"turn-{index}", run_id=f"run-{index}", payload=draft.payload,
                        search_text=draft.search_text, created_at=timestamp, updated_at=timestamp))
        async with self.factory() as db:
            await db.execute(ChatSession.__table__.insert(), sessions)
            await db.execute(ChatSessionEvent.__table__.insert(), events)
            await db.commit()
        statements = []
        def capture(_conn, _cursor, statement, params, _context, _many):
            statements.append((statement, params))
        sql_event.listen(self.engine.sync_engine, "before_cursor_execute", capture)
        started = perf_counter()
        try:
            overview = await self.queries.overview(self.filters)
            overview_count = len(statements)
            page = await self.queries.list(self.filters)
            elapsed = perf_counter() - started
        finally:
            sql_event.remove(self.engine.sync_engine, "before_cursor_execute", capture)
        self.assertEqual(overview["metrics"]["question_count"], count * rounds)
        self.assertEqual(page["total"], count)
        self.assertEqual(len(page["items"]), 50)
        self.assertLessEqual(overview_count, 8)
        self.assertLessEqual(len(statements) - overview_count, 8)
        plans = []
        async with self.engine.connect() as connection:
            for statement, params in (statements[0], statements[overview_count + 1]):
                plans.extend(row[3] for row in (await connection.exec_driver_sql("EXPLAIN QUERY PLAN " + statement, params)).all())
        self.assertTrue(any("INDEX ix_chat_session_events" in item for item in plans), plans)
        print(f"Diagnostic isolated SQL benchmark: {count} sessions / {len(events)} events; overview + list {elapsed:.3f}s; {overview_count} + {len(statements) - overview_count} SELECTs; existing event indexes used")

    async def test_message_parts_usage_is_not_multiplied(self):
        await self.store.append_message_parts("parts", messages=[ModelResponse(parts=[ThinkingPart("reason"), TextPart("answer")],
            model_name="historical-model", usage=RequestUsage(input_tokens=30, output_tokens=8))],
            agent_id=self.agent_id, user_id="user-one", turn_id="turn", run_id="run", terminal_status="completed")
        result = detail_event_metadata(await self.store.list_all_events("parts"))
        self.assertEqual(result["usage"]["input_tokens"], 30)
        self.assertEqual(result["usage"]["output_tokens"], 8)
        self.assertEqual(result["model_name"], "historical-model")
        self.assertEqual(result["message_count"], 1)
        self.assertEqual(result["runs"][0]["usage"]["input_tokens"], 30)
        self.assertEqual(result["runs"][0]["model_name"], "historical-model")

    async def test_multiple_responses_and_replayed_saves_count_each_response_once(self):
        responses = [
            ModelResponse(parts=[ThinkingPart("reason"), TextPart("working"), ToolCallPart("read", {}, "call")],
                usage=RequestUsage(input_tokens=10000, output_tokens=1000)),
            ModelRequest(parts=[ToolReturnPart("read", "found", "call")]),
            ModelResponse(parts=[ThinkingPart("finish"), TextPart("answer")],
                usage=RequestUsage(input_tokens=12000, output_tokens=500)),
        ]
        kwargs = dict(agent_id=self.agent_id, user_id="user-one", turn_id="turn", run_id="run",
            terminal_status="completed")
        await self.store.append_message_parts("parts", messages=responses, **kwargs)
        await self.store.append_message_parts("parts", messages=responses, **kwargs)
        result = detail_event_metadata(await self.store.list_all_events("parts"))
        self.assertEqual(result["usage"]["input_tokens"], 22000)
        self.assertEqual(result["usage"]["output_tokens"], 1500)
        self.assertEqual(result["usage"]["total_tokens"], 23500)
        self.assertEqual(result["runs"][0]["recorded_model_responses"], 2)
        self.assertEqual(result["runs"][0]["usage"], result["usage"])
        self.assertFalse(result["usage_partial"])
        self.assertEqual(result["usage_missing_responses"], 0)

    async def test_cache_summary_survives_split_replay_and_reports_partial_history(self):
        from deep_agent.agent.cache_usage import stamp_cache_usage
        responses = [
            ModelResponse(parts=[ThinkingPart("reason"), TextPart("working"), ToolCallPart("read", {}, "call")],
                usage=RequestUsage(input_tokens=10000, output_tokens=500, cache_read_tokens=8000)),
            ModelRequest(parts=[ToolReturnPart("read", "found", "call")]),
            ModelResponse(parts=[ThinkingPart("finish"), TextPart("answer")],
                usage=RequestUsage(input_tokens=6000, output_tokens=500, cache_read_tokens=3000,
                    cache_write_tokens=0)),
        ]
        for message in responses:
            if message.kind == "response":
                stamp_cache_usage(message)
        kwargs = dict(agent_id=self.agent_id, user_id="user-one", turn_id="turn", run_id="run",
            terminal_status="completed")
        await self.store.append_message_parts("cache-parts", messages=responses, **kwargs)
        await self.store.append_message_parts("cache-parts", messages=responses, **kwargs)
        result = detail_event_metadata(await self.store.list_all_events("cache-parts"))
        self.assertEqual(result["cache_usage"]["response_count"], 2)
        self.assertEqual(result["cache_usage"]["hit_ratio"], .6875)
        self.assertEqual(result["cache_usage"]["cache_read_tokens"], 11000)
        self.assertFalse(result["cache_usage"]["write_complete"])
        self.assertEqual(result["runs"][0]["cache_usage"], result["cache_usage"])
        await self.store.append_message_parts("cache-parts",
            messages=[ModelResponse(parts=[TextPart("older unknown cache")],
                usage=RequestUsage(input_tokens=30, output_tokens=2))],
            agent_id=self.agent_id, user_id="user-one", turn_id="next-turn", run_id="next-run",
            terminal_status="completed")
        reopened = detail_event_metadata(await self.store.list_all_events("cache-parts"))
        self.assertIsNone(reopened["cache_usage"]["hit_ratio"])
        self.assertEqual(reopened["cache_usage"]["cache_read_tokens"], 11000)
        self.assertFalse(reopened["cache_usage"]["read_complete"])
        self.assertEqual(reopened["runs"][0]["cache_usage"]["hit_ratio"], .6875)

    async def test_missing_usage_is_unknown_and_partial_usage_is_explicit(self):
        kwargs = dict(agent_id=self.agent_id, user_id="user-one", turn_id="turn", run_id="run")
        await self.store.append_message_parts("missing", messages=[ModelResponse(parts=[TextPart("answer")])],
            terminal_status="completed", **kwargs)
        events = await self.store.list_all_events("missing")
        result = detail_event_metadata(events)
        self.assertIsNone(result["usage"])
        self.assertEqual(result["usage_missing_responses"], 1)
        self.assertNotIn("usage", result["runs"][0])
        from deep_agent.conversation.projector import project_session_events
        self.assertIsNone(project_session_events(events).items[0].usage)
        self.assertEqual(events[0].payload["message"]["usage"]["input_tokens"], 0)
        await self.store.append_message_parts("missing", messages=[ModelResponse(parts=[TextPart("measured")],
            usage=RequestUsage(input_tokens=25, output_tokens=0))], first_message_ordinal=2,
            terminal_status="completed", **kwargs)
        result = detail_event_metadata(await self.store.list_all_events("missing"))
        self.assertEqual(result["usage"]["total_tokens"], 25)
        self.assertTrue(result["usage_partial"])
        self.assertTrue(result["runs"][0]["usage_partial"])
        self.assertEqual(result["runs"][0]["usage_missing_responses"], 1)

    async def test_duplicate_save_distinct_inputs_and_legacy_run_are_separate(self):
        from deep_agent.db.serialization import deserialize_message
        saved = await self.question("duplicate", content="same", run="original")
        await self.store.append("duplicate", [deserialize_message(saved.payload["message"])],
            agent_id=self.agent_id, user_id="user-one", run_id="original", turn_id="turn-1")
        await self.question("duplicate", content="same", run="new")
        legacy = await self.question("legacy", at=self.now - timedelta(days=20))
        async with self.factory() as db:
            await db.execute(update(ChatSessionEvent).where(ChatSessionEvent.event_id == legacy.event_id)
                .values(event_type="run/progress", created_at=self.now - timedelta(minutes=2)))
            await db.commit()
        overview = await self.queries.overview(self.filters)
        self.assertEqual(overview["metrics"]["session_count"], 1)
        self.assertEqual(overview["metrics"]["question_count"], 2)
        self.assertEqual(detail_event_metadata(await self.store.list_all_events("legacy"))["message_count"], 0)

    async def test_single_user_profile_is_admin_only_validated_and_no_store(self):
        from deep_agent.security.diagnostic_users import DiagnosticUserAuthenticationError
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[resolve_console_admin_access] = lambda: ConsoleAdminAccess(allowed=True, roles=("enterprise_admin",))
        calls = []
        async def profiles(request, caller, ids):
            calls.append((caller, ids, request.headers.get("authorization")))
            return {uid: {"user_id": uid, "username": "tester", "email": "test@example.com", "resolution": "resolved"} for uid in ids}
        path = "/api/v1/diagnostics/sessions/user-profile"
        with patch("deep_agent.security.diagnostic_users.resolve_diagnostic_users", profiles):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(path, params={"user_id": "4"}, headers={"user": "caller", "authorization": "test-only"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertEqual(response.json()["user"]["user_id"], "4")
                self.assertEqual(calls, [("caller", ["4"], "test-only")])
                for params in ({}, {"user_id": ""}, {"user_id": " "}, {"user_id": "x" * 129}, {"user_id": " x"}, [("user_id", "4"), ("user_id", "5")]):
                    invalid = await client.get(path, params=params, headers={"user": "caller"})
                    self.assertEqual(invalid.status_code, 422)
                    self.assertEqual(invalid.headers["cache-control"], "no-store")
                app.dependency_overrides[resolve_console_admin_access] = lambda: ConsoleAdminAccess(allowed=False, roles=())
                denied = await client.get(path, params={"user_id": "4"}, headers={"user": "caller"})
                self.assertEqual(denied.status_code, 403)
                self.assertEqual(denied.headers["cache-control"], "no-store")
                self.assertEqual(len(calls), 1)
        app.dependency_overrides[resolve_console_admin_access] = lambda: ConsoleAdminAccess(allowed=True, roles=("enterprise_admin",))
        async def expired(*args):
            raise DiagnosticUserAuthenticationError(401)
        with patch("deep_agent.security.diagnostic_users.resolve_diagnostic_users", expired):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                expired_result = await client.get(path, params={"user_id": "4"}, headers={"user": "caller"})
                self.assertEqual(expired_result.status_code, 401)
                self.assertEqual(expired_result.headers["cache-control"], "no-store")

    async def test_admin_cross_owner_history_event_permissions_and_no_store(self):
        await self.question("private", user="other-user", terminal="completed")
        ctx = SimpleNamespace(db_session_factory=self.factory, active_runs=self.active, store=self.store,
            stores=self.stores, registry=ToolPackageRegistry())
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        app.include_router(ordinary_router, prefix="/api/v1")
        for path in ("/api/v1/diagnostics/sessions", "/api/v1/diagnostics/sessions/overview", "/api/v1/diagnostics/sessions/users"):
            self.assertNotIn("source", [p["name"] for p in app.openapi()["paths"][path]["get"].get("parameters", [])])
        app.dependency_overrides[get_app_context] = lambda: ctx
        app.dependency_overrides[resolve_console_admin_access] = lambda: ConsoleAdminAccess(allowed=True, roles=("enterprise_admin",))
        async def profiles(_request, _caller, ids):
            return {uid: {"id": uid, "display_name": uid} for uid in ids}
        with patch("deep_agent.security.diagnostic_users.resolve_diagnostic_users", profiles):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                for suffix in ("", "/history", "/events"):
                    response = await client.get("/api/v1/diagnostics/sessions/private" + suffix, headers={"user": "admin-user"})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.headers["cache-control"], "no-store")
                bounds = {"start": self.filters.start.isoformat(), "end": self.filters.end.isoformat()}
                for suffix in ("", "/overview"):
                    response = await client.get("/api/v1/diagnostics/sessions" + suffix,
                        params={**bounds, "user_id": "other-user"}, headers={"user": "admin-user"})
                    self.assertEqual(response.status_code, 200, response.text)
                    data = response.json()
                    self.assertEqual(data["total"] if suffix == "" else data["metrics"]["session_count"], 1)
                    self.assertIn("other-user", data["users"])
                for params in ({"limit": 201}, {**bounds, "end": self.filters.start.isoformat()}, {"timezone": "invalid"}):
                    response = await client.get("/api/v1/diagnostics/sessions", params=params, headers={"user": "admin-user"})
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual(response.headers["cache-control"], "no-store")
                response = await client.get("/api/v1/diagnostics/sessions/private/events", params={"limit": 1}, headers={"user": "admin-user"})
                after = response.json()["next_after_sequence"]
                self.assertIsNotNone(after)
                following = await client.get("/api/v1/diagnostics/sessions/private/events", params={"after_sequence": after}, headers={"user": "admin-user"})
                self.assertTrue(all(row["sequence"] > after for row in following.json()["items"]))
                self.assertEqual((await client.post("/api/v1/diagnostics/sessions/private", headers={"user": "admin-user"})).status_code, 405)
                async with self.factory() as db:
                    await db.execute(update(ChatSessionEvent).where(ChatSessionEvent.session_id == "private",
                        ChatSessionEvent.event_type == "user/message").values(schema_version=99))
                    await db.commit()
                response = await client.get("/api/v1/diagnostics/sessions/private/history", headers={"user": "admin-user"})
                self.assertEqual(response.status_code, 409)
                self.assertEqual((await client.get("/api/v1/diagnostics/sessions/private/events", headers={"user": "admin-user"})).status_code, 200)
                self.assertEqual((await client.get("/api/v1/diagnostics/sessions/private", headers={"user": "admin-user"})).status_code, 200)
                response = await client.get("/api/v1/sessions/private/history", headers={"user": "admin-user"})
                self.assertEqual(response.status_code, 404)
                app.dependency_overrides[resolve_console_admin_access] = lambda: ConsoleAdminAccess(allowed=False, roles=())
                self.assertEqual((await client.get("/api/v1/diagnostics/sessions/private/events", headers={"user": "other-user"})).status_code, 403)
                app.dependency_overrides.pop(resolve_console_admin_access)
                self.assertEqual((await client.get("/api/v1/diagnostics/sessions/private/events")).status_code, 401)

    async def test_user_search_is_deduplicated_bounded_and_independent_of_session_page(self):
        async with self.factory() as db:
            db.add_all([ChatSession(session_id=f"candidate-{i}", agent_id=self.agent_id,
                user_id=f"user-{i:03}", created_at=self.now - timedelta(hours=1),
                updated_at=self.now - timedelta(hours=1)) for i in range(102)])
            db.add(ChatSession(session_id="duplicate-user", agent_id=self.agent_id,
                user_id="user-000", created_at=self.now - timedelta(hours=1)))
            await db.commit()
        statements = []
        def capture(_conn, _cursor, statement, *_args): statements.append(statement)
        sql_event.listen(self.engine.sync_engine, "before_cursor_execute", capture)
        try:
            first = await self.queries.user_option_ids(SessionFilters(self.filters.start, self.filters.end,
                user_id="unrelated", query="LY", limit=1, offset=999))
        finally:
            sql_event.remove(self.engine.sync_engine, "before_cursor_execute", capture)
        self.assertEqual(len(statements), 1)
        self.assertEqual(len(first["ids"]), 100)
        self.assertEqual(first["next_after"], "user-099")
        self.assertNotIn(".payload", statements[0])
        second = await self.queries.user_option_ids(self.filters, after=first["next_after"])
        self.assertEqual(second, {"ids": ["user-100", "user-101"], "next_after": None})
        app = FastAPI(); app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[get_app_context] = lambda: SimpleNamespace(db_session_factory=self.factory, active_runs=self.active)
        app.dependency_overrides[resolve_console_admin_access] = lambda: ConsoleAdminAccess(allowed=True, roles=("enterprise_admin",))
        async def profiles(_request, _caller, ids):
            self.assertLessEqual(len(ids), 100)
            return {uid: {"display_name": "LY" if uid == "user-101" else "Alice",
                          "username": "LY" if uid == "user-101" else "Alice",
                          "nickname": "李亚" if uid == "user-101" else None,
                          "email": "ly@example.test" if uid == "user-101" else None} for uid in ids}
        with patch("deep_agent.security.diagnostic_users.resolve_diagnostic_users", profiles):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                params = {"start": self.filters.start.isoformat(), "end": self.filters.end.isoformat(), "after": "user-099", "query": "ly"}
                response = await client.get("/api/v1/diagnostics/sessions/users", params=params, headers={"user": "admin"})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["items"], [{"user_id":"user-101", "display_name":"LY",
                    "username": "LY", "nickname": "李亚", "email": "ly@example.test"}])
                for query in ("李亚", "LY@EXAMPLE"):
                    params["query"] = query
                    response = await client.get("/api/v1/diagnostics/sessions/users", params=params, headers={"user":"admin"})
                    self.assertEqual([item["user_id"] for item in response.json()["items"]], ["user-101"])
                params["query"] = "user-100"
                response = await client.get("/api/v1/diagnostics/sessions/users", params=params, headers={"user":"admin"})
                self.assertEqual(response.json()["items"][0]["user_id"], "user-100")
                app.dependency_overrides[resolve_console_admin_access] = lambda: ConsoleAdminAccess(allowed=False, roles=())
                self.assertEqual((await client.get("/api/v1/diagnostics/sessions/users", params=params, headers={"user":"other"})).status_code, 403)

    def test_invalid_range_zone_status(self):
        for kwargs in ({"end": self.filters.start}, {"end": self.filters.start + timedelta(days=32)}, {"timezone": "invalid"}, {"latest_status": "success"}):
            with self.assertRaises(DiagnosticQueryError):
                SessionFilters(**{"start": self.filters.start, "end": self.filters.end, **kwargs})
