from __future__ import annotations

import unittest
import warnings
from unittest.mock import AsyncMock, patch

from pydantic_ai.messages import ModelRequest, UserPromptPart

from deep_agent.agent.attachments import (
    attachment_ref_dict,
    index_attachment_refs,
    refs_from_attachments,
    resolve_history_for_llm,
    resolve_user_prompt,
)
from deep_agent.agent.history import model_messages_to_history
from deep_agent.db.serialization import deserialize_message, serialize_message
from deep_agent.schemas.attachment import AttachmentRef


class TestAttachments(unittest.IsolatedAsyncioTestCase):
    @patch("deep_agent.agent.attachments._download_file", new_callable=AsyncMock)
    async def test_resolve_user_prompt_image(self, mock_download: AsyncMock) -> None:
        mock_download.return_value = b"img-bytes"
        headers = {"space": "test"}
        parts = await resolve_user_prompt(
            "see this",
            [AttachmentRef(id="platform-file-1", name="a.jpg", mime="image/jpeg")],
            headers,
        )
        self.assertEqual(parts[0], "see this")
        self.assertEqual(parts[1].media_type, "image/jpeg")
        self.assertEqual(parts[1].data, b"img-bytes")
        mock_download.assert_awaited_once_with("platform-file-1", headers)

    @patch("deep_agent.agent.attachments._download_file", new_callable=AsyncMock)
    async def test_image_is_forwarded_without_local_model_gate(self, mock_download: AsyncMock) -> None:
        mock_download.return_value = b"img-bytes"
        parts = await resolve_user_prompt(
            "see this",
            [AttachmentRef(id="platform-file-1", name="a.jpg", mime="image/jpeg")],
            {},
        )
        self.assertEqual(parts[1].media_type, "image/jpeg")
        mock_download.assert_awaited_once()

    @patch("deep_agent.agent.attachments._download_file", new_callable=AsyncMock)
    async def test_image_download_error_falls_back_to_text(self, mock_download: AsyncMock) -> None:
        from deep_agent.agent.attachments import AttachmentDownloadError

        mock_download.side_effect = AttachmentDownloadError("failed")
        parts = await resolve_user_prompt(
            "",
            [AttachmentRef(id="bad-id", name="a.jpg", mime="image/jpeg")],
            {},
        )
        self.assertIn("文件下载失败", parts[0].content)

    def test_attachment_ref_message_roundtrip(self) -> None:
        ref = attachment_ref_dict(
            AttachmentRef(id="f1", name="a.png", mime="image/png"),
        )
        msg = ModelRequest(parts=[UserPromptPart(content=["hello", ref])])
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            payload = serialize_message(msg)
        self.assertFalse(any("PydanticSerializationUnexpectedValue" in str(w.message) for w in caught))
        restored = deserialize_message(payload)
        first = restored.parts[0]
        assert isinstance(first, UserPromptPart)
        self.assertEqual(first.content, ["hello", ref])

    async def test_current_document_uses_tool_placeholder_not_legacy_extracted_text(self) -> None:
        ref = AttachmentRef(
            id="doc-1",
            name="a.txt",
            mime="text/plain",
            extracted_text="[附件: a.txt]\nhello",
        )
        with patch(
            "deep_agent.agent.attachments._download_file",
            new_callable=AsyncMock,
        ) as mock_download:
            parts = await resolve_user_prompt("", [ref], {})
            self.assertIn("file_id: doc-1", parts[0].content)
            self.assertIn("read_file 系统工具", parts[0].content)
            mock_download.assert_not_called()

    @patch("deep_agent.agent.attachments._download_file", new_callable=AsyncMock)
    async def test_history_stub_for_prior_attachment(self, mock_download: AsyncMock) -> None:
        history = [
            ModelRequest(
                parts=[
                    UserPromptPart(
                        content=[
                            attachment_ref_dict(
                                AttachmentRef(id="old-id", name="old.jpg", mime="image/jpeg")
                            )
                        ]
                    )
                ]
            ),
            ModelRequest(parts=[UserPromptPart(content="new")]),
        ]
        resolved = await resolve_history_for_llm(
            history,
            current_user_index=1,
            request_headers={},
        )
        first = resolved[0].parts[0]
        assert isinstance(first, UserPromptPart)
        self.assertIn("[附件: old.jpg]", str(first.content))
        mock_download.assert_not_awaited()

    def test_history_api_includes_attachments(self) -> None:
        ref = attachment_ref_dict(
            AttachmentRef(
                id="f1",
                name="note.txt",
                mime="text/plain",
                extracted_text="[附件: note.txt]\nhi",
            )
        )
        messages = [ModelRequest(parts=[UserPromptPart(content=["hello", ref])])]
        timeline = model_messages_to_history(messages)
        self.assertEqual(len(timeline), 1)
        self.assertEqual(timeline[0].content, "hello")
        self.assertEqual(len(timeline[0].attachments), 1)
        self.assertEqual(timeline[0].attachments[0].id, "f1")

    def test_index_attachment_refs_only_uses_supplied_session_history(self) -> None:
        current = AttachmentRef(id="current", name="current.txt", mime="text/plain")
        old = attachment_ref_dict(
            AttachmentRef(id="old", name="old.txt", mime="text/plain")
        )
        indexed = index_attachment_refs(
            attachments=[current],
            messages=[
                ModelRequest(parts=[UserPromptPart(content=[old])]),
            ],
        )
        self.assertEqual(set(indexed), {"current", "old"})

    def test_refs_from_attachments_text_only(self) -> None:
        parts = refs_from_attachments("hi", None)
        self.assertEqual(parts, "hi")
