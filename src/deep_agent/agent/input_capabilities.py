"""Platform input capability model and agent instruction augmentation.

Three layers (do not conflate):

1. **LLM native** — ``input_modalities`` on the preset (text / image / audio / video).
2. **Upload pipeline** — files accepted at POST /uploads by MIME whitelist.
3. **System tools** — ``read_file`` reads supported attachments on demand when present.
"""

from __future__ import annotations

from typing import Any

from deep_agent.agent.media_pipeline import allowed_mimes
from deep_agent.agent.spec import AgentSpec
from deep_agent.options import OPTION
from deep_agent.documents.models import ProviderCapability
from deep_agent.documents.providers import PROVIDERS
from deep_agent.documents.settings import DocumentParsingRuntimeConfig

# MIME → user-facing upload kind (platform text-extraction or image bytes).
_MIME_UPLOAD_KIND: dict[str, str] = {
    "image/jpeg": "image",
    "image/png": "image",
    "image/webp": "image",
    "image/gif": "image",
    "image/bmp": "image",
    "image/jp2": "image",
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.ms-excel": "xls",
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "text/plain": "text_file",
    "text/csv": "text_file",
    "text/markdown": "text_file",
    "text/html": "text_file",
    "text/xml": "text_file",
    "application/xml": "text_file",
    "application/json": "text_file",
    "text/json": "text_file",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "audio/mpeg": "audio",
    "audio/amr": "audio",
    "audio/wav": "audio",
    "video/mp4": "video",
}

_UPLOAD_KIND_LABELS: dict[str, str] = {
    "image": "图片",
    "text_file": "TXT / LOG / CSV / JSON / MD / HTML / XML",
    "pdf": "PDF",
    "doc": "DOC",
    "xls": "XLS",
    "ppt": "PPT",
    "pptx": "PPTX",
    "docx": "DOCX",
    "xlsx": "XLSX",
    "audio": "音频（MP3 / WAV / AMR）",
    "video": "视频（MP4）",
}

def document_upload_provider(config: DocumentParsingRuntimeConfig | None) -> ProviderCapability | None:
    if config is None or config.mode != "provider" or config.configuration_error or not config.api_key:
        return None
    return PROVIDERS.get(config.provider)


def document_upload_mimes(config: DocumentParsingRuntimeConfig | None) -> frozenset[str]:
    provider = document_upload_provider(config)
    return provider.mime_types if provider is not None else frozenset()


def upload_capability_kinds(additional_mimes: frozenset[str] = frozenset()) -> list[str]:
    """Upload kinds enabled by the current ``allowed_mimes()`` configuration."""
    kinds: list[str] = []
    seen: set[str] = set()
    for mime in sorted(allowed_mimes(additional_mimes)):
        kind = _MIME_UPLOAD_KIND.get(mime)
        if kind and kind not in seen:
            seen.add(kind)
            kinds.append(kind)
    return kinds


def upload_capabilities_payload(document_config: DocumentParsingRuntimeConfig | None = None) -> dict[str, Any]:
    extra = document_upload_mimes(document_config)
    provider = document_upload_provider(document_config)
    effective = allowed_mimes(extra)
    kinds = upload_capability_kinds(extra)
    return {
        "kinds": kinds,
        "labels": {k: _UPLOAD_KIND_LABELS.get(k, k) for k in kinds},
        "allowed_mimes": sorted(effective),
        "document_parsing": provider.describe() if provider else None,
        "blocked_document_mimes": sorted(extra - effective),
        "max_size_mb": int(OPTION.agent.upload_max_size_mb),
        "max_count": int(OPTION.agent.upload_max_count),
    }


def build_capability_instruction_block(spec: AgentSpec, *, document_config: DocumentParsingRuntimeConfig | None = None, document_available: bool = False) -> str:
    """Instructions appended to the agent system prompt for this turn."""
    upload_kinds = upload_capability_kinds(document_upload_mimes(document_config))
    upload_labels = [_UPLOAD_KIND_LABELS.get(k, k) for k in upload_kinds]
    lines = [
        "## Session input capabilities (platform-enforced)",
        "",
        f"Active LLM model string: `{spec.model}`.",
    ]

    if upload_labels:
        lines.append(
            "Platform upload pipeline accepts and stores these file types by file_id: "
            + ", ".join(upload_labels)
            + "."
        )
        if "image" in upload_kinds:
            lines.append(
                "Current-turn image attachments are sent directly to the model as native image bytes; "
                "the Provider determines whether the selected model accepts them."
            )
        lines.append(
            "When a current or historical session attachment is readable, the read_file system tool "
            "is injected automatically. Use resource={type: attachment, file_id: ...}; follow "
            "next_offset and next_char_offset while has_more is true. read_file only reads text, DOCX and XLSX; "
            "it does not support PDF, PPT/PPTX or image OCR. Do not use read_file to try reading a PDF."
        )
    else:
        lines.append("Platform file upload pipeline: disabled or no MIME types configured.")

    if document_available:
        provider = document_upload_provider(document_config)
        if provider is not None:
            lines.append(f"The parse_document tool is enabled for this Run: {provider.label}; supported formats: "
                         + ", ".join(provider.describe()["formats"]) + ". For PDF, including PDFs with a text layer, "
                         "call parse_document directly instead of first trying read_file. "
                         "When read_file is also available, use it for ordinary text, DOCX and XLSX content; use parse_document for OCR, complex layout and tables within its supported formats. "
                         "It may take longer and recognition may be inaccurate; continue with next_read when has_more is true.")
        else:
            lines.append("parse_document is enabled but its global provider credentials are not configured; parsing is unavailable until configured.")
    lines.extend(
        [
            "",
            "Formats outside the upload list are blocked by the upload policy. Professional document parsing is available only when its tool is explicitly enabled; do not claim unlisted parsing capabilities.",
            "",
            "When the user asks which input types you support, answer ONLY from the lists above. "
            "Do not infer capabilities from your training persona or generic assistant defaults.",
        ]
    )
    return "\n".join(lines)


def augment_instructions_with_capabilities(spec: AgentSpec, *, document_config: DocumentParsingRuntimeConfig | None = None, document_available: bool = False) -> str:
    """Append the capability block to agent instructions."""
    base = (spec.instructions or "").strip()
    block = build_capability_instruction_block(spec, document_config=document_config, document_available=document_available).strip()
    if not base:
        return block
    if block in base:
        return base
    return f"{base}\n\n{block}"
