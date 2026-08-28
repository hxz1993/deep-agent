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

# MIME → user-facing upload kind (platform text-extraction or image bytes).
_MIME_UPLOAD_KIND: dict[str, str] = {
    "image/jpeg": "image",
    "image/png": "image",
    "image/webp": "image",
    "image/gif": "image",
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
    "audio/wav": "audio",
    "video/mp4": "video",
}

_UPLOAD_KIND_LABELS: dict[str, str] = {
    "image": "图片",
    "text_file": "TXT / LOG / CSV / JSON / MD / HTML / XML",
    "docx": "DOCX",
    "xlsx": "XLSX",
    "audio": "音频（MP3 / WAV）",
    "video": "视频（MP4）",
}

def upload_capability_kinds() -> list[str]:
    """Upload kinds enabled by the current ``allowed_mimes()`` configuration."""
    kinds: list[str] = []
    seen: set[str] = set()
    for mime in sorted(allowed_mimes()):
        kind = _MIME_UPLOAD_KIND.get(mime)
        if kind and kind not in seen:
            seen.add(kind)
            kinds.append(kind)
    return kinds


def upload_capabilities_payload() -> dict[str, Any]:
    kinds = upload_capability_kinds()
    return {
        "kinds": kinds,
        "labels": {k: _UPLOAD_KIND_LABELS.get(k, k) for k in kinds},
        "allowed_mimes": sorted(allowed_mimes()),
        "max_size_mb": int(OPTION.agent.upload_max_size_mb),
        "max_count": int(OPTION.agent.upload_max_count),
    }


def build_capability_instruction_block(spec: AgentSpec) -> str:
    """Instructions appended to the agent system prompt for this turn."""
    upload_kinds = upload_capability_kinds()
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
            "next_offset while has_more is true."
        )
    else:
        lines.append("Platform file upload pipeline: disabled or no MIME types configured.")

    lines.extend(
        [
            "",
            "PDF, PPT/PPTX, legacy DOC, and formats outside the upload list are not available; "
            "PDF and presentation parsing require dedicated tools if introduced later.",
            "",
            "When the user asks which input types you support, answer ONLY from the lists above. "
            "Do not infer capabilities from your training persona or generic assistant defaults.",
        ]
    )
    return "\n".join(lines)


def augment_instructions_with_capabilities(spec: AgentSpec) -> str:
    """Append the capability block to agent instructions."""
    base = (spec.instructions or "").strip()
    block = build_capability_instruction_block(spec).strip()
    if not base:
        return block
    if block in base:
        return base
    return f"{base}\n\n{block}"
