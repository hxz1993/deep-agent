"""Safe first-turn session title generation tests."""

from __future__ import annotations

import json
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx2
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

from pydantic_ai.models.test import TestModel

from deep_agent.agent.title_generator import (
    _clean_title,
    _safe_title_input,
    _title_snippet,
    fallback_title,
    generate_title,
)
from deep_agent.schemas.attachment import AttachmentRef
from deep_agent.schemas.context_reference import ContextReference


def _context() -> ContextReference:
    return ContextReference(
        key="selection-1",
        type="deeptable.selection",
        schema_version="1",
        label="华东预算区",
        payload={
            "id": "region-42",
            "name": "华东",
            "nested": {"count": 12, "secret": "must-not-leak"},
            "description": "large free text must not be copied",
        },
        metadata={"token": "hidden", "source": "browser"},
    )


class TestTitleHelpers(unittest.TestCase):
    def test_clean_title(self) -> None:
        self.assertEqual(_clean_title('Title: "分析预算差异。"'), "分析预算差异")
        self.assertIsNone(_clean_title(""))
        long_title = "这是一段超过三十个汉字的会话标题用来验证截断逻辑是否按预期工作"
        self.assertEqual(_clean_title(long_title), long_title[:27] + "...")

    def test_title_snippet_extracts_wrapped_user_text(self) -> None:
        wrapped = "<reference>上下文</reference><user> 请总结季度报告 </user>"
        self.assertEqual(_title_snippet(wrapped), "请总结季度报告")

    def test_fallback_uses_text_then_context_then_attachment(self) -> None:
        attachment = AttachmentRef(id="file-1", name="预算.xlsx", mime="application/xlsx")
        self.assertEqual(fallback_title("分析差异", [_context()], [attachment]), "分析差异")
        self.assertEqual(fallback_title("", [_context()], [attachment]), "[上下文] 华东预算区")
        self.assertEqual(fallback_title("", [], [attachment]), "查看 预算.xlsx")

    def test_safe_projection_excludes_metadata_and_free_form_payload(self) -> None:
        attachment = AttachmentRef(
            id="file-secret-id",
            name="预算.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            extracted_text="附件正文不得进入标题请求",
        )
        projection = _safe_title_input("分析华东预算", [_context()], [attachment])
        payload = json.loads(projection.split("\n", 1)[1])
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertIn("region-42", serialized)
        self.assertIn("预算.xlsx", serialized)
        self.assertNotIn("must-not-leak", serialized)
        self.assertNotIn("hidden", serialized)
        self.assertNotIn("file-secret-id", serialized)
        self.assertNotIn("附件正文", serialized)


