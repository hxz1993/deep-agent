"""Web image bytes reach only the active model request, never the Event Store."""
from __future__ import annotations

import asyncio
import base64
import json
import tempfile
import unittest
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image, PngImagePlugin
from pydantic_ai import Agent, AgentRunResultEvent
from pydantic_ai.messages import (
    BinaryContent, ImageUrl, ModelRequest, ModelResponse, TextPart,
    ToolCallPart, ToolReturn, ToolReturnPart, UserPromptPart,
)
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from sqlalchemy import text

from deep_agent.agent.attachments import resolve_history_for_llm
from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.events import map_part
from deep_agent.agent.message_persistence import (
    MessagePersistenceCapability, StreamMessagePersistenceCapability,
)
from deep_agent.agent.tool_output_limits import build_tool_output_limits
from deep_agent.conversation.models import ToolConversationItem
from deep_agent.conversation.projector import project_session_events
from deep_agent.db.event_codec import encode_message_parts, encode_messages
from deep_agent.db.serialization import deserialize_message, serialize_message
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


URL = "https://cdn.example.com/chart.png"
IDENTIFIER = "web-image-fixture"


def _image_bytes(*, padding: int = 0) -> bytes:
    target = BytesIO()
    info = PngImagePlugin.PngInfo()
    info.add_text("marker", "web-image-private-bytes")
    if padding:
        info.add_text("large-fixture", "x" * padding)
    Image.new("RGB", (2, 2), "blue").save(target, format="PNG", pnginfo=info)
    return target.getvalue()


DATA = _image_bytes()


def image_result(*, legacy: bool = False, force_download: bool = False) -> ToolReturn:
    value = (
        ImageUrl(URL, media_type="image/png", force_download=force_download,
                 identifier=IDENTIFIER)
        if legacy else
        BinaryContent(DATA, media_type="image/png", identifier=IDENTIFIER)
    )
    return ToolReturn(return_value=value, metadata={
        "sources": [{"url": URL, "title": None}],
        "web_image_view": {
            "mode": "local", "media_type": "image/png",
            "force_download": force_download, "ssrf_validated": True,
        },
        "web_content_untrusted": True,
    })


def image_part(*, legacy: bool = False, force_download: bool = False) -> ToolReturnPart:
    wrapped = image_result(legacy=legacy, force_download=force_download)
    return ToolReturnPart("web_image_view", wrapped.return_value, "image-call",
                          metadata=wrapped.metadata)


def tool_returns(messages):
    return [part for message in messages for part in message.parts
            if isinstance(part, ToolReturnPart)]


