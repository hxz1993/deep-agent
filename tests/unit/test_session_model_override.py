from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from deep_agent.api.v1.deps import build_runner_for_agent
from deep_agent.core.exceptions import AgentNotFoundError, LlmModelNotFoundError
from deep_agent.db.models import Agent, ChatSession, LlmModel


def _agent(*, model_id: str) -> Agent:
    row = MagicMock(spec=Agent)
    row.id = "00000000-0000-4000-8000-000000000001"
    row.name = "demo"
    row.model_id = model_id
    row.prompt = "You are helpful."
    row.capabilities = None
    row.temperature = 0.2
    row.llm_model = _llm(model_id, "default-model")
    return row


def _llm(model_id: str, model: str) -> LlmModel:
    return LlmModel(id=model_id, name=model, model=f"openai:{model}")


class BuildRunnerForAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_agent_default_model(self) -> None:
        default = _llm("00000000-0000-4000-8000-000000000010", "gpt-a")
        agent = _agent(model_id=default.id)
        ctx = AsyncMock()
        ctx.stores.agent.get_by_id = AsyncMock(return_value=agent)
        ctx.stores.llm_model.get_by_id = AsyncMock(return_value=default)

        with patch("deep_agent.api.v1.deps._build_runner_from_spec", new_callable=AsyncMock) as build:
            build.return_value = object()
            await build_runner_for_agent(ctx, agent.id)
            spec = build.call_args.args[0]
            self.assertEqual(spec.model, "openai:gpt-a")
            self.assertEqual(spec.instructions, "You are helpful.")

    async def test_model_id_override(self) -> None:
        default = _llm("00000000-0000-4000-8000-000000000010", "gpt-a")
        override = _llm("00000000-0000-4000-8000-000000000011", "gpt-b")
        agent = _agent(model_id=default.id)
        ctx = AsyncMock()
        ctx.stores.agent.get_by_id = AsyncMock(return_value=agent)
        ctx.stores.llm_model.get_by_id = AsyncMock(return_value=override)

        with patch("deep_agent.api.v1.deps._build_runner_from_spec", new_callable=AsyncMock) as build:
            build.return_value = object()
            await build_runner_for_agent(ctx, agent.id, model_id=override.id)
            spec = build.call_args.args[0]
            self.assertEqual(spec.model, "openai:gpt-b")
            self.assertEqual(spec.temperature, 0.2)

    async def test_missing_override_model_raises(self) -> None:
        agent = _agent(model_id="00000000-0000-4000-8000-000000000010")
        ctx = AsyncMock()
        ctx.stores.agent.get_by_id = AsyncMock(return_value=agent)
        ctx.stores.llm_model.get_by_id = AsyncMock(return_value=None)
        with self.assertRaises(LlmModelNotFoundError):
            await build_runner_for_agent(ctx, agent.id, model_id="missing")

    async def test_missing_agent_raises(self) -> None:
        ctx = AsyncMock()
        ctx.stores.agent.get_by_id = AsyncMock(return_value=None)
        with self.assertRaises(AgentNotFoundError):
            await build_runner_for_agent(ctx, "missing-agent")

    async def test_override_keeps_agent_prompt_and_capabilities(self) -> None:
        default = _llm("00000000-0000-4000-8000-000000000010", "gpt-a")
        override = _llm("00000000-0000-4000-8000-000000000011", "gpt-b")
        agent = _agent(model_id=default.id)
        agent.capabilities = ["native", "skill:calculator"]
        ctx = AsyncMock()
        ctx.stores.agent.get_by_id = AsyncMock(return_value=agent)
        ctx.stores.llm_model.get_by_id = AsyncMock(return_value=override)

        with patch("deep_agent.api.v1.deps._build_runner_from_spec", new_callable=AsyncMock) as build:
            build.return_value = object()
            await build_runner_for_agent(ctx, agent.id, model_id=override.id)
            spec = build.call_args.args[0]
            self.assertEqual(spec.model, "openai:gpt-b")
            self.assertEqual(spec.instructions, "You are helpful.")
            self.assertEqual(spec.capabilities, ["native", "skill:calculator"])


class ChatSessionModelColumnTests(unittest.TestCase):
    def test_chat_session_has_no_llm_model_id_column(self) -> None:
        cols = {c.key for c in ChatSession.__table__.columns}
        self.assertNotIn("llm_model_id", cols)


if __name__ == "__main__":
    unittest.main()
