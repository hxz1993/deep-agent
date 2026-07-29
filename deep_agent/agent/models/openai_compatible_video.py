"""OpenAI-compatible Chat Completions model with ``video_url`` parts."""

from __future__ import annotations

from typing import Any, Literal, cast

from openai.types.chat import ChatCompletionContentPartParam
from pydantic_ai.messages import BinaryContent, VideoUrl
from pydantic_ai.models import download_item
from pydantic_ai.models.openai import OpenAIChatModel
from typing_extensions import TypedDict, override


class _VideoURL(TypedDict):
    url: str


class _VideoPart(TypedDict, total=False):
    video_url: _VideoURL
    type: Literal["video_url"]
    fps: float


def _video_part(
    url: str,
    *,
    vendor_metadata: dict[str, Any] | None = None,
) -> ChatCompletionContentPartParam:
    part: _VideoPart = {
        "type": "video_url",
        "video_url": {"url": url},
    }
    if vendor_metadata and "fps" in vendor_metadata:
        try:
            part["fps"] = float(vendor_metadata["fps"])
        except (TypeError, ValueError):
            pass
    return cast(ChatCompletionContentPartParam, part)


class OpenAICompatibleVideoChatModel(OpenAIChatModel):
    """Map Pydantic AI video content to the common ``video_url`` extension."""

    @override
    async def _map_binary_content_item(
        self,
        item: BinaryContent,
    ) -> ChatCompletionContentPartParam:
        if item.is_video:
            return _video_part(
                item.data_uri,
                vendor_metadata=item.vendor_metadata,
            )
        return await super()._map_binary_content_item(item)

    @override
    async def _map_video_url_item(
        self,
        item: VideoUrl,
    ) -> ChatCompletionContentPartParam:
        url = item.url
        if item.force_download:
            content = await download_item(
                item,
                data_format="base64_uri",
                type_format="extension",
            )
            url = content["data"]
        return _video_part(url, vendor_metadata=item.vendor_metadata)