class WebImagePersistenceProjectionTests(unittest.IsolatedAsyncioTestCase):
    def assert_no_image_payload(self, value) -> None:
        encoded = json.dumps(value, default=str)
        self.assertNotIn("web-image-private-bytes", encoded)
        self.assertNotIn(base64.b64encode(DATA).decode(), encoded)
        self.assertNotIn('"kind": "binary"', encoded)
        self.assertNotIn('"data"', encoded)
        self.assertNotIn("data:image/", encoded)

    async def test_serialization_uses_reference_without_mutating_runtime_image(self):
        for force_download in (False, True):
            with self.subTest(force_download=force_download):
                part = image_part(force_download=force_download)
                original_metadata = deepcopy(part.metadata)
                request = ModelRequest(parts=[part])
                payload = serialize_message(request)
                self.assert_no_image_payload(payload)
                saved = payload["parts"][0]["content"]
                self.assertEqual(saved["kind"], "image-url")
                self.assertEqual(saved["url"], URL)
                self.assertEqual(saved["media_type"], "image/png")
                self.assertEqual(saved["force_download"], force_download)
                self.assertEqual(saved["identifier"], IDENTIFIER)
                self.assertEqual(part.metadata, original_metadata)
                self.assertIs(request.parts[0], part)
                self.assertIsInstance(part.content, BinaryContent)
                self.assertEqual(part.content.data, DATA)

    async def test_complete_and_part_event_codecs_do_not_serialize_image_bytes(self):
        request = ModelRequest(parts=[image_part()], metadata={
            "deepagent_message_ordinal": 2, "deepagent_part_indices": [0],
        })
        for encode in (encode_messages, encode_message_parts):
            with self.subTest(codec=encode.__name__):
                drafts = encode([request], session_id="session", run_id="run")
                self.assertEqual(len(drafts), 1)
                self.assert_no_image_payload(drafts[0].payload)
                restored = deserialize_message(drafts[0].payload["message"])
                self.assertIsInstance(restored.parts[0].content, ImageUrl)
                self.assertEqual(restored.parts[0].content.url, URL)

    async def test_sse_retains_safe_reference_and_source_metadata(self):
        part = image_part(force_download=True)
        event = map_part(part)
        self.assert_no_image_payload(event)
        self.assertEqual(event["result"]["kind"], "web-image-reference")
        self.assertEqual(event["result"]["url"], URL)
        self.assertEqual(event["result"]["media_type"], "image/png")
        self.assertTrue(event["result"]["force_download"])
        self.assertEqual(event["sources"], part.metadata["sources"])
        self.assertEqual(event["web"], part.metadata["web_image_view"])
        self.assertIsInstance(part.content, BinaryContent)

    async def test_legacy_image_url_and_new_saved_result_both_resume_as_small_reference(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                raw = ModelRequest(parts=[image_part(legacy=legacy, force_download=True)])
                restored = deserialize_message(serialize_message(raw))
                with patch("deep_agent.agent.attachments._download_file", new=AsyncMock()) as fetch:
                    resolved = await resolve_history_for_llm(
                        [restored], current_user_index=None, request_headers={},
                    )
                fetch.assert_not_awaited()
                content = resolved[0].parts[0].content
                self.assertEqual(content["kind"], "web-image-reference")
                self.assertEqual(content["url"], URL)
                self.assertEqual(content["media_type"], "image/png")
                self.assertTrue(content["force_download"])
                self.assert_no_image_payload(content)
                self.assertIsInstance(restored.parts[0].content, ImageUrl)

    async def test_compacted_legacy_reference_recommends_current_tool_without_rewriting_history(self):
        legacy = {"kind": "web-image-reference", "url": URL,
                  "note": "Web image viewed in a previous run; call web_image_view to inspect it again."}
        raw = ModelRequest(parts=[ToolReturnPart("web_image_view", [legacy], "old-compacted-call")])
        resolved = await resolve_history_for_llm([raw], current_user_index=None, request_headers={})
        note = resolved[0].parts[0].content[0]["note"]
        self.assertIn("call view_image", note)
        self.assertNotIn("call web_image_view", note)
        self.assertIn("call web_image_view", raw.parts[0].content[0]["note"])

    async def test_unrelated_binary_tools_and_user_attachments_keep_existing_serialization(self):
        request = ModelRequest(parts=[
            ToolReturnPart("another_image_tool", BinaryContent(b"image", media_type="image/png"),
                           "another-call"),
            UserPromptPart([BinaryContent(b"attachment", media_type="image/png")]),
        ])
        saved = serialize_message(request)
        self.assertEqual(saved["parts"][0]["content"]["kind"], "binary")
        self.assertEqual(saved["parts"][1]["content"][0]["kind"], "binary")
        restored = deserialize_message(saved)
        self.assertEqual(restored.parts[0].content.data, b"image")
        self.assertEqual(restored.parts[1].content[0].data, b"attachment")


class WebImageSdkPersistenceTests(unittest.IsolatedAsyncioTestCase):
    assert_no_image_payload = WebImagePersistenceProjectionTests.assert_no_image_payload

    async def test_real_sdk_nonstream_and_stream_keep_visual_input_and_persist_only_url(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                await self._sdk_case(streaming=streaming)

    async def test_visual_result_bypasses_text_overflow_without_platform_storage(self):
        upload = AsyncMock(return_value=SimpleNamespace(id="unwanted-spilled-web-image"))
        download = AsyncMock()
        limits, store = build_tool_output_limits(
            request_headers={}, context_window_tokens=20_000,
            max_output_tokens=4_000, estimator=TokenEstimator.conservative(),
            file_store=SimpleNamespace(upload=upload, download=download),
        )
        deps = AgentDeps(session_id="image-with-output-limits")
        store.bind(deps, [])
        image = image_result()
        image.return_value = BinaryContent(_image_bytes(padding=200_000), media_type="image/png",
                                           identifier=IDENTIFIER)

        async def model(messages, info):
            returned = tool_returns(messages)
            if returned:
                self.assertTrue(isinstance(returned[0].content, BinaryContent),
                                "Native visual content must not become an overflow text preview")
                self.assertEqual(returned[0].content.data, image.return_value.data)
                self.assertNotIn("overflow_handle", returned[0].metadata)
                return ModelResponse(parts=[TextPart("native image retained")])
            return ModelResponse(parts=[ToolCallPart("web_image_view", {"url": URL}, "image-call")])

        agent = Agent(FunctionModel(model), deps_type=AgentDeps, capabilities=[limits])

        @agent.tool_plain
        async def web_image_view(url: str) -> ToolReturn:
            return image

        result = await agent.run("inspect", deps=deps)
        self.assertEqual(result.output, "native image retained")
        upload.assert_not_awaited()
        download.assert_not_awaited()

    async def _sdk_case(self, *, streaming: bool) -> None:
        with tempfile.TemporaryDirectory(prefix="TMP to delete-web-image-events-") as tmp:
            stores, engine, _ = await make_sqlite_stores(
                "sqlite+aiosqlite:///" + str(Path(tmp) / "history.db")
            )
            try:
                agent_id = await seed_default_agent(stores)
                session = "web-image-stream" if streaming else "web-image-http"
                deps = AgentDeps(session_id=session, agent_id=agent_id, user="fixture-user",
                                 turn_id="image-turn", run_id="image-run")
                queue = asyncio.Queue()
                cap_type = StreamMessagePersistenceCapability if streaming else MessagePersistenceCapability
                cap = cap_type(stores.coordinator, history_length=0, skip_user_prompt=False,
                               event_queue=queue)
                upload = AsyncMock(return_value=SimpleNamespace(id="unwanted-spilled-web-image"))
                download = AsyncMock()
                limits, overflow_store = build_tool_output_limits(
                    request_headers={}, context_window_tokens=20_000,
                    max_output_tokens=4_000, estimator=TokenEstimator.conservative(),
                    file_store=SimpleNamespace(upload=upload, download=download),
                )
                overflow_store.bind(deps, [])
                model_visual_inputs = []

                def check_image(messages):
                    returns = tool_returns(messages)
                    if returns:
                        self.assertEqual(len(returns), 1)
                        self.assertEqual(returns[0].tool_name, "web_image_view")
                        self.assertIsInstance(returns[0].content, BinaryContent)
                        self.assertEqual(returns[0].content.data, DATA)
                        model_visual_inputs.append(returns[0].content.identifier)
                        # No extra user prompt carrying the same image was introduced.
                        prompts = [part for message in messages for part in message.parts
                                   if isinstance(part, UserPromptPart)]
                        self.assertEqual(len(prompts), 1)
                        self.assertEqual(prompts[0].content, "inspect fixture")
                    return bool(returns)

                async def model(messages, info):
                    if check_image(messages):
                        return ModelResponse(parts=[TextPart("fixture inspected")])
                    return ModelResponse(parts=[ToolCallPart("web_image_view", {"url": URL}, "image-call")])

                async def stream(messages, info):
                    if check_image(messages):
                        yield "fixture inspected"
                    else:
                        yield {0: DeltaToolCall(name="web_image_view", json_args=json.dumps({"url": URL}),
                                                tool_call_id="image-call")}

                agent = Agent(FunctionModel(function=model, stream_function=stream),
                              deps_type=AgentDeps, capabilities=[limits, cap])

                @agent.tool_plain
                async def web_image_view(url: str) -> ToolReturn:
                    self.assertEqual(url, URL)
                    return image_result()

                if streaming:
                    result = None
                    async with agent.run_stream_events("inspect fixture", deps=deps,
                                                       run_id=deps.run_id) as events:
                        async for event in events:
                            if isinstance(event, AgentRunResultEvent):
                                result = event.result
                    self.assertIsNotNone(result)
                else:
                    result = await agent.run("inspect fixture", deps=deps, run_id=deps.run_id)
                self.assertEqual(result.output, "fixture inspected")
                self.assertEqual(model_visual_inputs, [IDENTIFIER])
                self.assertIsNone(cap.persistence_error)
                self.assertIsInstance(tool_returns(result.new_messages())[0].content, BinaryContent)
                upload.assert_not_awaited()
                download.assert_not_awaited()

                # Independent DB connection proves that no SDK image object escaped into JSON.
                async with engine.connect() as connection:
                    rows = (await connection.execute(text(
                        "SELECT payload FROM chat_session_events WHERE session_id = :session ORDER BY sequence"
                    ), {"session": session})).scalars().all()
                self.assertTrue(rows)
                for row in rows:
                    self.assert_no_image_payload(json.loads(row) if isinstance(row, str) else row)

                published = []
                while not queue.empty():
                    published.append(queue.get_nowait())
                self.assertTrue(published)
                for event in published:
                    self.assert_no_image_payload(event)

                saved_events = await stores.coordinator.list_events(session, limit=100)
                history = project_session_events(saved_events)
                results = [item for item in history.items if isinstance(item, ToolConversationItem)]
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0].output["kind"], "web-image-reference")
                self.assertEqual(results[0].output["url"], URL)
                self.assert_no_image_payload(history.model_dump(mode="json"))
                raw = await stores.coordinator.get_raw_history(session, agent_id=agent_id)
                resolved = await resolve_history_for_llm(raw, current_user_index=None, request_headers={})
                self.assertEqual(tool_returns(resolved)[0].content["kind"], "web-image-reference")
                observations = []

                async def next_model(messages, info):
                    prior = tool_returns(messages)
                    self.assertEqual(len(prior), 1)
                    self.assertEqual(prior[0].content["url"], URL)
                    self.assertFalse(any(isinstance(part.content, BinaryContent) for part in prior))
                    observations.append(True)
                    return ModelResponse(parts=[TextPart("history preserved")])

                continued = Agent(FunctionModel(next_model))
                with patch("deep_agent.agent.attachments._download_file", new=AsyncMock()) as fetch:
                    answer = await continued.run("continue", message_history=resolved)
                fetch.assert_not_awaited()
                self.assertEqual(answer.output, "history preserved")
                self.assertEqual(observations, [True])
            finally:
                await engine.dispose()


if __name__ == "__main__":
    unittest.main()
