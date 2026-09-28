from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.usage import RequestUsage
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deep_agent.db.conversation import SqlConversationCoordinator
from deep_agent.db.dialect import DbDialect
from deep_agent.db.models import Agent, Base, ChatSession, ChatSessionEvent, LlmModel, LlmProvider
from deep_agent.db.stores.bundle import create_data_stores
from deep_agent.diagnostics.sessions import SessionFilters
from deep_agent.diagnostics.usage import UsageQueries
from deep_agent.usage import merge_calls, response_call


class UsageCallTests(unittest.TestCase):
    def test_explicit_zero_partial_and_missing_are_distinct(self):
        cases = [
            (RequestUsage(input_tokens=0, output_tokens=0), "reported", 0, 0),
            (RequestUsage(input_tokens=12), "partial", 12, None),
            (RequestUsage(), "unreported", None, None),
        ]
        for usage, state, input_tokens, output_tokens in cases:
            with self.subTest(state=state):
                call = response_call(ModelResponse(parts=[TextPart("x")], usage=usage),
                    request_id="request", model_id="model", provider_id="provider", purpose="chat")
                self.assertEqual((call["state"], call["input_tokens"], call["output_tokens"]),
                    (state, input_tokens, output_tokens))
        self.assertEqual(merge_calls(merge_calls(None, [{"request_id": "a", "state": "partial"}]),
            [{"request_id": "a", "state": "reported"}])["calls"],
            [{"request_id": "a", "state": "reported"}])


class UsageLedgerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.store: SqlConversationCoordinator = create_data_stores(DbDialect.SQLITE, self.factory).coordinator
        self.model_id = "00000000-0000-4000-8000-000000000099"
        self.agent_id = "00000000-0000-4000-8000-000000000001"
        self.provider_id = "1" * 32
        async with self.factory() as db:
            db.add_all([
                LlmProvider(id=self.provider_id, name="Provider", provider_type="openai_compatible",
                    use_default_base_url=False, base_url="http://example.invalid/v1", api_key=None),
                LlmModel(id=self.model_id, provider_id=self.provider_id, name="Model", model="test:model"),
                Agent(id=self.agent_id, name="Agent", model_id=self.model_id, prompt="test"),
            ])
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    def filters(self):
        now = datetime.now(UTC)
        return SessionFilters(start=now - timedelta(days=1), end=now + timedelta(minutes=1), timezone="Asia/Shanghai")

    async def test_source_filters_keep_console_continuation_usage_in_channel(self):
        for sid, kind, source_id in (("internal", None, None), ("channel", "channel:dingtalk", "deleted-account")):
            for n in range(2):
                run = sid + str(n)
                await self.store.append_message_parts(sid,
                    messages=[ModelResponse(parts=[TextPart("reply")], usage=RequestUsage(input_tokens=10, output_tokens=2), run_id=run)],
                    agent_id=self.agent_id, user_id="owner", turn_id=run, run_id=run,
                    usage_model_id=self.model_id, usage_provider_id=self.provider_id)
            async with self.factory() as db:
                await db.execute(update(ChatSession).where(ChatSession.session_id == sid).values(source_type=kind, source_id=source_id))
                await db.commit()
        query = UsageQueries(self.factory)
        f = self.filters()
        all_usage = await query.report(f)
        self.assertEqual(all_usage["summary"]["requests"], 4)
        for kind, source_id in (("internal", None), ("channel:dingtalk", "deleted-account")):
            scoped = SessionFilters(f.start, f.end, source_type=kind, source_id=source_id)
            report = await query.report(scoped)
            self.assertEqual(report["summary"]["requests"], 2)
            sessions, total = await query.sessions(scoped, dimension="agent", key=self.agent_id)
            self.assertEqual(total, 1)
            self.assertEqual(sessions[0]["source_type"], kind)
            self.assertEqual(sessions[0]["source_id"], source_id)

    async def test_split_response_and_replay_use_one_carrier_and_one_request(self):
        message = ModelResponse(parts=[TextPart("first"), TextPart("second")],
            usage=RequestUsage(input_tokens=100, output_tokens=20), run_id="usage-run")
        args = dict(agent_id=self.agent_id, user_id="user-1", turn_id="a" * 32,
            run_id="usage-run", usage_model_id=self.model_id, usage_provider_id=self.provider_id)
        await self.store.append_message_parts("usage-session", messages=[message], **args)
        await self.store.append_message_parts("usage-session", messages=[message], terminal_status="completed", **args)
        events = await self.store.list_events("usage-session")
        self.assertEqual(len(events), 2)
        self.assertEqual(sum(event.usage is not None for event in events), 1)
        report = await UsageQueries(self.factory).report(self.filters())
        self.assertEqual(report["summary"]["requests"], 1)
        self.assertEqual(report["summary"]["total_tokens"], 120)

    async def test_title_and_compaction_calls_aggregate_without_run_usage(self):
        user = await self.store.append("other-session",
            [ModelRequest(parts=[UserPromptPart(content="question")], run_id="other-run")],
            agent_id=self.agent_id, user_id="user-1", turn_id="b" * 32, run_id="other-run")
        fallback = await self.store.ensure_title_fallback("other-session", agent_id=self.agent_id,
            user_id="user-1", title="question", source_event_id=user[0].event_id,
            turn_id="b" * 32, run_id="other-run")
        self.assertIsNotNone(fallback)
        fallback_updated_at = fallback.updated_at
        title_call = response_call(ModelResponse(parts=[TextPart("Title")],
            usage=RequestUsage(input_tokens=10, output_tokens=2)), request_id="title-1",
            model_id=self.model_id, provider_id=self.provider_id, purpose="title")
        updated_title = await self.store.attach_usage(fallback.event_id, calls=[title_call], usage_model_id=self.model_id)
        self.assertEqual(updated_title.updated_at, fallback_updated_at)
        compaction = await self.store.append_lifecycle("other-session", event_type="compaction",
            status="running", agent_id=self.agent_id, user_id="user-1", turn_id="b" * 32,
            run_id="other-run", payload={"reason": "manual"})
        first = response_call(ModelResponse(parts=[TextPart("summary")],
            usage=RequestUsage(input_tokens=30, output_tokens=4)), request_id="summary-1",
            model_id=self.model_id, provider_id=self.provider_id, purpose="compaction")
        second = response_call(ModelResponse(parts=[TextPart("summary 2")],
            usage=RequestUsage(input_tokens=40, output_tokens=5)), request_id="summary-2",
            model_id=self.model_id, provider_id=self.provider_id, purpose="compaction")
        await self.store.attach_usage(compaction.event_id, calls=[first, second], usage_model_id=self.model_id)
        await self.store.attach_usage(compaction.event_id, calls=[first], usage_model_id=self.model_id)
        await self.store.update_lifecycle(compaction.event_id, status="failed", payload={"reason": "timeout"})
        report = await UsageQueries(self.factory).report(self.filters())
        self.assertEqual(report["summary"]["requests"], 3)
        self.assertEqual(report["summary"]["total_tokens"], 91)
        self.assertEqual({row["key"]: row["requests"] for row in report["groups"]["purpose"]},
            {"title": 1, "compaction": 2})
        persisted = await self.store.list_events("other-session")
        failed = next(event for event in persisted if event.event_id == compaction.event_id)
        self.assertEqual(failed.status, "failed")
        self.assertEqual(len(failed.usage["calls"]), 2)
        sessions, total = await UsageQueries(self.factory).sessions(self.filters(), dimension="agent", key=self.agent_id)
        self.assertEqual(total, 1)
        self.assertEqual(sessions[0]["requests"], 3)


    async def test_usage_returns_stored_channel_title_unchanged(self):
        from deep_agent.db.models import ChatSession
        response = ModelResponse(parts=[TextPart("reply")], usage=RequestUsage(input_tokens=10, output_tokens=2), run_id="run")
        await self.store.append_message_parts("channel-session", messages=[response],
            agent_id=self.agent_id, user_id="owner", turn_id="c" * 32, run_id="run",
            usage_model_id=self.model_id, usage_provider_id=self.provider_id)
        stored_title = "User｜Old Name(A)"
        async with self.factory() as db:
            await db.execute(update(ChatSession).where(ChatSession.session_id == "channel-session")
                .values(title=stored_title))
            await db.commit()
        rows, _ = await UsageQueries(self.factory).sessions(self.filters(), dimension="agent", key=self.agent_id)
        self.assertEqual(rows[0]["title"], stored_title)

    async def test_timezone_and_deleted_model_keep_historical_usage(self):
        response = ModelResponse(parts=[TextPart("reply")],
            usage=RequestUsage(input_tokens=10, output_tokens=2),
            model_name="historical-model", provider_name="provider-snapshot", run_id="date-run")
        await self.store.append("date-session", [response], agent_id=self.agent_id,
            user_id="user-1", turn_id="c" * 32, run_id="date-run",
            usage_model_id=self.model_id, usage_provider_id=self.provider_id)
        historical_time = datetime(2026, 9, 22, 23, 30, tzinfo=UTC)
        async with self.factory() as db:
            await db.execute(update(ChatSessionEvent).where(ChatSessionEvent.session_id == "date-session")
                .values(created_at=historical_time, updated_at=historical_time))
            await db.execute(delete(LlmModel).where(LlmModel.id == self.model_id))
            await db.commit()
        filters = SessionFilters(start=datetime(2026, 9, 22, 0, tzinfo=UTC),
            end=datetime(2026, 9, 24, 0, tzinfo=UTC), timezone="Asia/Shanghai")
        report = await UsageQueries(self.factory).report(filters)
        self.assertEqual(report["groups"]["date"][0]["key"], "2026-09-23")
        self.assertEqual(report["groups"]["model"][0]["key"], self.model_id)
        self.assertTrue(report["model_snapshots"][self.model_id]["deleted"])
        self.assertEqual(report["model_snapshots"][self.model_id]["name"], "historical-model")

    async def test_filters_and_missing_counters_use_only_selected_events(self):
        for session_id, user_id, usage in [
            ("included", "user-1", RequestUsage(input_tokens=10)),
            ("other-user", "user-2", RequestUsage(input_tokens=100, output_tokens=20)),
        ]:
            await self.store.append(session_id, [ModelResponse(parts=[TextPart("reply")], usage=usage)],
                agent_id=self.agent_id, user_id=user_id, run_id=session_id, turn_id=session_id,
                usage_model_id=self.model_id, usage_provider_id=self.provider_id)
        base = self.filters()
        report = await UsageQueries(self.factory).report(SessionFilters(start=base.start, end=base.end,
            agent_id=self.agent_id, user_id="user-1", timezone=base.timezone))
        self.assertEqual(report["summary"]["requests"], 1)
        self.assertEqual(report["summary"]["total_tokens"], 10)
        self.assertEqual(report["summary"]["missing_requests"], 1)
        self.assertEqual(report["summary"]["input_reported_requests"], 1)
        self.assertEqual(report["summary"]["output_reported_requests"], 0)
        empty = await UsageQueries(self.factory).report(SessionFilters(start=base.start, end=base.end,
            agent_id="absent", timezone=base.timezone))
        self.assertEqual(empty["summary"]["requests"], 0)
