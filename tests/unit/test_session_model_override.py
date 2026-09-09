from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from deep_agent.api.v1.deps import build_runner_for_agent
from deep_agent.models.provider_registry import ProviderConfigurationError
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
    return LlmModel(
        id=model_id,
        name=model,
        model=f"openai:{model}",
        provider_id="00000000-0000-4000-8000-000000000099",
    )


def _provider():
    return SimpleNamespace(
        id="00000000-0000-4000-8000-000000000099",
        name="test",
        provider_type="openai_compatible",
        use_default_base_url=False,
        base_url="https://example.invalid/v1",
        api_key="test-key",
    )


def _ctx() -> AsyncMock:
    ctx = AsyncMock()
    ctx.stores.llm_configuration.get_provider = AsyncMock(
        return_value=_provider()
    )
    return ctx


class BuildRunnerForAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_channel_instructions_are_request_local_and_chinese(self):
        model = _llm("00000000-0000-4000-8000-000000000010", "gpt-a")
        agent = _agent(model_id=model.id)
        ctx = _ctx()
        ctx.stores.agent.get_by_id = AsyncMock(return_value=agent)
        ctx.stores.llm_model.get_by_id = AsyncMock(return_value=model)
        with patch("deep_agent.api.v1.deps._build_runner_from_spec", new_callable=AsyncMock) as build:
            await build_runner_for_agent(ctx, agent.id, channel_type="wecom")
            channel_spec = build.call_args.args[0]
            self.assertIn("本轮消息来自企业微信", channel_spec.instructions)
            self.assertIn("不同 sender_id 代表不同发言者", channel_spec.instructions)
            await build_runner_for_agent(ctx, agent.id)
            self.assertEqual(build.call_args.args[0].instructions, agent.prompt)
            self.assertEqual(agent.prompt, "You are helpful.")

    async def test_uses_agent_default_model(self) -> None:
        default = _llm("00000000-0000-4000-8000-000000000010", "gpt-a")
        agent = _agent(model_id=default.id)
        ctx = _ctx()
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
        ctx = _ctx()
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
        ctx = _ctx()
        ctx.stores.agent.get_by_id = AsyncMock(return_value=agent)
        ctx.stores.llm_model.get_by_id = AsyncMock(return_value=None)
        with self.assertRaises(LlmModelNotFoundError):
            await build_runner_for_agent(ctx, agent.id, model_id="missing")

    async def test_disabled_override_model_fails_closed(self) -> None:
        disabled = _llm("00000000-0000-4000-8000-000000000011", "gpt-b")
        disabled.enabled = False
        agent = _agent(model_id="00000000-0000-4000-8000-000000000010")
        ctx = _ctx()
        ctx.stores.agent.get_by_id = AsyncMock(return_value=agent)
        ctx.stores.llm_model.get_by_id = AsyncMock(return_value=disabled)

        with self.assertRaisesRegex(ProviderConfigurationError, "disabled"):
            await build_runner_for_agent(ctx, agent.id, model_id=disabled.id)
        ctx.stores.llm_configuration.get_provider.assert_not_awaited()

    async def test_existing_agent_with_disabled_default_model_fails_closed(self) -> None:
        disabled = _llm("00000000-0000-4000-8000-000000000010", "gpt-a")
        disabled.enabled = False
        agent = _agent(model_id=disabled.id)
        ctx = _ctx()
        ctx.stores.agent.get_by_id = AsyncMock(return_value=agent)
        ctx.stores.llm_model.get_by_id = AsyncMock(return_value=disabled)

        with self.assertRaisesRegex(ProviderConfigurationError, "disabled"):
            await build_runner_for_agent(ctx, agent.id)
        ctx.stores.llm_configuration.get_provider.assert_not_awaited()

    async def test_missing_agent_raises(self) -> None:
        ctx = _ctx()
        ctx.stores.agent.get_by_id = AsyncMock(return_value=None)
        with self.assertRaises(AgentNotFoundError):
            await build_runner_for_agent(ctx, "missing-agent")

    async def test_override_keeps_agent_prompt_and_capabilities(self) -> None:
        default = _llm("00000000-0000-4000-8000-000000000010", "gpt-a")
        override = _llm("00000000-0000-4000-8000-000000000011", "gpt-b")
        agent = _agent(model_id=default.id)
        agent.capabilities = ["builtin:general"]
        agent.skills = ["deepagent-diagnostics"]
        ctx = _ctx()
        ctx.stores.agent.get_by_id = AsyncMock(return_value=agent)
        ctx.stores.llm_model.get_by_id = AsyncMock(return_value=override)

        with patch("deep_agent.api.v1.deps._build_runner_from_spec", new_callable=AsyncMock) as build:
            build.return_value = object()
            await build_runner_for_agent(ctx, agent.id, model_id=override.id)
            spec = build.call_args.args[0]
            self.assertEqual(spec.model, "openai:gpt-b")
            self.assertEqual(spec.instructions, "You are helpful.")
            self.assertEqual(spec.capabilities, ["builtin:general"])
            self.assertEqual(spec.skills, ["deepagent-diagnostics"])


class ChatSessionModelColumnTests(unittest.TestCase):
    def test_chat_session_has_no_llm_model_id_column(self) -> None:
        cols = {c.key for c in ChatSession.__table__.columns}
        self.assertNotIn("llm_model_id", cols)


if __name__ == "__main__":
    unittest.main()
