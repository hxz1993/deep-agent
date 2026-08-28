from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic_ai import Agent
from pydantic_ai.capabilities import ToolSearch
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.usage import UsageLimits

from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.runner import AgentRunner
from deep_agent.agent.tool_discovery import (
    MCP_SEARCH_RESULT_LIMIT,
    build_mcp_discovery_toolset,
    search_deferred_tools,
)
from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from deep_agent.schemas.llm_model import LlmModelCreate
from tests.helpers.request_identity import install_test_user_header
from tests.helpers.sqlite_stores import (
    get_default_test_provider_id,
    make_sqlite_stores,
    seed_default_agent,
)
from tests.integration.helpers.chat import mock_request_disconnect, parse_sse_events
from tests.integration.helpers.models import text_only_model


def _context_ref(
    *,
    key: str = "current_selection",
    label: str | None = "销售预算!B2:C4",
) -> dict:
    return {
        "key": key,
        "type": "deeptable.selection",
        "schema_version": "2.0",
        "label": label,
        "payload": {"cells": [{"address": "B2", "value": 42}]},
        "metadata": {"source": "browser", "secret": "history-only"},
    }


def cube_query_data(cube_name: str, query: str) -> str:
    """查询财务模型 Cube 数据与期间趋势。"""
    return f"{cube_name}:{query}"


def deeptable_workbook_get(name: str) -> str:
    """读取电子表格页面和输入表工作簿。"""
    return name


def deeptable_dynamic_report_create(name: str) -> str:
    """为电子表格页面创建动态报表。"""
    return name


def deeptable_report_template_generate(name: str) -> str:
    """为电子表格输入表生成报表模板。"""
    return name


def platform_user_profile(user_id: str) -> str:
    """Read a platform user profile."""
    return user_id


def audit_log_list(category: str) -> str:
    """List recent audit log entries by category."""
    return category


def workflow_task_list(status: str) -> str:
    """List workflow tasks by status."""
    return status


def connector_source_get(name: str) -> str:
    """Read a connector data source."""
    return name


def variable_value_get(name: str) -> str:
    """Read a configured variable value."""
    return name


def dimension_member_list(dimension: str) -> str:
    """List members in a business dimension."""
    return dimension


def platform_menu_list(module: str) -> str:
    """List platform menus for a module."""
    return module


class ChatContextReferenceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        stores, self._engine, session_factory = await make_sqlite_stores()
        self._model = await stores.llm_configuration.create_model(
            LlmModelCreate(
                provider_id=await get_default_test_provider_id(stores),
                name="context-ref-model",
                model="private:context-ref-model",
                input_modalities=["text"],
                context_window_tokens=128_000,
                max_output_tokens=8_192,
            )
        )
        self._agent_id = await seed_default_agent(stores, name="context-ref-agent")
        self._app = FastAPI()
        install_test_user_header(self._app)
        self._app.include_router(api_router)
        init_context(
            AppContext.for_testing(
                registry=build_registry(),
                stores=stores,
                mcp_lease_manager=McpLeaseManager(),
                db_engine=self._engine,
                db_session_factory=session_factory,
            )
        )
        self._no_title = patch(
            "deep_agent.agent.runner.AgentRunner._generate_model_title",
            new_callable=AsyncMock,
        )
        self._no_title.start()
        self._transport = ASGITransport(app=self._app)

    async def asyncTearDown(self) -> None:
        self._no_title.stop()
        await get_context().event_persister.aclose()
        reset_context()
        await self._engine.dispose()

    async def _history(self, session_id: str) -> dict:
        await get_context().event_persister.flush(session_id)
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            response = await client.get(f"/api/v1/sessions/{session_id}/history")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _discovery_runner(self) -> AgentRunner:
        async def answer(_messages: list, _info: AgentInfo):
            yield "done"

        mcp = FunctionToolset(
            tools=[
                cube_query_data,
                deeptable_workbook_get,
                deeptable_dynamic_report_create,
                deeptable_report_template_generate,
                platform_user_profile,
                audit_log_list,
                workflow_task_list,
                connector_source_get,
                variable_value_get,
                dimension_member_list,
                platform_menu_list,
            ]
        ).prefixed("deepfos")
        discovery = build_mcp_discovery_toolset(
            [mcp],
            server_names=["deepfos"],
            estimator=TokenEstimator.conservative(),
        )
        assert discovery is not None
        agent = Agent(
            FunctionModel(stream_function=answer),
            deps_type=AgentDeps,
            toolsets=[discovery],
            capabilities=[
                ToolSearch(
                    strategy=search_deferred_tools,
                    max_results=MCP_SEARCH_RESULT_LIMIT,
                )
            ],
        )
        return AgentRunner(
            agent,
            get_context().store,
            UsageLimits(request_limit=5),
            estimator=TokenEstimator.conservative(),
        )

    async def test_stream_tool_preselection_uses_message_not_page_context_label(self) -> None:
        runner = self._discovery_runner()
        page_ref = {
            **_context_ref(label="电子表格输入表 B020101_copy"),
            "type": "deeptable.page",
        }
        with patch(
            "deep_agent.api.v1.chat.build_runner_for_agent",
            new_callable=AsyncMock,
            return_value=runner,
        ):
            with mock_request_disconnect():
                async with AsyncClient(
                    transport=self._transport,
                    base_url="http://test",
                    timeout=30.0,
                ) as client:
                    async with client.stream(
                        "POST",
                        "/api/v1/chat/stream",
                        json={
                            "agent_id": self._agent_id,
                            "message": "火星气象温度是多少？不要调用工具；不知道就说不知道。",
                            "context_refs": [page_ref],
                        },
                    ) as response:
                        self.assertEqual(response.status_code, 200)
                        raw = "".join([chunk async for chunk in response.aiter_text()])

        final = next(
            event for event in parse_sse_events(raw) if event.get("type") == "final"
        )
        discovery = final["context"]["tool_discovery"]
        self.assertEqual(discovery["preselected_tools"], [])

    async def test_stream_financial_question_ignores_page_label_for_preselection(self) -> None:
        page_ref = {
            **_context_ref(label="电子表格输入表 B020101_copy"),
            "type": "deeptable.range",
        }
        question = "2026年预算工作版下，当前财务模型1至3月已售房数有什么趋势？"
        observed: list[list[str]] = []
        for context_refs in (None, [page_ref]):
            with patch(
                "deep_agent.api.v1.chat.build_runner_for_agent",
                new_callable=AsyncMock,
                return_value=self._discovery_runner(),
            ):
                with mock_request_disconnect():
                    async with AsyncClient(
                        transport=self._transport,
                        base_url="http://test",
                        timeout=30.0,
                    ) as client:
                        body = {"agent_id": self._agent_id, "message": question}
                        if context_refs is not None:
                            body["context_refs"] = context_refs
                        async with client.stream(
                            "POST",
                            "/api/v1/chat/stream",
                            json=body,
                        ) as response:
                            self.assertEqual(response.status_code, 200)
                            raw = "".join([chunk async for chunk in response.aiter_text()])
            final = next(
                event for event in parse_sse_events(raw) if event.get("type") == "final"
            )
            observed.append(final["context"]["tool_discovery"]["preselected_tools"])

        self.assertIn("deepfos_cube_query_data", observed[0])
        self.assertEqual(observed[1], observed[0])

    async def test_context_only_stream_persists_structured_history_and_safe_title(self) -> None:
        with patch(
            "deep_agent.agent.factory._resolve_model",
            return_value=text_only_model("已分析当前选区"),
        ):
            with mock_request_disconnect():
                async with AsyncClient(
                    transport=self._transport,
                    base_url="http://test",
                    timeout=30.0,
                ) as client:
                    async with client.stream(
                        "POST",
                        "/api/v1/chat/stream",
                        json={
                            "agent_id": self._agent_id,
                            "model_id": self._model.id,
                            "capabilities": [],
                            "context_refs": [_context_ref()],
                        },
                    ) as response:
                        self.assertEqual(response.status_code, 200)
                        raw = "".join([chunk async for chunk in response.aiter_text()])
        events = parse_sse_events(raw)
        session_id = events[0]["session_id"]
        final = next(event for event in events if event.get("type") == "final")
        self.assertEqual(final["status"], "completed")

        history = await self._history(session_id)
        user = next(item for item in history["items"] if item["kind"] == "user_message")
        self.assertIsNone(user["text"])
        self.assertEqual(
            user["context_refs"],
            [{key: value for key, value in _context_ref().items() if key != "metadata"}],
        )
        self.assertNotIn("<context_references>", str(history))

        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            sessions = (await client.get("/api/v1/sessions")).json()
        item = next(row for row in sessions["items"] if row["session_id"] == session_id)
        self.assertEqual(item["user_prompt"], "[上下文] 销售预算!B2:C4")

    async def test_normal_chat_accepts_text_attachment_and_context_together(self) -> None:
        with patch(
            "deep_agent.agent.factory._resolve_model",
            return_value=text_only_model("ok"),
        ):
            async with AsyncClient(transport=self._transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/chat",
                    json={
                        "agent_id": self._agent_id,
                        "message": "结合附件解释选区",
                        "model_id": self._model.id,
                        "capabilities": [],
                        "attachments": [
                            {
                                "id": "file-1",
                                "name": "note.txt",
                                "mime": "text/plain",
                            }
                        ],
                        "context_refs": [_context_ref()],
                    },
                )
        self.assertEqual(response.status_code, 200)
        history = await self._history(response.json()["session_id"])
        user = next(item for item in history["items"] if item["kind"] == "user_message")
        self.assertEqual(user["text"], "结合附件解释选区")
        self.assertEqual(user["attachments"][0]["id"], "file-1")
        self.assertEqual(user["context_refs"][0]["key"], "current_selection")

    async def test_manual_rename_preserves_context_refs(self) -> None:
        with patch(
            "deep_agent.agent.factory._resolve_model",
            return_value=text_only_model("ok"),
        ):
            async with AsyncClient(transport=self._transport, base_url="http://test") as client:
                created = await client.post(
                    "/api/v1/chat",
                    json={
                        "agent_id": self._agent_id,
                        "model_id": self._model.id,
                        "capabilities": [],
                        "context_refs": [_context_ref()],
                    },
                )
        self.assertEqual(created.status_code, 200)
        session_id = created.json()["session_id"]
        before = await self._history(session_id)

        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            manual = await client.patch(
                f"/api/v1/sessions/{session_id}",
                json={"title": "手动选区分析"},
            )
        self.assertEqual(manual.status_code, 200)
        self.assertEqual(manual.json()["title"], "手动选区分析")
        self.assertEqual((await self._history(session_id))["items"], before["items"])

    async def test_deferred_results_and_context_refs_are_rejected_before_writing(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/chat/stream",
                json={
                    "session_id": "deferred-context-conflict",
                    "model_id": self._model.id,
                    "context_refs": [_context_ref()],
                    "deferred_tool_results": [
                        {"tool_call_id": "call-1", "result": {"ok": True}}
                    ],
                },
            )
        self.assertEqual(response.status_code, 422)
        self.assertIn("mutually exclusive", response.text)
        self.assertEqual(
            await get_context().store.get_history("deferred-context-conflict"),
            [],
        )

    async def test_duplicate_keys_are_rejected_by_openapi_schema(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/chat",
                json={
                    "model_id": self._model.id,
                    "context_refs": [_context_ref(), _context_ref()],
                },
            )
        self.assertEqual(response.status_code, 422)
        self.assertIn("keys must be unique", response.text)

    async def test_invalid_utf8_json_is_reported_as_422(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/chat",
                content=b"\xff",
                headers={"content-type": "application/json"},
            )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "invalid_json_encoding")

    async def test_provider_failure_keeps_the_raw_context_reference(self) -> None:
        async def fail_provider(_messages, _info):
            if False:  # pragma: no cover - keeps this function an async generator
                yield ""
            raise RuntimeError("provider unavailable")

        with patch(
            "deep_agent.agent.factory._resolve_model",
            return_value=FunctionModel(stream_function=fail_provider),
        ):
            with mock_request_disconnect():
                async with AsyncClient(
                    transport=self._transport,
                    base_url="http://test",
                    timeout=30.0,
                ) as client:
                    async with client.stream(
                        "POST",
                        "/api/v1/chat/stream",
                        json={
                            "agent_id": self._agent_id,
                            "model_id": self._model.id,
                            "capabilities": [],
                            "message": "分析失败后仍要保留引用",
                            "context_refs": [_context_ref()],
                        },
                    ) as response:
                        self.assertEqual(response.status_code, 200)
                        raw = "".join([chunk async for chunk in response.aiter_text()])

        events = parse_sse_events(raw)
        self.assertEqual(events[-1]["type"], "error")
        history = await self._history(events[0]["session_id"])
        user = next(item for item in history["items"] if item["kind"] == "user_message")
        self.assertEqual(
            user["context_refs"],
            [{key: value for key, value in _context_ref().items() if key != "metadata"}],
        )


if __name__ == "__main__":
    unittest.main()
