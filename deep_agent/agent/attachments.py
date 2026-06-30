from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from pydantic_ai.messages import BinaryContent, ModelMessage, ModelRequest, TextContent, UserContent, UserPromptPart

from deep_agent.schemas.attachment import AttachmentRef

ATTACHMENT_REF_KIND = "attachment-ref"

# ponytail: name-heuristic until llm_models gains a supports_vision flag
_VISION_MODEL_MARKERS = (
    "gpt-4o",
    "gpt-4-turbo",
    "gpt-4-vision",
    "gpt-4.1",
    "claude-3",
    "claude-sonnet-4",
    "claude-opus-4",
    "gemini",
    "qwen3-vl",
    "qwen3.7",
    "qwen3.6",
    "qwen3.5-omni",
    "kimi-k2",
    "moonshot-v1-vision",
    "deepseek-vl",
    "glm-4v",
    "internvl",
)


class AttachmentDownloadError(Exception):
    """Platform file download returned an error payload instead of file bytes."""


def model_supports_image_input(model_name: str) -> bool:
    name = model_name.lower()
    if ":" in name:
        _prefix, _sep, rest = name.partition(":")
        if rest:
            name = rest
    return any(marker in name for marker in _VISION_MODEL_MARKERS)


def attachment_ref_dict(ref: AttachmentRef) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": ATTACHMENT_REF_KIND,
        "id": ref.id,
        "name": ref.name,
        "mime": ref.mime,
    }
    if ref.extracted_text:
        payload["extracted_text"] = ref.extracted_text
    return payload


def is_attachment_ref(item: Any) -> bool:
    return isinstance(item, dict) and item.get("kind") == ATTACHMENT_REF_KIND


def refs_from_attachments(
    message: str,
    attachments: Sequence[AttachmentRef] | None,
) -> str | list[str | dict[str, Any]]:
    parts: list[str | dict[str, Any]] = []
    text = message.strip()
    if text:
        parts.append(text)
    for ref in attachments or ():
        parts.append(attachment_ref_dict(ref))
    if not parts:
        return ""
    if len(parts) == 1 and isinstance(parts[0], str):
        return parts[0]
    return parts


def has_user_turn(message: str, attachments: Sequence[AttachmentRef] | None) -> bool:
    if message.strip():
        return True
    return bool(attachments)


async def resolve_user_prompt(
    message: str,
    attachments: Sequence[AttachmentRef] | None,
    request_headers: Mapping[str, str],
    *,
    vision_capable: bool = True,
) -> list[UserContent]:
    parts: list[UserContent] = []
    text = message.strip()
    if text:
        parts.append(text)
    for ref in attachments or ():
        parts.extend(
            await _resolve_ref(ref, request_headers, full=True, vision_capable=vision_capable)
        )
    return parts


async def resolve_history_for_llm(
    messages: list[ModelMessage],
    *,
    current_user_index: int | None,
    request_headers: Mapping[str, str],
    vision_capable: bool = True,
) -> list[ModelMessage]:
    resolved: list[ModelMessage] = []
    for idx, message in enumerate(messages):
        if not isinstance(message, ModelRequest):
            resolved.append(message)
            continue
        full_turn = idx == current_user_index
        new_parts = []
        for part in message.parts:
            if not isinstance(part, UserPromptPart):
                new_parts.append(part)
                continue
            new_parts.append(
                UserPromptPart(
                    content=await _resolve_user_prompt_part(
                        part.content,
                        request_headers,
                        full=full_turn,
                        vision_capable=vision_capable,
                    ),
                    timestamp=part.timestamp,
                )
            )
        resolved.append(ModelRequest(parts=new_parts, timestamp=message.timestamp))
    return resolved


async def _download_file(file_id: str, request_headers: Mapping[str, str]) -> bytes:
    from deepfos_platform.client.platform import PlatformAPI

    api = PlatformAPI(header=request_headers)
    data = await api.file.download(file_id)
    if _looks_like_platform_error_json(data):
        raise AttachmentDownloadError(f"Platform file download failed for {file_id}")
    return data


def _looks_like_platform_error_json(data: bytes) -> bool:
    if not data or data[:1] != b"{":
        return False
    try:
        payload = json.loads(data[: min(len(data), 4096)])
    except json.JSONDecodeError:
        return False
    return isinstance(payload, dict) and payload.get("status") is False


async def _resolve_user_prompt_part(
    content: str | Sequence[Any],
    request_headers: Mapping[str, str],
    *,
    full: bool,
    vision_capable: bool,
) -> str | list[UserContent]:
    if isinstance(content, str):
        return content
    resolved: list[UserContent] = []
    for item in content:
        if isinstance(item, str):
            resolved.append(item)
            continue
        if is_attachment_ref(item):
            ref = AttachmentRef(
                id=str(item["id"]),
                name=item.get("name"),
                mime=item.get("mime"),
                extracted_text=item.get("extracted_text"),
            )
            resolved.extend(
                await _resolve_ref(
                    ref,
                    request_headers,
                    full=full,
                    vision_capable=vision_capable,
                )
            )
            continue
        if isinstance(item, dict) and item.get("kind") == "text-content":
            resolved.append(str(item.get("content", "")))
    if not resolved:
        return ""
    if len(resolved) == 1 and isinstance(resolved[0], str):
        return resolved[0]
    return resolved


async def _resolve_ref(
    ref: AttachmentRef,
    request_headers: Mapping[str, str],
    *,
    full: bool,
    vision_capable: bool,
) -> list[UserContent]:
    mime = (ref.mime or "").split(";", 1)[0].strip().lower()
    name = ref.name or ref.id
    if not full:
        if ref.extracted_text:
            return [TextContent(ref.extracted_text)]
        return [TextContent(f"[附件: {name}]")]

    if mime.startswith("image/"):
        if not vision_capable:
            return [TextContent(f"[附件: {name}]（当前模型不支持图片输入）")]
        try:
            data = await _download_file(ref.id, request_headers)
        except AttachmentDownloadError:
            return [TextContent(f"[附件: {name}]（文件下载失败）")]
        return [BinaryContent(data=data, media_type=mime or "application/octet-stream")]
    if ref.extracted_text:
        return [TextContent(ref.extracted_text)]
    if mime in ("text/plain", "text/csv"):
        data = await _download_file(ref.id, request_headers)
        return [TextContent(data.decode("utf-8", errors="replace"))]
    return [TextContent(f"[附件: {name}]")]
