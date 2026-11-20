from __future__ import annotations

import json
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic_ai import FunctionToolset, ToolReturn
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel

from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.capabilities.registry import ToolPackage
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from deep_agent.files.storage import LocalFileStore
from deep_agent.schemas.agent import AgentUpdate
from deepfos_platform.client.platform import FILE_TYPE_ATTACHMENT
from tests.helpers.request_identity import TEST_USER_ID, install_test_user_header
from tests.helpers.sqlite_stores import (
    make_sqlite_stores,
    seed_default_agent,
)
from tests.integration.helpers.chat import mock_request_disconnect, parse_sse_events
from tests.helpers.models import build_slow_stream_model, text_only_model, tool_call_model

_CHAT_JSON = {"capabilities": []}
_CONFIRM_CAPABILITIES = ["ui:builtin"]


def _assistant_texts(history: dict) -> list[str]:
    return [
        item["text"]
        for item in history.get("items") or []
        if item.get("kind") == "assistant_message" and item.get("text")
    ]


def _streamed_text(events: list[dict]) -> str:
    return "".join(ev.get("delta") or "" for ev in events if ev.get("type") == "text")


def _assistant_messages(history: dict) -> list[dict]:
    return [
        item
        for item in history.get("items") or []
        if item.get("kind") == "assistant_message"
    ]


def _message_items(history: dict) -> list[dict]:
    return [
        item
        for item in history.get("items") or []
        if item.get("kind") in {"user_message", "assistant_message"}
    ]


def _tool_call_ids(history: dict) -> list[str]:
    return [
        item["call_id"]
        for item in history.get("items") or []
        if item.get("kind") in {"tool", "plan"} and item.get("call_id")
    ]


class ChatHistoryPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        stores, self._engine, session_factory = await make_sqlite_stores()
        self._agent_id = await seed_default_agent(stores, name="history-agent")
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

    async def _stream_events(self, *, model: TestModel, json_body: dict) -> list[dict]:
        with patch("deep_agent.agent.factory._resolve_model", return_value=model):
            with mock_request_disconnect():
                async with AsyncClient(
                    transport=self._transport,
                    base_url="http://test",
                    timeout=30.0,
                ) as client:
                    async with client.stream(
                        "POST",
                        "/api/v1/chat/stream",
                        json={**_CHAT_JSON, "agent_id": self._agent_id, **json_body},
                    ) as response:
                        self.assertEqual(response.status_code, 200)
                        body = "".join([chunk async for chunk in response.aiter_text()])
        return parse_sse_events(body)

    async def _history(self, session_id: str) -> dict:
        await get_context().event_persister.flush(session_id)
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/sessions/{session_id}/history")
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    async def test_http_stream_complete_history_matches_final_output(self) -> None:
        reply = "exact integration reply"
        events = await self._stream_events(
            model=text_only_model(reply),
            json_body={"message": "hello"},
        )
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["output"], reply)

        session_id = events[0]["session_id"]
        data = await self._history(session_id)
        self.assertGreaterEqual(len(_message_items(data)), 2)
        self.assertEqual(_assistant_texts(data), [reply])

    async def test_http_sync_chat_persists_assistant(self) -> None:
        reply = "sync path reply"
        with patch("deep_agent.agent.factory._resolve_model", return_value=text_only_model(reply)):
            with mock_request_disconnect():
                async with AsyncClient(transport=self._transport, base_url="http://test") as client:
                    resp = await client.post(
                        "/api/v1/chat",
                        json={
                            **_CHAT_JSON,
                            "agent_id": self._agent_id,
                            "message": "sync hello",
                        },
                    )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["output"], reply)

        data = await self._history(body["session_id"])
        self.assertGreaterEqual(len(_message_items(data)), 2)
        self.assertEqual(_assistant_texts(data), [reply])

    async def _local_file_store(self):
        ctx = get_context()
        directory = TemporaryDirectory(prefix="TMP_to_delete_present_files_")
        self.addCleanup(directory.cleanup)
        ctx.file_store = LocalFileStore(directory.name)
        await ctx.file_store.initialize()
        return ctx.file_store

    async def _register_file_fixture(self):
        store = await self._local_file_store()
        tools = FunctionToolset()
        files = []

        @tools.tool_plain
        async def generate_reports() -> ToolReturn:
            for index in range(12):
                name = f"report-{index}.csv"
                data = b"total\n42\n"
                file = await store.upload(
                    file_type=FILE_TYPE_ATTACHMENT, file_name=name, mime="text/csv",
                    data=data, request_headers={"user": TEST_USER_ID},
                )
                files.append({"file_id": file.id, "file_name": name, "mime": "text/csv", "byte_size": len(data)})
            return ToolReturn(files, metadata={"generated_files": files})

        get_context().registry.register(ToolPackage("builtin:present-fixture", "builtin", tools))
        return files

    async def test_present_is_injected_before_any_files_and_preserves_direct_chat_outputs(self):
        files = await self._register_file_fixture()
        for mode in ("plain", "structured", "allowed_text"):
            with self.subTest(mode=mode):
                if mode != "plain":
                    await get_context().stores.agent.update_unique(self._agent_id, AgentUpdate(
                        structured_output_enabled=True,
                        allow_text_output=mode == "allowed_text",
                        output_config={"name": "report", "json_schema": {
                            "type": "object", "properties": {"number": {"type": "integer"}},
                            "required": ["number"], "additionalProperties": False,
                        }},
                    ), user_id=TEST_USER_ID)
                calls = 0
                offset = len(files)

                def respond(_messages, info):
                    nonlocal calls
                    calls += 1
                    names = {tool.name for tool in info.function_tools}
                    self.assertIn("present", names)
                    if calls == 1:
                        part = ToolCallPart("present", {"file_ids": ["invented-file-id"]}, "reject-id")
                    elif calls == 2:
                        part = ToolCallPart("generate_reports", {}, "generate")
                    elif calls == 3:
                        file_id = files[offset + 4]["file_id"]
                        part = ToolCallPart("present", {"file_ids": [file_id, file_id]}, "publish")
                    elif calls == 4:
                        part = (ToolCallPart(info.output_tools[0].name, {"number": 42}, "business")
                                if mode == "structured" else TextPart("完整报告已交付。"))
                    else:
                        self.fail("Unexpected model request")
                    return ModelResponse(parts=[part])

                async def stream(messages, info):
                    part = respond(messages, info).parts[0]
                    if isinstance(part, TextPart):
                        yield part.content
                    else:
                        yield {0: DeltaToolCall(name=part.tool_name, json_args=json.dumps(part.args),
                                                tool_call_id=part.tool_call_id)}

                model = FunctionModel(respond, stream_function=stream)
                prompt = {"message": "生成报告，交付正式的一份", "capabilities": ["builtin:present-fixture"]}
                if mode == "structured":
                    with patch("deep_agent.agent.factory._resolve_model", return_value=model), mock_request_disconnect():
                        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
                            response = await client.post("/api/v1/chat", json={**prompt, "agent_id": self._agent_id})
                    self.assertEqual(response.status_code, 200, response.text)
                    reply = response.json()
                    self.assertEqual(reply["output"], {"number": 42})
                    self.assertEqual(reply["output_info"]["name"], "report")
                    session_id = reply["session_id"]
                    kind = "structured_output"
                else:
                    events = await self._stream_events(model=model, json_body=prompt)
                    reply = next(event for event in events if event.get("type") == "final")
                    self.assertEqual(_streamed_text(events), "完整报告已交付。")
                    self.assertTrue(all("output_files" not in event for event in events if event is not reply))
                    session_id = events[0]["session_id"]
                    kind = "assistant_message"
                self.assertEqual(reply["status"], "completed")
                self.assertEqual([file["id"] for file in reply["output_files"]], [files[offset + 4]["file_id"]])
                self.assertEqual(reply["output_files"][0]["name"], "report-4.csv")
                self.assertIn("/console/file-preview-local.html?", reply["output_files"][0]["preview_url"])
                self.assertNotIn("_output_file_scope", reply)
                async with AsyncClient(transport=self._transport, base_url="http://test") as client:
                    download = await client.get(f'/api/v1/local-files/{files[offset + 4]["file_id"]}/download')
                self.assertEqual(download.status_code, 200)
                self.assertEqual(download.content, b"total\n42\n")
                history = await self._history(session_id)
                deliveries = [item for item in history["items"] if item.get("output_files")]
                self.assertEqual([item["kind"] for item in deliveries], [kind])
                self.assertEqual(deliveries[0]["output_files"], reply["output_files"])
                if mode == "plain":
                    async def use_saved_file(messages, _info):
                        if any(getattr(part, "tool_call_id", None) == "reuse"
                               for message in messages for part in message.parts):
                            yield "再次交付这份报告。"
                        else:
                            yield {0: DeltaToolCall(
                                name="present", json_args=json.dumps({"file_ids": [files[offset + 4]["file_id"]]}),
                                tool_call_id="reuse",
                            )}

                    followup = await self._stream_events(
                        model=FunctionModel(stream_function=use_saved_file),
                        json_body={"session_id": session_id, "message": "再给我同一份报告"},
                    )
                    reused = next(event for event in followup if event.get("type") == "final")
                    self.assertEqual(reused["output_files"], reply["output_files"])

    async def test_present_can_deliver_user_attachment_without_a_file_generation_tool(self):
        store = await self._local_file_store()
        original = b"total\n42\n"
        owned = await store.upload(
            file_type=FILE_TYPE_ATTACHMENT, file_name="input.csv", mime="text/csv",
            data=original, request_headers={"user": TEST_USER_ID},
        )
        private = await store.upload(
            file_type=FILE_TYPE_ATTACHMENT, file_name="private.csv", mime="text/csv",
            data=b"private", request_headers={"user": "another-user"},
        )
        calls = 0

        async def stream(_messages, info):
            nonlocal calls
            calls += 1
            self.assertIn("present", {tool.name for tool in info.function_tools})
            if calls <= 2:
                file_ids = [owned.id, private.id] if calls == 1 else [owned.id]
                yield {0: DeltaToolCall(name="present", json_args=json.dumps({"file_ids": file_ids}),
                                        tool_call_id=f"present-{calls}")}
            else:
                yield "原文件在下面。"

        events = await self._stream_events(model=FunctionModel(stream_function=stream), json_body={
            "message": "把上传的 CSV 原样给我看",
            "attachments": [{"id": file.id, "name": file.file_name, "mime": file.mime} for file in (owned, private)],
        })
        final = next(event for event in events if event.get("type") == "final")
        self.assertEqual([file["id"] for file in final["output_files"]], [owned.id])
        self.assertEqual(final["output_files"][0]["byte_size"], len(original))
        session_id = events[0]["session_id"]
        history = await self._history(session_id)
        self.assertEqual(
            [item["status"] for item in history["items"] if item.get("tool_name") == "present"],
            ["removed", "completed"],
        )

        async def followup_stream(messages, info):
            self.assertIn("present", {tool.name for tool in info.function_tools})
            if not any(getattr(part, "tool_name", None) == "present" for m in messages for part in m.parts):
                yield {0: DeltaToolCall(name="present", json_args=json.dumps({"file_ids": [owned.id]}),
                                        tool_call_id="present-again")}
            else:
                yield "这次继续交付原文件。"

        followup = await self._stream_events(
            model=FunctionModel(stream_function=followup_stream),
            json_body={"session_id": session_id, "message": "把上面那份原文件再给我看"},
        )
        next_final = next(event for event in followup if event.get("type") == "final")
        self.assertEqual(next_final["output_files"], final["output_files"])

    async def test_present_limits_run_total_and_drops_pending_delivery_on_ui_resume(self):
        files = await self._register_file_fixture()
        calls = 0

        async def stream(_messages, info):
            nonlocal calls
            calls += 1
            if calls == 1:
                name, args = "generate_reports", {}
            elif calls in (2, 3):
                name = "present"
                subset = files[:10] if calls == 2 else files[10:]
                args = {"file_ids": [file["file_id"] for file in subset]}
            elif calls == 4:
                name = next(tool.name for tool in info.function_tools if tool.name.endswith("__confirm"))
                args = {"message": "继续？"}
            else:
                self.fail("Unexpected model request")
            yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"step-{calls}")}

        capabilities = ["builtin:present-fixture", *_CONFIRM_CAPABILITIES]
        events = await self._stream_events(
            model=FunctionModel(stream_function=stream), json_body={"message": "生成后确认", "capabilities": capabilities},
        )
        final = next(event for event in events if event.get("type") == "final")
        self.assertEqual(final["status"], "pending")
        self.assertNotIn("output_files", final)
        session = events[0]["session_id"]
        pending_history = await self._history(session)
        present_results = [item for item in pending_history["items"] if item.get("tool_name") == "present"]
        self.assertEqual([item["status"] for item in present_results], ["completed", "removed"])
        self.assertTrue(all("output_files" not in item for item in pending_history["items"]))

        async def resume_stream(messages, info):
            self.assertIn("present", {tool.name for tool in info.function_tools})
            names = [getattr(part, "tool_name", None) for message in messages for part in message.parts]
            self.assertNotIn("present", names)
            self.assertIn("generate_reports", names)
            yield "已确认。"

        resumed = await self._stream_events(model=FunctionModel(stream_function=resume_stream), json_body={
            "session_id": session, "capabilities": capabilities, "deferred_tool_results": [
                {"tool_call_id": final["deferred"]["calls"][0]["tool_call_id"], "result": {"confirmed": True}},
            ],
        })
        complete = next(event for event in resumed if event.get("type") == "final")
        self.assertEqual(complete["status"], "completed")
        self.assertNotIn("output_files", complete)
        self.assertTrue(all("output_files" not in item for item in (await self._history(session))["items"]))

    async def test_http_stream_multi_turn_retains_all_assistant_messages(self) -> None:
        session_id: str | None = None
        expected = ["first-turn-reply", "second-turn-reply"]
        for idx, reply in enumerate(expected):
            json_body: dict = {"message": f"turn-{idx + 1}"}
            if session_id is not None:
                json_body["session_id"] = session_id
            events = await self._stream_events(
                model=text_only_model(reply),
                json_body=json_body,
            )
            final = next(ev for ev in reversed(events) if ev.get("type") == "final")
            self.assertEqual(final["status"], "completed")
            session_id = events[0]["session_id"]

        assert session_id is not None
        data = await self._history(session_id)
        self.assertEqual(len(_message_items(data)), 4)
        self.assertEqual(_assistant_texts(data), expected)
        users = [
            item["text"]
            for item in data["items"]
            if item.get("kind") == "user_message"
        ]
        self.assertEqual(users, ["turn-1", "turn-2"])

    async def test_http_stream_deltas_match_persisted_history_text(self) -> None:
        chunks = ["alpha-", "beta-", "gamma-"]
        model, _ = build_slow_stream_model(chunks=chunks, delay_s=0.03)
        expected = "".join(chunks)

        with patch("deep_agent.agent.factory._resolve_model", return_value=model):
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
                            **_CHAT_JSON,
                            "agent_id": self._agent_id,
                            "message": "stream please",
                        },
                    ) as response:
                        body = "".join([chunk async for chunk in response.aiter_text()])

        events = parse_sse_events(body)
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "completed")
        self.assertEqual(_streamed_text(events), expected)

        self.assertNotIn(
            "conversation_item_upsert",
            {event.get("type") for event in events},
        )

        session_id = events[0]["session_id"]
        data = await self._history(session_id)
        self.assertGreaterEqual(len(_message_items(data)), 2)
        self.assertEqual(_assistant_texts(data), [expected])

    async def test_text_only_model_skips_auto_tool_calls(self) -> None:
        events = await self._stream_events(
            model=text_only_model("plain text"),
            json_body={"message": "no tools please"},
        )
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "completed")
        self.assertNotIn("tool_call", {ev.get("type") for ev in events})

    async def test_test_model_default_has_no_ui_tools_without_selection(self) -> None:
        events = await self._stream_events(
            model=TestModel(call_tools=[], custom_output_text="plain final"),
            json_body={"message": "no UI tools selected"},
        )
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["output"], "plain final")
        called_tools = {
            event.get("tool")
            for event in events
            if event.get("type") == "tool_call"
        }
        self.assertTrue(called_tools.isdisjoint({"builtin__choice", "builtin__confirm"}))

    async def test_test_model_calls_explicitly_selected_builtin_ui_tools(self) -> None:
        events = await self._stream_events(
            model=TestModel(
                call_tools=["builtin__choice", "builtin__confirm"],
                custom_output_text="never returned as final",
            ),
            json_body={
                "message": "triggers selected UI tools",
                "capabilities": _CONFIRM_CAPABILITIES,
            },
        )
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "pending")
        self.assertEqual(
            [call["tool"] for call in final["deferred"]["calls"]],
            ["builtin__choice", "builtin__confirm"],
        )

    async def test_tool_call_model_persists_deferred_tool_call_in_history(self) -> None:
        events = await self._stream_events(
            model=tool_call_model("builtin__confirm", text="after confirm"),
            json_body={"message": "need confirm", "capabilities": _CONFIRM_CAPABILITIES},
        )
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "pending")

        session_id = events[0]["session_id"]
        data = await self._history(session_id)
        self.assertEqual(len(_message_items(data)), 1)
        assistants = _assistant_messages(data)
        self.assertEqual(len(assistants), 0)
        tool_calls = [item for item in data["items"] if item.get("kind") == "tool"]
        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[0]["tool_name"], "builtin__confirm")
        self.assertEqual(tool_calls[0]["status"], "awaiting_input")

    async def test_hitl_stream_then_history_exactly_one_assistant(self) -> None:
        events = await self._stream_events(
            model=tool_call_model("builtin__confirm", text="done after confirm"),
            json_body={"message": "请确认", "capabilities": _CONFIRM_CAPABILITIES},
        )
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "pending")
        session_id = events[0]["session_id"]

        data = await self._history(session_id)
        self.assertEqual(len(_message_items(data)), 1)
        assistants = _assistant_messages(data)
        self.assertEqual(len(assistants), 0)
        self.assertEqual(len(_tool_call_ids(data)), 1)

    async def test_hitl_stream_history_resume_deferred_completes(self) -> None:
        events = await self._stream_events(
            model=tool_call_model("builtin__confirm", text="time is noon"),
            json_body={"message": "现在几点", "capabilities": _CONFIRM_CAPABILITIES},
        )
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "pending")
        session_id = events[0]["session_id"]
        tool_call_id = final["deferred"]["calls"][0]["tool_call_id"]

        pending_history = await self._history(session_id)
        self.assertEqual(len(_assistant_messages(pending_history)), 0)
        pending_tool = next(item for item in pending_history["items"] if item["kind"] == "tool")
        self.assertEqual(pending_tool["status"], "awaiting_input")

        title_before = await get_context().store.get_session_title(session_id, agent_id=self._agent_id)
        resume_events = await self._stream_events(
            model=tool_call_model("builtin__confirm", text="time is noon"),
            json_body={
                "session_id": session_id,
                "session_title": "工具回传不应改名",
                "capabilities": _CONFIRM_CAPABILITIES,
                "deferred_tool_results": [
                    {"tool_call_id": tool_call_id, "result": {"confirmed": True}},
                ],
            },
        )
        resume_final = next(ev for ev in reversed(resume_events) if ev.get("type") == "final")
        self.assertEqual(resume_final["status"], "completed")
        self.assertNotIn("error", {ev.get("type") for ev in resume_events})

        self.assertEqual(await get_context().store.get_session_title(session_id, agent_id=self._agent_id), title_before)
        self.assertNotIn("session_title", {ev.get("type") for ev in resume_events})
        done_history = await self._history(session_id)
        self.assertGreaterEqual(len(_message_items(done_history)), 2)
        self.assertGreaterEqual(len(_assistant_messages(done_history)), 1)
        completed_tool = next(item for item in done_history["items"] if item["kind"] == "tool")
        self.assertEqual(completed_tool["status"], "completed")

    async def test_stream_complete_raw_history_not_duplicated(self) -> None:
        """user_message -> stream done -> GET history must not duplicate assistant turns."""
        events = await self._stream_events(
            model=text_only_model("single reply"),
            json_body={"message": "hello"},
        )
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "completed")
        session_id = events[0]["session_id"]

        data = await self._history(session_id)
        self.assertEqual(len(_message_items(data)), 2)
        self.assertEqual(len(_assistant_messages(data)), 1)
        self.assertEqual(_assistant_texts(data), ["single reply"])


if __name__ == "__main__":
    unittest.main()
