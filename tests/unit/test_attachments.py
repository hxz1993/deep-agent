from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from pydantic_ai.messages import ModelRequest, UserPromptPart

from deep_agent.agent.attachments import (
    attachment_ref_dict,
    refs_from_attachments,
    resolve_history_for_llm,
    resolve_user_prompt,
)
from deep_agent.agent.history import model_messages_to_history
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

    async def test_resolve_document_uses_extracted_text(self) -> None:
        ref = AttachmentRef(
            id="doc-1",
            name="a.pdf",
            mime="application/pdf",
            extracted_text="[附件: a.pdf]\nhello",
        )
        with patch(
            "deep_agent.agent.attachments._download_file",
            new_callable=AsyncMock,
        ) as mock_download:
            parts = await resolve_user_prompt("", [ref], {})
            self.assertEqual(parts[0].content, "[附件: a.pdf]\nhello")
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
        resolved = await resolve_history_for_llm(history, current_user_index=1, request_headers={})
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

    def test_refs_from_attachments_text_only(self) -> None:
        parts = refs_from_attachments("hi", None)
        self.assertEqual(parts, "hi")
