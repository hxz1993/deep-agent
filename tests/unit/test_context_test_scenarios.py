from __future__ import annotations

import asyncio
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolReturnPart

from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.context.test_scenarios import (
    CONTEXT_TEST_SCENARIOS,
    build_context_test_history,
)
from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from deep_agent.db.serialization import deserialize_message, serialize_message
from deep_agent.options import OPTION
from deep_agent.schemas.agent import AgentCreate
from tests.helpers.sqlite_stores import make_sqlite_stores_sync


class ContextTestScenarioBuilderTests(unittest.TestCase):
    def test_scenarios_build_pure_serializable_dialogue_above_trigger(self) -> None:
        estimator = TokenEstimator.conservative()
        for scenario in CONTEXT_TEST_SCENARIOS:
            with self.subTest(scenario=scenario.key):
                built = build_context_test_history(
                    scenario,
                    context_window_tokens=16_000,
                    max_output_tokens=2_000,
                    estimator=estimator,
                )
                self.assertGreater(
                    built.budget.estimated_request_tokens,
                    built.budget.trigger_tokens,
                )
                self.assertTrue(
                    all(
                        isinstance(message, (ModelRequest, ModelResponse))
                        for message in built.messages
                    )
                )
                self.assertFalse(
                    any(
                        isinstance(part, ToolReturnPart)
                        for message in built.messages
                        for part in message.parts
                    )
                )
                for message in built.messages:
                    self.assertEqual(
                        deserialize_message(serialize_message(message)),
                        message,
                    )


class ContextTestScenarioApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._enabled = OPTION.agent.context_test_scenarios_enabled
        self.user_id = "context-tester"
        self.stores, self.engine, self.session_factory = make_sqlite_stores_sync()
        app = FastAPI()
        app.include_router(api_router)
        init_context(
            AppContext.for_testing(
                registry=build_registry(),
                stores=self.stores,
                mcp_lease_manager=McpLeaseManager(),
                db_engine=self.engine,
                db_session_factory=self.session_factory,
            )
        )
        self.agent_id = asyncio.run(self._seed_agent())
        self.client = TestClient(app, headers={"user": self.user_id})

    def tearDown(self) -> None:
        OPTION.agent.context_test_scenarios_enabled = self._enabled
        reset_context()
        asyncio.run(self.engine.dispose())

    async def _seed_agent(self) -> str:
        model = (await self.stores.llm_model.list_all())[0]
        agent = await self.stores.agent.create_unique(
            AgentCreate(
                name="context-test-agent",
                model_id=model.id,
                prompt="用于上下文压缩测试",
                capabilities=[],
            )
        )
        return agent.id

    def test_disabled_endpoint_is_not_exposed(self) -> None:
        OPTION.agent.context_test_scenarios_enabled = False
        response = self.client.get(
            "/api/v1/context-test-scenarios",
            params={"agent_id": self.agent_id},
        )
        self.assertEqual(response.status_code, 404)

    def test_create_scenario_writes_owned_schema_valid_history(self) -> None:
        OPTION.agent.context_test_scenarios_enabled = True
        listing = self.client.get(
            "/api/v1/context-test-scenarios",
            params={"agent_id": self.agent_id},
        )
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(len(listing.json()["scenarios"]), 3)

        response = self.client.post(
            "/api/v1/context-test-scenarios/long_chinese_summary",
            json={"agent_id": self.agent_id},
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        session_id = payload["session"]["session_id"]
        self.assertTrue(session_id.startswith("context-test-long_chinese_summary-"))
        self.assertGreater(
            payload["estimated_history_tokens"],
            payload["trigger_tokens"],
        )

        owner = asyncio.run(
            get_context().store.get_session_owner(
                session_id,
                agent_id=self.agent_id,
            )
        )
        self.assertEqual(owner.user_id, self.user_id)
        history = asyncio.run(
            get_context().store.get_history(
                session_id,
                agent_id=self.agent_id,
            )
        )
        self.assertEqual(len(history), payload["session"]["message_count"])
        self.assertFalse(
            any(
                isinstance(part, ToolReturnPart)
                for message in history
                for part in message.parts
            )
        )


if __name__ == "__main__":
    unittest.main()
