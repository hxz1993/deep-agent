from __future__ import annotations

import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from pydantic_ai.exceptions import UsageLimitExceeded

from deep_agent.agent.limits import format_stream_error
from deep_agent.agent.spec import AgentSpec
from deep_agent.api.v1 import api_router
from deep_agent.api.v1.deps import _build_runner_from_spec
from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.options import Agent as AgentOptions
from deep_agent.options import DeepAgentOptions, OPTION, validate_runtime_options
from deep_agent.schemas.agent import (
    LEGACY_MAX_REQUESTS_MESSAGE,
    AgentCreate,
    AgentResponse,
    AgentUpdate,
)
from tests.helpers.admin_auth import allow_console_admin
from tests.helpers.sqlite_stores import make_sqlite_stores_sync


class GlobalRequestLimitOptionTests(unittest.TestCase):
    def test_default_is_30(self) -> None:
        self.assertEqual(AgentOptions.max_requests.default, 30)

    def test_environment_can_override_default(self) -> None:
        previous_source = AgentOptions.max_requests.set_by
        try:
            with patch.dict(os.environ, {"DEEP_AGENT_MAX_REQUESTS": "41"}):
                options = DeepAgentOptions()
                self.assertEqual(options.agent.max_requests, 41)
                self.assertEqual(AgentOptions.max_requests.set_by, "DEEP_AGENT_MAX_REQUESTS")
        finally:
            AgentOptions.max_requests.set_by = previous_source

    def test_non_positive_value_is_rejected(self) -> None:
        options = DeepAgentOptions()
        with self.assertRaisesRegex(Exception, "positive integer"):
            options.agent.max_requests = 0
        with self.assertRaisesRegex(Exception, "positive integer"):
            options.agent.max_requests = -1

    def test_startup_validation_rejects_invalid_environment_value(self) -> None:
        with patch.dict(os.environ, {"DEEP_AGENT_MAX_REQUESTS": "0"}):
            with self.assertRaisesRegex(Exception, "positive integer"):
                validate_runtime_options(DeepAgentOptions())


class AgentRequestLimitSchemaTests(unittest.TestCase):
    def test_agent_schemas_no_longer_publish_max_requests(self) -> None:
        self.assertNotIn("max_requests", AgentCreate.model_json_schema()["properties"])
        self.assertNotIn("max_requests", AgentUpdate.model_json_schema()["properties"])
        self.assertNotIn("max_requests", AgentResponse.model_json_schema()["properties"])

    def test_legacy_create_and_update_payloads_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, LEGACY_MAX_REQUESTS_MESSAGE):
            AgentCreate(
                name="legacy",
                model_id="00000000-0000-4000-8000-000000000001",
                prompt="legacy",
                max_requests=12,
            )
        with self.assertRaisesRegex(ValidationError, LEGACY_MAX_REQUESTS_MESSAGE):
            AgentUpdate.model_validate({"max_requests": None})


class AgentRequestLimitApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stores, self.engine, session_factory = make_sqlite_stores_sync()
        init_context(
            AppContext.for_testing(
                stores=self.stores,
                db_engine=self.engine,
                db_session_factory=session_factory,
            )
        )
        app = FastAPI()
        app.include_router(api_router)
        allow_console_admin(app)
        self.client = TestClient(app)
        self.model_id = asyncio.run(self.stores.llm_model.list_all())[0].id

    def tearDown(self) -> None:
        reset_context()
        asyncio.run(self.engine.dispose())

    def test_crud_response_omits_agent_request_limit(self) -> None:
        response = self.client.post(
            "/api/v1/agents",
            json={
                "name": "global-limit-agent",
                "model_id": self.model_id,
                "prompt": "helpful",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertNotIn("max_requests", response.json())

        fetched = self.client.get(f"/api/v1/agents/{response.json()['id']}")
        self.assertEqual(fetched.status_code, 200, fetched.text)
        self.assertNotIn("max_requests", fetched.json())

    def test_legacy_create_and_update_requests_return_422(self) -> None:
        create = self.client.post(
            "/api/v1/agents",
            json={
                "name": "legacy-limit-agent",
                "model_id": self.model_id,
                "prompt": "legacy",
                "max_requests": 12,
            },
        )
        self.assertEqual(create.status_code, 422)
        self.assertIn(LEGACY_MAX_REQUESTS_MESSAGE, create.text)

        valid = self.client.post(
            "/api/v1/agents",
            json={
                "name": "valid-agent",
                "model_id": self.model_id,
                "prompt": "valid",
            },
        )
        self.assertEqual(valid.status_code, 201, valid.text)
        update = self.client.patch(
            f"/api/v1/agents/{valid.json()['id']}",
            json={"max_requests": None},
        )
        self.assertEqual(update.status_code, 422)
        self.assertIn(LEGACY_MAX_REQUESTS_MESSAGE, update.text)


class AgentRunnerGlobalRequestLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_runner_uses_only_global_request_limit(self) -> None:
        spec = AgentSpec(name="test", model="test", instructions="helpful")
        ctx = SimpleNamespace(
            stores=MagicMock(),
            registry=MagicMock(),
            store=MagicMock(),
            skill_runtime=MagicMock(),
        )
        previous_limit = OPTION.agent.max_requests
        OPTION.agent.max_requests = 37
        try:
            with (
                patch(
                    "deep_agent.api.v1.deps.load_mcp_rows",
                    new=AsyncMock(return_value=[]),
                ),
                patch(
                    "deep_agent.api.v1.deps.prepare_ui_tool_packages_for_spec",
                    new=AsyncMock(return_value=([], {})),
                ),
                patch(
                    "deep_agent.api.v1.deps.resolve_mcp_toolsets",
                    new=AsyncMock(return_value=[]),
                ),
                patch(
                    "deep_agent.api.v1.deps.build_mcp_resource_toolset",
                    return_value=None,
                ),
                patch(
                    "deep_agent.api.v1.deps.augment_instructions_with_capabilities",
                    return_value="helpful",
                ),
                patch("deep_agent.api.v1.deps.build_agent", return_value=MagicMock()),
            ):
                runner = await _build_runner_from_spec(spec, ctx)
        finally:
            OPTION.agent.max_requests = previous_limit

        self.assertEqual(runner.usage_limits.request_limit, 37)


class UsageLimitErrorTests(unittest.TestCase):
    def test_format_stream_error_request_limit(self) -> None:
        exc = UsageLimitExceeded("The next request would exceed the request_limit of 30")
        self.assertEqual(
            format_stream_error(exc),
            "本轮对话模型请求次数已达上限（30 次），请简化问题或开启新对话后重试。",
        )

    def test_format_stream_error_total_tokens_limit(self) -> None:
        exc = UsageLimitExceeded(
            "The next request would exceed the total_tokens_limit of 1000000 (total_tokens=1000001)"
        )
        self.assertEqual(
            format_stream_error(exc),
            "本轮对话 token 用量已达上限（1000000），请缩短上下文或开启新对话后重试。",
        )

    def test_format_stream_error_passthrough(self) -> None:
        self.assertEqual(format_stream_error(RuntimeError("boom")), "boom")


if __name__ == "__main__":
    unittest.main()
