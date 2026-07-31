from __future__ import annotations

import unittest

from pydantic_ai.messages import BinaryContent, VideoUrl
from pydantic_ai.providers.openai import OpenAIProvider

from deep_agent.agent.models.openai_compatible_video import (
    OpenAICompatibleVideoChatModel,
)


class OpenAICompatibleVideoTests(unittest.IsolatedAsyncioTestCase):
    def _model(self) -> OpenAICompatibleVideoChatModel:
        return OpenAICompatibleVideoChatModel(
            "video-model",
            provider=OpenAIProvider(
                api_key="test-key",
                base_url="https://example.invalid/v1",
            ),
        )

    async def test_binary_video_uses_video_url_extension(self) -> None:
        part = await self._model()._map_binary_content_item(
            BinaryContent(
                data=b"fake-video",
                media_type="video/mp4",
                vendor_metadata={"fps": 2},
            )
        )
        self.assertEqual(part["type"], "video_url")
        self.assertEqual(part["fps"], 2.0)
        self.assertIn("data:video/mp4;base64,", part["video_url"]["url"])

    async def test_video_url_is_preserved(self) -> None:
        part = await self._model()._map_video_url_item(
            VideoUrl(url="https://cdn.example.invalid/video.mp4")
        )
        self.assertEqual(
            part,
            {
                "type": "video_url",
                "video_url": {"url": "https://cdn.example.invalid/video.mp4"},
            },
        )


if __name__ == "__main__":
    unittest.main()
