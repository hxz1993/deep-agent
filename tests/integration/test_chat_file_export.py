from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
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


class ChatFileExportIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        stores, self._engine, session_factory = await make_sqlite_stores()
        model = await stores.llm_model.create_unique(
            LlmModelCreate(
                name="file-export-model",
                model="private:file-export-test",
                input_modalities=["text"],
                context_window_tokens=32_000,
                max_output_tokens=2_000,
            )
        )
        self.agent = await stores.agent.create_unique(
            AgentCreate(
                name="file-export-agent",
                model_id=model.id,
                prompt="按用户要求把文件发布到下载中心。",
                capabilities=[],
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

    def _model(self, args: dict[str, object]) -> tuple[FunctionModel, list[set[str]]]:
        calls = 0
        exposed: list[set[str]] = []

        async def stream(_messages, info):
            nonlocal calls
            exposed.append({tool.name for tool in info.function_tools})
            calls += 1
            if calls == 1:
                yield {
                    0: DeltaToolCall(
                        name="export_file_to_download_center",
                        json_args=json.dumps(args, ensure_ascii=False),
                        tool_call_id="file-export-call",
                    )
                }
            else:
                yield "文件已发布到下载中心"

        return FunctionModel(stream_function=stream), exposed

    async def _stream(
        self,
        *,
        body: dict,
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
                        headers={"app": "app001", "space": "app"},
                        json=body,
                    ) as response:
                        self.assertEqual(response.status_code, 200)
                        raw = "".join([chunk async for chunk in response.aiter_text()])
        return parse_sse_events(raw)

    async def test_generated_content_is_directly_disclosed_and_persisted(self) -> None:
        model, exposed = self._model(
            {
                "source": {"type": "content", "content": "# 测试报告"},
                "file_name": "generated-report.md",
            }
        )
        upload = AsyncMock(
            return_value=SimpleNamespace(id="generated-file", fileSize=15)
        )
        save = AsyncMock(return_value="download-record")
        with patch(
            "deep_agent.files.export_toolset.PlatformAPI",
            return_value=SimpleNamespace(
                file=SimpleNamespace(upload=upload, delete_file=AsyncMock())
            ),
        ), patch(
            "deep_agent.files.export_toolset.SpaceAPI",
            return_value=SimpleNamespace(
                business_record=SimpleNamespace(save=save)
            ),
        ):
            events = await self._stream(
                body={
                    "agent_id": self.agent.id,
                    "message": "导出Markdown报告",
                },
                model=model,
            )

        self.assertIn("export_file_to_download_center", exposed[0])
        self.assertEqual(
            next(item for item in events if item["type"] == "final")["output"],
            "文件已发布到下载中心",
        )
        session_id = events[0]["session_id"]
        await get_context().event_persister.flush(session_id, agent_id=self.agent.id)
        history = await get_context().store.get_history(
            session_id,
            agent_id=self.agent.id,
        )
        returns = [
            part
            for message in history
            for part in message.parts
            if isinstance(part, ToolReturnPart)
            and part.tool_name == "export_file_to_download_center"
        ]
        self.assertEqual(len(returns), 1)
        self.assertEqual(returns[0].content["download_record_id"], "download-record")

    async def test_existing_attachment_reuses_file_id_without_upload(self) -> None:
        model, _exposed = self._model(
            {
                "source": {"type": "platform_file", "file_id": "existing-file"},
                "file_name": "existing-report.html",
            }
        )
        save = AsyncMock(return_value="existing-download-record")
        with patch(
            "deep_agent.files.export_toolset._download_file",
            new_callable=AsyncMock,
            return_value=b"<h1>Existing report</h1>",
        ), patch("deep_agent.files.export_toolset.PlatformAPI") as platform, patch(
            "deep_agent.files.export_toolset.SpaceAPI",
            return_value=SimpleNamespace(
                business_record=SimpleNamespace(save=save)
            ),
        ):
            events = await self._stream(
                body={
                    "agent_id": self.agent.id,
                    "message": "把附件登记到下载中心",
                    "attachments": [
                        {
                            "id": "existing-file",
                            "name": "source.html",
                            "mime": "text/html",
                        }
                    ],
                },
                model=model,
            )

        platform.assert_not_called()
        self.assertEqual(save.await_args.args[0]["fileId"], "existing-file")
        self.assertEqual(
            next(item for item in events if item["type"] == "final")["output"],
            "文件已发布到下载中心",
        )


if __name__ == "__main__":
    unittest.main()
