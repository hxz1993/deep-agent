"""Observed title usage survives cancellation of the background title task."""
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.usage import RequestUsage

from deep_agent.agent.runner import AgentRunner


class TitleUsageCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_title_retains_received_usage_and_propagates_cancel(self):
        runner = object.__new__(AgentRunner)
        runner.agent = SimpleNamespace(model=None)
        runner.store = SimpleNamespace(attach_usage=AsyncMock(), apply_session_title=AsyncMock())
        runner._model_id = "model"
        runner._model_name = "test-model"
        runner._provider_id = "provider"
        runner._provider_type = "openai_compatible"
        deps = SimpleNamespace(run_id="run", agent_id="agent", file_store=None,
                               request_headers={}, user="user")

        async def received_then_cancelled(**kwargs):
            kwargs["usage_collector"].append(ModelResponse(parts=[TextPart("partial")],
                usage=RequestUsage(input_tokens=10, output_tokens=2)))
            raise asyncio.CancelledError

        with patch("deep_agent.agent.title_generator.generate_title", received_then_cancelled):
            with self.assertRaises(asyncio.CancelledError):
                await runner._generate_model_title("session", deps,
                    fallback_event=SimpleNamespace(event_id="fallback"), message="question",
                    attachments=None, context_refs=None, session_event_queue=None)
        runner.store.apply_session_title.assert_not_awaited()
        runner.store.attach_usage.assert_awaited_once()
        call = runner.store.attach_usage.call_args.kwargs["calls"][0]
        self.assertEqual((call["purpose"], call["input_tokens"], call["output_tokens"]),
                         ("title", 10, 2))
        self.assertEqual(call["request_id"], "fallback:title:0")


class EmptyResponseUsageTests(unittest.IsolatedAsyncioTestCase):
    def test_rejected_tool_response_keeps_metering_header_without_arguments(self):
        from pydantic_ai.messages import ToolCallPart
        from deep_agent.agent.message_persistence import MessagePersistenceCapability
        capability = MessagePersistenceCapability(None, history_length=0, skip_user_prompt=True)
        capability._record(ModelResponse(parts=[ToolCallPart("unknown", {"secret": "value"}, "call")],
                                         usage=RequestUsage(input_tokens=10, output_tokens=2)))
        messages = capability._completed_messages()
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].parts, [])
        self.assertEqual(messages[0].usage.input_tokens, 10)

    async def test_empty_response_usage_survives_later_parts_without_duplicate(self):
        from pydantic_ai.messages import ModelRequest, UserPromptPart
        from tests.helpers.sqlite_stores import make_sqlite_stores
        stores, engine, _ = await make_sqlite_stores()
        try:
            store = stores.coordinator
            from deep_agent.schemas.agent import AgentCreate
            agent = await stores.agent.create_unique(AgentCreate(name="usage-agent", prompt="test"))
            args = dict(agent_id=agent.id, user_id="user", run_id="empty-run", turn_id="empty-turn")
            await store.append("empty-session", [ModelRequest(parts=[UserPromptPart("question")])], **args)
            response = ModelResponse(parts=[], usage=RequestUsage(input_tokens=10, output_tokens=2),
                                     metadata={"deepagent_message_ordinal": 1})
            await store.append_message_parts("empty-session", messages=[response], **args)
            events = await store.list_events("empty-session")
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].usage["calls"][0]["input_tokens"], 10)
            response.parts = [TextPart("answer")]
            await store.append_message_parts("empty-session", messages=[response], **args)
            await store.append_message_parts("empty-session", messages=[response], **args)
            events = await store.list_events("empty-session")
            calls = [call for event in events for call in (event.usage or {}).get("calls", [])]
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["request_id"], "empty-run:1")
        finally:
            await engine.dispose()
