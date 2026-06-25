from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from deep_agent.agent.spec import AgentSpec, default_spec
from deep_agent.api.v1.deps import _model_settings_for_spec, build_runner
from deep_agent.core.exceptions import LlmModelNotFoundError
from deep_agent.db.models import LlmModel
from pydantic_ai.settings import ModelSettings


class ModelSettingsHelperTests(unittest.TestCase):
    def test_none_when_temperature_unset(self) -> None:
        self.assertIsNone(_model_settings_for_spec(AgentSpec(name="t", model="x", instructions="")))

    def test_builds_settings_when_temperature_set(self) -> None:
        spec = AgentSpec(name="t", model="x", instructions="", temperature=0.7)
        settings = _model_settings_for_spec(spec)
        self.assertIsNotNone(settings)
        self.assertEqual(settings["temperature"], 0.7)


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


class DefaultSpecTests(unittest.TestCase):
    def test_default_spec_accepts_temperature(self) -> None:
        spec = default_spec(temperature=1.0)
        self.assertEqual(spec.temperature, 1.0)


if __name__ == "__main__":
    unittest.main()
