from __future__ import annotations

import json
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic_ai.messages import ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from deep_agent.sandbox.profile import (
    DEFAULT_SANDBOX_PROFILE,
    load_sandbox_profile,
    probe_sandbox_profile,
    resolve_sandbox_profile,
)
from deep_agent.sandbox import SubprocessBackend
from deep_agent.sandbox.types import (
    ExecuteRequest,
    ExecuteResult,
    SandboxError,
    TruncationMetadata,
)
from deep_agent.schemas.agent import AgentCreate
from deep_agent.schemas.llm_model import LlmModelCreate
from tests.helpers.request_identity import install_test_user_header
from tests.helpers.sandbox import sandbox_test_python
from tests.helpers.sqlite_stores import make_sqlite_stores
from tests.integration.helpers.chat import mock_request_disconnect, parse_sse_events


class UnavailableSandboxBackend:
    def __init__(self) -> None:
        self.requests: list[ExecuteRequest] = []
        self.health = AsyncMock(
            return_value=type(
                "Health",
                (),
                {
                    "enabled": True,
                    "backend": "unavailable",
                    "state_scope": "request",
                    "security_level": "none",
                    "production_ready": False,
                },
            )()
        )
        self.aclose = AsyncMock()

    async def execute(self, request: ExecuteRequest) -> ExecuteResult:
        self.requests.append(request)
        return ExecuteResult(
            request_id=request.request_id,
            status="error",
            exit_code=None,
            stdout="",
            stderr="",
            result=None,
            error=SandboxError(
                code="BACKEND_UNAVAILABLE",
                message="sandbox backend is unavailable",
            ),
            duration_ms=0,
            backend="unavailable",
            environment="test",
            security_level="none",
            memory_peak_bytes=None,
            truncated=TruncationMetadata(),
        )


class ChatPythonSandboxIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        stores, self._engine, session_factory = await make_sqlite_stores()
        model = await stores.llm_model.create_unique(
            LlmModelCreate(
                name="sandbox-model",
                model="private:sandbox-test",
                input_modalities=["text"],
                context_window_tokens=32_000,
                max_output_tokens=2_000,
            )
        )
        self.agent = await stores.agent.create_unique(
            AgentCreate(
                name="sandbox-agent",
                model_id=model.id,
                prompt="使用 Python 沙箱完成计算。",
                capabilities=["builtin:sandbox"],
            )
        )
        self.backend = SubprocessBackend(
            python_executable=sandbox_test_python(),
            max_concurrency=2,
        )
        manifest = load_sandbox_profile(DEFAULT_SANDBOX_PROFILE)
        probe = await probe_sandbox_profile(manifest, self.backend, timeout_ms=30_000)
        self.sandbox_profile = resolve_sandbox_profile(manifest, probe)
        self.app = FastAPI()
        install_test_user_header(self.app)
        self.app.include_router(api_router)
        init_context(
            AppContext.for_testing(
                registry=build_registry(
                    sandbox_backend=self.backend,
                    sandbox_profile=self.sandbox_profile,
                ),
                stores=stores,
                mcp_lease_manager=McpLeaseManager(),
                db_engine=self._engine,
                db_session_factory=session_factory,
                sandbox_backend=self.backend,
                sandbox_profile=self.sandbox_profile,
            )
        )
        self.transport = ASGITransport(app=self.app)
        self._no_title = patch(
            "deep_agent.agent.runner.AgentRunner._maybe_generate_title",
            new_callable=AsyncMock,
        )
        self._no_title.start()

    async def asyncTearDown(self) -> None:
        self._no_title.stop()
        await self.backend.aclose()
        await get_context().event_persister.aclose()
        reset_context()
        await self._engine.dispose()

    def _model(self, steps: list[dict[str, object]]) -> FunctionModel:
        calls = 0

        async def stream(_messages, _info):
            nonlocal calls
            step = steps[calls]
            calls += 1
            if "tool" in step:
                yield {
                    0: DeltaToolCall(
                        name=step["tool"],
                        json_args=json.dumps(step["args"], ensure_ascii=False),
                        tool_call_id=step.get("tool_call_id", f"sandbox-call-{calls}"),
                    )
                }
            else:
                yield str(step.get("text", "完成"))

        return FunctionModel(stream_function=stream)

    async def _stream(
        self,
        body: dict,
        *,
        model: FunctionModel,
    ) -> list[dict]:
        with patch("deep_agent.agent.factory._resolve_model", return_value=model):
            with mock_request_disconnect():
                async with AsyncClient(
                    transport=self.transport,
                    base_url="http://test",
                ) as client:
                    async with client.stream(
                        "POST",
                        "/api/v1/chat/stream",
                        json=body,
                    ) as response:
                        self.assertEqual(response.status_code, 200)
                        raw = "".join(
                            [chunk async for chunk in response.aiter_text()]
                        )
        return parse_sse_events(raw)

    async def test_execute_python_persists_tool_call_and_result(self) -> None:
        events = await self._stream(
            {
                "agent_id": self.agent.id,
                "message": "计算 1+1",
            },
            model=self._model(
                [
                    {
                        "tool": "execute_python",
                        "args": {"code": "1 + 1"},
                        "tool_call_id": "sandbox-add",
                    },
                    {"text": "结果是 2"},
                ]
            ),
        )
        self.assertTrue(any(item["type"] == "tool_call" for item in events))
        self.assertTrue(any(item["type"] == "tool_result" for item in events))
        session_id = events[0]["session_id"]

        await get_context().event_persister.flush(
            session_id,
            agent_id=self.agent.id,
        )
        raw_history = await get_context().store.get_history(
            session_id,
            agent_id=self.agent.id,
        )
        tool_calls = [
            part
            for message in raw_history
            for part in message.parts
            if isinstance(part, ToolCallPart) and part.tool_name == "execute_python"
        ]
        tool_returns = [
            part
            for message in raw_history
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == "execute_python"
        ]
        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(len(tool_returns), 1)
        self.assertEqual(tool_returns[0].content["result"], 2)

    async def test_second_call_cannot_see_first_call_variables(self) -> None:
        session_id = str(uuid.uuid4())
        first = await self._stream(
            {
                "agent_id": self.agent.id,
                "session_id": session_id,
                "message": "设置变量",
            },
            model=self._model(
                [
                    {
                        "tool": "execute_python",
                        "args": {"code": "session_var = 99"},
                        "tool_call_id": "sandbox-set",
                    },
                    {"text": "已设置"},
                ]
            ),
        )
        self.assertTrue(any(item["type"] == "tool_result" for item in first))

        second = await self._stream(
            {
                "agent_id": self.agent.id,
                "session_id": session_id,
                "message": "读取变量",
            },
            model=self._model(
                [
                    {
                        "tool": "execute_python",
                        "args": {"code": "session_var"},
                        "tool_call_id": "sandbox-get",
                    },
                    {"text": "变量不存在"},
                ]
            ),
        )
        tool_result = next(item for item in second if item["type"] == "tool_result")
        result = tool_result["result"]
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], "PYTHON_EXCEPTION")

    async def test_backend_unavailable_does_not_fallback_to_subprocess(self) -> None:
        unavailable = UnavailableSandboxBackend()
        stores = get_context().stores
        init_context(
            AppContext.for_testing(
                registry=build_registry(sandbox_backend=unavailable),
                stores=stores,
                mcp_lease_manager=get_context().mcp_lease_manager,
                db_engine=self._engine,
                db_session_factory=get_context().db_session_factory,
                sandbox_backend=unavailable,
            )
        )
        with patch.object(
            SubprocessBackend,
            "execute",
            new_callable=AsyncMock,
        ) as subprocess_execute:
            events = await self._stream(
                {
                    "agent_id": self.agent.id,
                    "message": "执行代码",
                },
                model=self._model(
                    [
                        {
                            "tool": "execute_python",
                            "args": {"code": "1 + 1"},
                            "tool_call_id": "sandbox-unavailable",
                        },
                        {"text": "失败"},
                    ]
                ),
            )

        subprocess_execute.assert_not_awaited()
        self.assertEqual(len(unavailable.requests), 1)
        tool_result = next(item for item in events if item["type"] == "tool_result")
        self.assertEqual(tool_result["result"]["error"]["code"], "BACKEND_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