class TestGenerateTitle(unittest.IsolatedAsyncioTestCase):
    async def test_native_title_switches_do_not_change_main_model_requests(self) -> None:
        for provider_type, model_class in [("deepseek", OpenAIResponsesModel), ("deepseek", OpenAIChatModel), ("moonshotai", OpenAIChatModel)]:
            with self.subTest(provider=provider_type, protocol=model_class.__name__):
                sent = []
                def transport(request):
                    body = json.loads(request.content)
                    sent.append(body)
                    if model_class is OpenAIResponsesModel:
                        payload = {"id": "resp-1", "object": "response", "created_at": 0,
                                   "model": "deepseek-flash", "status": "completed",
                                   "output": [{"id": "msg-1", "type": "message", "role": "assistant", "status": "completed",
                                               "content": [{"type": "output_text", "text": "代码轨迹测试", "annotations": []}]}]}
                    else:
                        payload = {"id": "chat-1", "object": "chat.completion", "created": 0, "model": "fixture",
                                   "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "代码轨迹测试"}}]}
                    return httpx2.Response(200, json=payload)
                async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport)) as client:
                    # A custom endpoint has no native profile for newly named models.
                    model = model_class("deepseek-flash" if provider_type == "deepseek" else "kimi-k2.6",
                                        provider=OpenAIProvider(api_key="private-key", http_client=client))
                    result = await generate_title(model=model, provider_type=provider_type, user_text="private prompt")
                    self.assertEqual(result.title, "代码轨迹测试")
                    await Agent(model).run("normal chat", model_settings={"temperature": 0.7})
                if model_class is OpenAIResponsesModel:
                    self.assertEqual(sent[0]["reasoning"]["effort"], "none")
                    self.assertNotIn("reasoning", sent[1])
                else:
                    self.assertEqual(sent[0]["thinking"], {"type": "disabled"})
                    self.assertNotIn("thinking", sent[1])
                if provider_type == "moonshotai":
                    self.assertNotIn("temperature", sent[0])
                self.assertEqual(sent[1]["temperature"], 0.7)

    async def test_token_limit_failure_has_correlated_safe_diagnostic_and_no_retry(self) -> None:
        calls, records = [], []
        def transport(request):
            calls.append(request)
            return httpx2.Response(200, json={"id": "resp-1", "object": "response", "created_at": 0,
                "model": "deepseek-flash", "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
                "output": [{"id": "reasoning-1", "type": "reasoning", "summary": [{"type": "summary_text", "text": "private reasoning"}]}]})
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport)) as client:
            model = OpenAIResponsesModel("deepseek-flash", provider=OpenAIProvider(api_key="private-key", http_client=client))
            with patch("deep_agent.agent.title_generator.log_event", side_effect=lambda event, **facts: records.append((event, facts))):
                result = await generate_title(model=model, provider_type="deepseek", user_text="private prompt",
                                              diagnostic_fields={"session_id": "session-1", "run_id": "run-1"})
        self.assertIsNone(result)
        self.assertEqual(len(calls), 1)
        self.assertEqual(records[-1][0], "title_generation_failed")
        self.assertEqual(records[-1][1]["reason"], "output_token_limit")
        self.assertEqual(records[-1][1]["session_id"], "session-1")
        self.assertEqual(records[-1][1]["run_id"], "run-1")
        self.assertNotIn("private", repr(records))

    async def test_cancellation_is_logged_and_propagated(self) -> None:
        store = AsyncMock()
        store.download.side_effect = asyncio.CancelledError()
        with patch("deep_agent.agent.title_generator.log_event") as logged:
            with self.assertRaises(asyncio.CancelledError):
                await generate_title(model=TestModel(), user_text="", file_store=store,
                                     attachments=[AttachmentRef(id="image", mime="image/png")])
        self.assertEqual(logged.call_args.args[0], "title_generation_cancelled")

    async def test_uses_effective_model_once(self) -> None:
        result = await generate_title(
            model=TestModel(call_tools=[], custom_output_text="分析预算差异"),
            user_text="请分析预算差异",
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.title, "分析预算差异")
        self.assertFalse(result.image_used)

    async def test_first_image_is_downloaded_and_sent(self) -> None:
        store = AsyncMock()
        store.download.return_value = b"fake-png"
        attachments = [
            AttachmentRef(id="img-1", name="截图.png", mime="image/png"),
            AttachmentRef(id="img-2", name="第二张.png", mime="image/png"),
        ]
        result = await generate_title(
            model=TestModel(call_tools=[], custom_output_text="截图问题分析"),
            user_text="",
            attachments=attachments,
            file_store=store,
            request_headers={"user": "tester"},
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.image_used)
        store.download.assert_awaited_once_with("img-1", {"user": "tester"})

    async def test_image_download_failure_keeps_fallback(self) -> None:
        store = AsyncMock()
        store.download.side_effect = FileNotFoundError("missing")
        result = await generate_title(
            model=TestModel(call_tools=[], custom_output_text="不应调用"),
            user_text="",
            attachments=[AttachmentRef(id="img-1", name="截图.png", mime="image/png")],
            file_store=store,
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
