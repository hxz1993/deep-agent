from __future__ import annotations

import unittest

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from pydantic_ai.settings import ModelSettings

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.runner import AgentRunner
from pydantic_ai.usage import UsageLimits


class AgentRunnerModelSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_passes_merged_model_settings(self) -> None:
        captured: dict[str, object] = {}

        class _CapturingAgent(Agent):
            async def run(self, *args, **kwargs):  # type: ignore[override]
                captured["model_settings"] = kwargs.get("model_settings")
                return await super().run(*args, **kwargs)

        agent = _CapturingAgent(TestModel(), deps_type=AgentDeps)
        runner = AgentRunner(
            agent,
            store=_NoopStore(),
            usage_limits=UsageLimits(request_limit=5),
            model_settings=ModelSettings(max_tokens=2048),
        )
        await runner.run("sess", "hi", AgentDeps(session_id="sess"))
        settings = captured.get("model_settings")
        self.assertIsNotNone(settings)
        self.assertNotIn("temperature", settings)
        self.assertEqual(settings["max_tokens"], 2048)


class _NoopStore:
    async def get_history(self, session_id: str, *, agent_id: str | None = None):
        return []

    async def append(self, session_id: str, messages, **_kwargs):
        return None


if __name__ == "__main__":
    unittest.main()
