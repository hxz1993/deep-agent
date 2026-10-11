"""Real HTTP/SSE/Runner/Event Store with controlled OCR and real local files."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic_ai.messages import ToolReturnPart, RetryPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from deep_agent.files.storage import LocalFileStore
from deep_agent.schemas.agent import AgentCreate
from deep_agent.schemas.llm_model import LlmModelCreate
from tests.helpers.request_identity import install_test_user_header, TEST_USER_ID
from tests.helpers.sqlite_stores import make_sqlite_stores, get_default_test_provider_id
from tests.integration.helpers.chat import mock_request_disconnect, parse_sse_events
from tests.unit.test_document_layout_tool import original
from tests.unit.test_document_tools import pdf


class EvidenceHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        stores, self.engine, factory = await make_sqlite_stores()
        self.directory = tempfile.TemporaryDirectory(prefix="TMP_to_delete_evidence_history_")
        self.files = LocalFileStore(Path(self.directory.name))
        await self.files.initialize()
        model = await stores.llm_configuration.create_model(LlmModelCreate(
            provider_id=await get_default_test_provider_id(stores), name="evidence-model", model="test",
            context_window_tokens=128000, max_output_tokens=2000, input_modalities=["text"]))
        self.agent = await stores.agent.create_unique(AgentCreate(name="evidence-agent", model_id=model.id,
            prompt="Read and cite evidence", capabilities=["builtin:document"]))
        await stores.global_setting.upsert("document_parsing", value={"mode": "provider", "provider": "glm", "api_key": "controlled-key"}, version=1)
        init_context(AppContext.for_testing(registry=build_registry(), stores=stores,
            mcp_lease_manager=McpLeaseManager(), db_engine=self.engine, db_session_factory=factory, file_store=self.files))
        self.app = FastAPI()
        install_test_user_header(self.app)
        self.app.include_router(api_router)
        self.transport = ASGITransport(app=self.app)

    async def asyncTearDown(self):
        await get_context().event_persister.aclose()
        reset_context()
        await self.engine.dispose()
        self.directory.cleanup()

    async def stream(self, body, model):
        with patch("deep_agent.agent.factory._resolve_model", return_value=model), \
             patch("deep_agent.agent.runner.AgentRunner._generate_model_title", new_callable=AsyncMock), mock_request_disconnect():
            async with AsyncClient(transport=self.transport, base_url="http://test") as client:
                response = await client.post("/api/v1/chat/stream", json=body)
                self.assertEqual(response.status_code, 200, response.text)
                return parse_sse_events(response.text)

    async def test_layout_export_continuations_history_and_next_run_file_authority(self):
        observed = []
        async def model(messages, info):
            returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart) and p.tool_name == "parse_document"]
            if not returns:
                args = {"resource": {"type": "attachment", "file_id": "input-pdf"},
                        "include_layout": True, "export_file": True, "limit": 1}
            else:
                value = returns[-1].content
                self.assertEqual(value["export"]["status"], "saved")
                observed.append(value)
                if value["has_more"]:
                    args = value["next_read"]
                else:
                    yield "完成；证据文件 " + value["export"]["file"]["file_id"]
                    return
            yield {0: DeltaToolCall(name="parse_document", tool_call_id=f"parse-{len(returns)}", json_args=json.dumps(args))}

        with patch("deep_agent.files.toolset._download_file", AsyncMock(return_value=pdf(4))), \
             patch("deep_agent.documents.toolset.parse_with_provider", AsyncMock(return_value=original())) as parser, \
             patch.object(self.files, "upload", wraps=self.files.upload) as upload:
            events = await self.stream({"agent_id": self.agent.id, "message": "解析并导出", "attachments": [
                {"id": "input-pdf", "name": "sample.pdf", "mime": "application/pdf"}]}, FunctionModel(stream_function=model))
        parser.assert_awaited_once()
        upload.assert_awaited_once()  # no generic overflow spill, even with export metadata
        self.assertEqual(len(observed), 2)
        ref = observed[0]["export"]["file"]
        self.assertEqual(observed[-1]["export"]["file"], ref)
        session_id = events[0]["session_id"]
        await get_context().event_persister.flush(session_id, agent_id=self.agent.id)
        history = await get_context().store.get_history(session_id, agent_id=self.agent.id)
        parts = [p for m in history for p in m.parts if isinstance(p, ToolReturnPart) and p.tool_name == "parse_document"]
        self.assertEqual([p.content for p in parts], observed)
        self.assertTrue(all(p.metadata["document_evidence_file"]["file_id"] == ref["file_id"] for p in parts))
        async with AsyncClient(transport=self.transport, base_url="http://test") as client:
            for view in ("history", "events"):
                response = await client.get(f"/api/v1/sessions/{session_id}/{view}", params={"agent_id": self.agent.id})
                self.assertEqual(response.status_code, 200)
                self.assertIn(ref["file_id"], response.text)
                self.assertIn("block_id", response.text)
        data = await self.files.download_bounded(ref["file_id"], {"user": TEST_USER_ID}, max_bytes=ref["byte_size"])
        self.assertEqual(hashlib.sha256(data).hexdigest(), ref["sha256"])
        self.assertEqual(json.loads(data)["document_id"], observed[0]["document"]["document_id"])

        # A later Run cannot reuse the old temporary document cache. The saved
        # file remains downloadable independently via the existing FileStore.
        async def next_model(messages, info):
            returns = [p for m in messages for p in m.parts if isinstance(p, RetryPromptPart)]
            if returns:
                self.assertIn("不属于当前 Run", str(returns[-1].content))
                yield "旧解析引用不可跨Run续读，已导出的文件仍可下载"
            else:
                yield {0: DeltaToolCall(name="parse_document", tool_call_id="old-document", json_args=json.dumps({
                    "resource": observed[0]["document"], "include_layout": True}))}
        followup = await self.stream({"agent_id": self.agent.id, "session_id": session_id, "message": "按旧解析引用继续读取"},
                                     FunctionModel(stream_function=next_model))
        self.assertEqual(next(e for e in followup if e["type"] == "final")["output"], "旧解析引用不可跨Run续读，已导出的文件仍可下载")
