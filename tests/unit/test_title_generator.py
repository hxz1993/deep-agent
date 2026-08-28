"""Safe first-turn session title generation tests."""

from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock

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
