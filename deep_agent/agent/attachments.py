from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Mapping, Sequence

from pydantic_ai.messages import BinaryContent, ModelMessage, ModelRequest, TextContent, UserContent, UserPromptPart

from deep_agent.agent.context_references import (
    context_reference_dict,
    context_references_from_content,
    format_context_reference_prompt,
    is_context_reference,
)
from deep_agent.schemas.attachment import AttachmentRef
from deep_agent.schemas.context_reference import ContextReference

ATTACHMENT_REF_KIND = "attachment-ref"
_NATIVE_MEDIA: tuple[tuple[str, str, str], ...] = (
    ("image/", "image", "图片"),
    ("audio/", "audio", "音频"),
    ("video/", "video", "视频"),
)


def model_supports_image_input(
    db_modalities: list[str] | None = None,
) -> bool:
    """Return whether the model accepts image attachments.

    ``llm_models.input_modalities`` is the sole runtime source. The model name,
    Provider URL, and catalog are intentionally not consulted.

    Args:
        db_modalities: ``input_modalities`` persisted on the ``llm_models`` row.
    """
    return "image" in (db_modalities or [])


class AttachmentDownloadError(Exception):
    """Platform file download returned an error payload instead of file bytes."""


class UnsupportedAttachmentModalityError(ValueError):
    """Attached native media is incompatible with the active model."""


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


def _merge_attachment_ref(
    existing: AttachmentRef | None,
    incoming: AttachmentRef,
) -> AttachmentRef:
    if existing is None:
        return incoming
    return AttachmentRef(
        id=incoming.id,
        name=existing.name or incoming.name,
        mime=existing.mime or incoming.mime,
        extracted_text=existing.extracted_text or incoming.extracted_text,
    )


def index_attachment_refs(
    attachments: Sequence[AttachmentRef] | None = None,
    messages: Sequence[ModelMessage] | None = None,
) -> dict[str, AttachmentRef]:
    """Index only attachment ids that belong to the current raw session."""
    indexed: dict[str, AttachmentRef] = {}
    for ref in attachments or ():
        indexed[ref.id] = _merge_attachment_ref(indexed.get(ref.id), ref)
    for message in messages or ():
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if not isinstance(part, UserPromptPart) or not isinstance(
                part.content,
                (list, tuple),
            ):
                continue
            for item in part.content:
                if not is_attachment_ref(item):
                    continue
                ref = AttachmentRef(
                    id=str(item["id"]),
                    name=item.get("name"),
                    mime=item.get("mime"),
                    extracted_text=item.get("extracted_text"),
                )
                indexed[ref.id] = _merge_attachment_ref(indexed.get(ref.id), ref)
    return indexed


def refs_from_attachments(
    message: str,
    attachments: Sequence[AttachmentRef] | None,
    context_refs: Sequence[ContextReference] | None = None,
) -> str | list[str | dict[str, Any]]:
    parts: list[str | dict[str, Any]] = []
    for ref in context_refs or ():
        parts.append(context_reference_dict(ref))
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


def has_user_turn(
    message: str,
    attachments: Sequence[AttachmentRef] | None,
    context_refs: Sequence[ContextReference] | None = None,
) -> bool:
    if message.strip():
        return True
    return bool(attachments or context_refs)


async def resolve_user_prompt(
    message: str,
    attachments: Sequence[AttachmentRef] | None,
    request_headers: Mapping[str, str],
    *,
    context_refs: Sequence[ContextReference] | None = None,
    input_modalities: Sequence[str] | None = None,
) -> list[UserContent]:
    modalities = frozenset(input_modalities or ())
    parts: list[UserContent] = []
    text = message.strip()
    if context_refs:
        parts.append(format_context_reference_prompt(context_refs, text))
    elif text:
        parts.append(text)
    for ref in attachments or ():
        parts.extend(
            await _resolve_ref(
                ref,
                request_headers,
                full=True,
                modalities=modalities,
            )
        )
    return parts


async def resolve_history_for_llm(
    messages: list[ModelMessage],
    *,
    current_user_index: int | None,
    request_headers: Mapping[str, str],
    input_modalities: Sequence[str] | None = None,
) -> list[ModelMessage]:
    modalities = frozenset(input_modalities or ())
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
                replace(
                    part,
                    content=await _resolve_user_prompt_part(
                        part.content,
                        request_headers,
                        full=full_turn,
                        modalities=modalities,
                    ),
                )
            )
        resolved.append(replace(message, parts=new_parts))
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
    modalities: frozenset[str],
) -> str | list[UserContent]:
    if isinstance(content, str):
        return content
    resolved: list[UserContent] = []
    context_refs = context_references_from_content(content)
    text_chunks: list[str] = []
    for item in content:
        if isinstance(item, str):
            text_chunks.append(item)
        elif isinstance(item, dict) and item.get("kind") == "text-content":
            text_chunks.append(str(item.get("content", "")))
    if context_refs:
        resolved.append(
            format_context_reference_prompt(
                context_refs,
                "\n".join(chunk for chunk in text_chunks if chunk),
            )
        )
    else:
        resolved.extend(chunk for chunk in text_chunks if chunk)
    for item in content:
        if isinstance(item, str) or is_context_reference(item):
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
                    modalities=modalities,
                )
            )
            continue
        if isinstance(item, dict) and item.get("kind") == "text-content":
            continue
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
    modalities: frozenset[str],
) -> list[UserContent]:
    mime = (ref.mime or "").split(";", 1)[0].strip().lower()
    name = ref.name or ref.id
    if not full:
        if ref.extracted_text:
            return [TextContent(ref.extracted_text)]
        return [
            TextContent(
                f"[附件: {name}] (file_id: {ref.id}, mime: {ref.mime or 'unknown'})"
            )
        ]

    native = _native_modality_for_mime(mime)
    if native is not None:
        modality, label = native
        if modality not in modalities:
            raise UnsupportedAttachmentModalityError(
                f"当前模型不支持{label}输入（附件: {name}）"
            )
        try:
            data = await _download_file(ref.id, request_headers)
        except AttachmentDownloadError:
            return [TextContent(f"[附件: {name}]（文件下载失败）")]
        return [BinaryContent(data=data, media_type=mime or "application/octet-stream")]
    return [
        TextContent(
            f"[附件: {name}] (file_id: {ref.id}, mime: {ref.mime or 'unknown'})"
            "（如需读取内容，请调用 read_file 系统工具）"
        )
    ]


def _native_modality_for_mime(mime: str) -> tuple[str, str] | None:
    for prefix, modality, label in _NATIVE_MEDIA:
        if mime.startswith(prefix):
            return modality, label
    if mime == "application/pdf":
        return "pdf", "PDF"
    return None
