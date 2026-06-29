from __future__ import annotations

from typing import Any, Mapping, Sequence

from pydantic_ai.messages import BinaryContent, ModelMessage, ModelRequest, TextContent, UserContent, UserPromptPart

from deep_agent.schemas.attachment import AttachmentRef

ATTACHMENT_REF_KIND = "attachment-ref"


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
) -> list[UserContent]:
    parts: list[UserContent] = []
    text = message.strip()
    if text:
        parts.append(text)
    for ref in attachments or ():
        parts.extend(await _resolve_ref(ref, request_headers, full=True))
    return parts


async def resolve_history_for_llm(
    messages: list[ModelMessage],
    *,
    current_user_index: int | None,
    request_headers: Mapping[str, str],
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
                    ),
                    timestamp=part.timestamp,
                )
            )
        resolved.append(ModelRequest(parts=new_parts, timestamp=message.timestamp))
    return resolved


async def _download_file(file_id: str, request_headers: Mapping[str, str]) -> bytes:
    from deepfos_platform.client.platform import PlatformAPI

    api = PlatformAPI(header=request_headers)
    return await api.file.download(file_id)


async def _resolve_user_prompt_part(
    content: str | Sequence[Any],
    request_headers: Mapping[str, str],
    *,
    full: bool,
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
            resolved.extend(await _resolve_ref(ref, request_headers, full=full))
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
) -> list[UserContent]:
    mime = (ref.mime or "").split(";", 1)[0].strip().lower()
    name = ref.name or ref.id
    if not full:
        if ref.extracted_text:
            return [TextContent(ref.extracted_text)]
        return [TextContent(f"[附件: {name}]")]

    if mime.startswith("image/"):
        data = await _download_file(ref.id, request_headers)
        return [BinaryContent(data=data, media_type=mime or "application/octet-stream")]
    if ref.extracted_text:
        return [TextContent(ref.extracted_text)]
    if mime in ("text/plain", "text/csv"):
        data = await _download_file(ref.id, request_headers)
        return [TextContent(data.decode("utf-8", errors="replace"))]
    return [TextContent(f"[附件: {name}]")]
