from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Mapping, Sequence

from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    TextContent,
    ToolReturnPart,
    UserContent,
    UserPromptPart,
)

from deep_agent.agent.context_references import (
    context_reference_dict,
    context_references_from_content,
    format_context_reference_prompt,
    is_context_reference,
)
from deep_agent.schemas.attachment import AttachmentRef
from deep_agent.schemas.context_reference import ContextReference
from deep_agent.files.storage import FileStore, FileStoreError, PlatformFileStore
from deep_agent.web.images import prior_run_web_image_value
from deepfos_platform.errors.classes import APIStatusNotTrue

ATTACHMENT_REF_KIND = "attachment-ref"
_NATIVE_MEDIA_PREFIXES = ("image/", "audio/", "video/")


class AttachmentDownloadError(Exception):
    """Platform file download returned an error payload instead of file bytes."""


def attachment_ref_dict(ref: AttachmentRef) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": ATTACHMENT_REF_KIND,
        "id": ref.id,
        "name": ref.name,
        "mime": ref.mime,
    }
    if ref.extracted_text:
        payload["extracted_text"] = ref.extracted_text
    if ref.input_mode != "auto":
        payload["input_mode"] = ref.input_mode
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
        input_mode=existing.input_mode,
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
                    input_mode=item.get("input_mode", "auto"),
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
    file_store: FileStore | None = None,
) -> list[UserContent]:
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
                file_store=file_store,
            )
        )
    return parts


async def resolve_history_for_llm(
    messages: list[ModelMessage],
    *,
    current_user_index: int | None,
    request_headers: Mapping[str, str],
    file_store: FileStore | None = None,
) -> list[ModelMessage]:
    resolved: list[ModelMessage] = []
    for idx, message in enumerate(messages):
        if not isinstance(message, ModelRequest):
            resolved.append(message)
            continue
        full_turn = idx == current_user_index
        new_parts = []
        for part in message.parts:
            if isinstance(part, ToolReturnPart) and part.tool_name == "web_image_view":
                new_parts.append(
                    replace(
                        part,
                        content=prior_run_web_image_value(part.content),
                    )
                )
                continue
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
                        file_store=file_store,
                    ),
                )
            )
        resolved.append(replace(message, parts=new_parts))
    return resolved


async def _download_file(
    file_id: str,
    request_headers: Mapping[str, str],
    *,
    file_store: FileStore | None = None,
) -> bytes:
    try:
        data = await (file_store or PlatformFileStore()).download(
            file_id,
            request_headers,
        )
    except FileStoreError as exc:
        raise AttachmentDownloadError(f"File download failed for {file_id}") from exc
    if _looks_like_platform_error_json(data):
        payload = json.loads(data)
        cause = APIStatusNotTrue(
            "files/download", payload.get("message") or payload.get("msg") or "平台未提供错误说明",
            payload.get("code"),
        )
        raise AttachmentDownloadError(f"Platform file download failed for {file_id}") from cause
    return data


def _looks_like_platform_error_json(data: bytes) -> bool:
    if not data or data[:1] != b"{":
        return False
    try:
        payload = json.loads(data[: min(len(data), 4096)])
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("status") is False


async def _resolve_user_prompt_part(
    content: str | Sequence[Any],
    request_headers: Mapping[str, str],
    *,
    full: bool,
    file_store: FileStore | None = None,
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
                input_mode=item.get("input_mode", "auto"),
            )
            resolved.extend(
                await _resolve_ref(
                    ref,
                    request_headers,
                    full=full,
                    file_store=file_store,
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
    file_store: FileStore | None = None,
) -> list[UserContent]:
    mime = (ref.mime or "").split(";", 1)[0].strip().lower()
    name = ref.name or ref.id
    if ref.input_mode == "reference":
        return [TextContent(f"[附件: {name}] (file_id: {ref.id}, mime: {ref.mime or 'unknown'})（保留原文件，本轮未将文件内容交给模型）")]
    if not full:
        if ref.extracted_text:
            return [TextContent(ref.extracted_text)]
        return [
            TextContent(
                f"[附件: {name}] (file_id: {ref.id}, mime: {ref.mime or 'unknown'})"
            )
        ]

    if _is_native_media(mime):
        try:
            data = await _download_file(
                ref.id,
                request_headers,
                file_store=file_store,
            )
        except AttachmentDownloadError:
            return [TextContent(f"[附件: {name}]（文件下载失败）")]
        return [BinaryContent(data=data, media_type=mime or "application/octet-stream")]
    return [
        TextContent(
            f"[附件: {name}] (file_id: {ref.id}, mime: {ref.mime or 'unknown'})"
            "（仅提供文件引用；请按本轮工具支持的格式选择读取或解析工具获取内容）"
        )
    ]


def _is_native_media(mime: str) -> bool:
    return mime.startswith(_NATIVE_MEDIA_PREFIXES)
