from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from deep_agent.schemas.agent import AgentCreate
from deep_agent.schemas.llm_model import LlmModelCreate
from tests.helpers.request_identity import install_test_user_header
from tests.helpers.sqlite_stores import make_sqlite_stores
from tests.integration.helpers.chat import mock_request_disconnect, parse_sse_events


class ChatAttachmentToolIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        stores, self._engine, session_factory = await make_sqlite_stores()
        model = await stores.llm_model.create_unique(
            LlmModelCreate(
                name="attachment-model",
                model="private:attachment-test",
                input_modalities=["text"],
                context_window_tokens=32_000,
                max_output_tokens=2_000,
            )
        )
        self.agent = await stores.agent.create_unique(
            AgentCreate(
                name="attachment-agent",
                model_id=model.id,
                prompt="读取用户附件并回答。",
                capabilities=["builtin:attachment"],
            )
        )
        self.app = FastAPI()
        install_test_user_header(self.app)
        self.app.include_router(api_router)
        init_context(
            AppContext.for_testing(
                registry=build_registry(),
                stores=stores,
                mcp_lease_manager=McpLeaseManager(),
                db_engine=self._engine,
                db_session_factory=session_factory,
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
        await get_context().event_persister.aclose()
        reset_context()
        await self._engine.dispose()

    def _model(self, file_id: str) -> FunctionModel:
        calls = 0

        async def stream(_messages, _info):
            nonlocal calls
            calls += 1
            if calls == 1:
                yield {
                    0: DeltaToolCall(
                        name="read_text_attachment",
                        json_args=f'{{"file_id":"{file_id}","limit":2}}',
                        tool_call_id="attachment-read",
                    )
                }
            else:
                yield "已读取附件内容"

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

    @patch(
        "deep_agent.capabilities.attachments.builtin._download_file",
        new_callable=AsyncMock,
        return_value=b"first line\nsecond line\nthird line",
    )
    async def test_real_agent_tool_chain_persists_and_reuses_session_attachment(
        self,
        download: AsyncMock,
    ) -> None:
        file_id = "platform-text-1"
        events = await self._stream(
            {
                "agent_id": self.agent.id,
                "message": "读取附件",
                "attachments": [
                    {
                        "id": file_id,
                        "name": "note.txt",
                        "mime": "text/plain",
                    }
                ],
            },
            model=self._model(file_id),
        )
        self.assertTrue(any(item["type"] == "tool_call" for item in events))
        self.assertTrue(any(item["type"] == "tool_result" for item in events))
        final = next(item for item in events if item["type"] == "final")
        self.assertEqual(final["output"], "已读取附件内容")
        session_id = events[0]["session_id"]

        await get_context().event_persister.flush(
            session_id,
            agent_id=self.agent.id,
        )
        raw_history = await get_context().store.get_history(
            session_id,
            agent_id=self.agent.id,
        )
        self.assertTrue(
            any(
                isinstance(part, ToolReturnPart)
                and part.tool_name == "read_text_attachment"
                for message in raw_history
                for part in message.parts
            )
        )

        continued = await self._stream(
            {
                "agent_id": self.agent.id,
                "session_id": session_id,
                "message": "再读取一次",
            },
            model=self._model(file_id),
        )
        self.assertEqual(
            next(item for item in continued if item["type"] == "final")["output"],
            "已读取附件内容",
        )
        self.assertEqual(download.await_count, 2)


if __name__ == "__main__":
    unittest.main()
