from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from deep_agent.agent.spec import AgentSpec, DEFAULT_INSTRUCTIONS
from deep_agent.api.v1.deps import _model_settings_for_spec, build_runner
from deep_agent.core.exceptions import LlmModelNotFoundError
from deep_agent.db.models import LlmModel
from pydantic_ai.settings import ModelSettings


class ModelSettingsHelperTests(unittest.TestCase):
    def test_none_when_temperature_and_output_limit_unset(self) -> None:
        self.assertIsNone(
            _model_settings_for_spec(AgentSpec(name="t", model="x", instructions=""))
        )

    def test_builds_settings_with_temperature_only(self) -> None:
        spec = AgentSpec(name="t", model="x", instructions="", temperature=0.7)
        settings = _model_settings_for_spec(spec)
        self.assertIsNotNone(settings)
        self.assertEqual(settings["temperature"], 0.7)
        self.assertNotIn("max_tokens", settings)

    def test_builds_settings_with_output_limit_only(self) -> None:
        spec = AgentSpec(
            name="t",
            model="x",
            instructions="",
            max_output_tokens=2048,
        )
        settings = _model_settings_for_spec(spec)
        self.assertIsNotNone(settings)
        self.assertEqual(settings["max_tokens"], 2048)
        self.assertNotIn("temperature", settings)

    def test_merges_temperature_and_output_limit(self) -> None:
        spec = AgentSpec(
            name="t",
            model="x",
            instructions="",
            temperature=0.4,
            max_output_tokens=4096,
        )
        settings = _model_settings_for_spec(spec)
        self.assertEqual(
            settings,
            ModelSettings(temperature=0.4, max_tokens=4096),
        )


class BuildRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_id_loads_llm_preset(self) -> None:
        llm = LlmModel(id="00000000-0000-4000-8000-000000000099", name="m", model="openai:gpt-4o")
        ctx = AsyncMock()
        ctx.stores.llm_model.get_by_id = AsyncMock(return_value=llm)

        with patch("deep_agent.api.v1.deps._build_runner_from_spec", new_callable=AsyncMock) as build:
            build.return_value = object()
            await build_runner(ctx, model_id=llm.id, temperature=0.5)
            spec = build.call_args.args[0]
            self.assertEqual(spec.model, "openai:gpt-4o")
            self.assertEqual(spec.temperature, 0.5)

    async def test_missing_model_id_raises(self) -> None:
        ctx = AsyncMock()
        ctx.stores.llm_model.get_by_id = AsyncMock(return_value=None)
        with self.assertRaises(LlmModelNotFoundError):
            await build_runner(ctx, model_id="missing")

    async def test_model_override_uses_default_instructions(self) -> None:
        ctx = AsyncMock()
        ctx.stores.llm_model.list_all = AsyncMock(return_value=[])

        with patch("deep_agent.api.v1.deps._build_runner_from_spec", new_callable=AsyncMock) as build:
            build.return_value = object()
            await build_runner(ctx, model="anthropic:claude-sonnet-4-5", temperature=1.0)
            spec = build.call_args.args[0]
            self.assertEqual(spec.model, "anthropic:claude-sonnet-4-5")
            self.assertEqual(spec.instructions, DEFAULT_INSTRUCTIONS)
            self.assertEqual(spec.temperature, 1.0)

    async def test_no_model_uses_first_llm_preset(self) -> None:
        llm = LlmModel(id="00000000-0000-4000-8000-000000000099", name="m", model="openai:gpt-4o")
        ctx = AsyncMock()
        ctx.stores.llm_model.list_all = AsyncMock(return_value=[llm])

        with patch("deep_agent.api.v1.deps._build_runner_from_spec", new_callable=AsyncMock) as build:
            build.return_value = object()
            await build_runner(ctx)
            spec = build.call_args.args[0]
            self.assertEqual(spec.model, "openai:gpt-4o")

    async def test_no_model_and_empty_llm_table_raises(self) -> None:
        ctx = AsyncMock()
        ctx.stores.llm_model.list_all = AsyncMock(return_value=[])
        with self.assertRaises(LlmModelNotFoundError):
            await build_runner(ctx)


if __name__ == "__main__":
    unittest.main()
