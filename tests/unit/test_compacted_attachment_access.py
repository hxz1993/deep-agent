from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage, UsageLimits

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.runner import AgentRunner
from deep_agent.files import build_file_toolset
from tests.unit import test_session_event_store as fixture
from tests.unit.test_context_compaction import valid_summary


class CompactedAttachmentAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.SessionEventStoreTests.asyncSetUp(self)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_original_attachment_remains_readable_after_compaction(self):
        ref = {"kind": "attachment-ref", "id": "local_original", "name": "README.txt", "mime": "text/plain"}
        raw = [ModelRequest(parts=[UserPromptPart(content=["uploaded", ref])]),
               ModelResponse(parts=[TextPart("received")]),
               ModelRequest(parts=[UserPromptPart(content="latest")]),
               ModelResponse(parts=[TextPart("done")])]
        await self.store.append("files", raw, agent_id=self.agent_id, user_id="user-1", run_id="original-run")
        summary = valid_summary() + "\nMentioned file local_unrelated is not authorization."
        projected = [ModelRequest(parts=[UserPromptPart(content="Summary of previous conversation:\n\n" + summary)]), *raw[-2:]]
        event = await self.store.persist_compaction_projection(
            "files", agent_id=self.agent_id, user_id="user-1", raw_history=raw,
            projected_history=projected, summary_markdown=summary, trigger="pressure",
            model_id="model", estimated_before=100, estimated_after=50, target_tokens=60,
            context_window_tokens=1000, preserved_tail_tokens=25, duration_ms=10,
        )
        self.assertIsNotNone(event)
        runner = AgentRunner(Agent(TestModel(), deps_type=AgentDeps), self.store, UsageLimits(request_limit=4))
        deps = AgentDeps(session_id="files", agent_id=self.agent_id, user="user-1", run_id="next-run")
        await runner._prepare_history("files", "Read the original attachment again", deps)
        self.assertEqual(set(deps.attachment_by_id), {"local_original"})
        ctx = RunContext(deps=deps, model=TestModel(), usage=RunUsage())
        tools = await build_file_toolset().get_tools(ctx)
        self.assertIn("read_file", tools)
        with patch("deep_agent.files.toolset._download_file", new_callable=AsyncMock, return_value=b"gross_profit=130") as download:
            result = await tools["read_file"].call_func({"resource": {"type": "attachment", "file_id": "local_original"}}, ctx)
            self.assertEqual(result.content, "gross_profit=130")
            with self.assertRaises(ModelRetry):
                await tools["read_file"].call_func({"resource": {"type": "attachment", "file_id": "local_unrelated"}}, ctx)
            download.assert_awaited_once()
