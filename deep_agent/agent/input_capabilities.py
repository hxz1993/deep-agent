"""Platform input capability model and agent instruction augmentation.

Three layers (do not conflate):

1. **LLM native** — ``input_modalities`` on the preset (text / image / audio / video / pdf).
2. **Upload pipeline** — files accepted at POST /uploads; non-images become ``extracted_text``.
3. **Agent tools** — only claim after a tool is registered and enabled (not listed here yet).
"""

from __future__ import annotations

from typing import Any

from deep_agent.agent.attachments import model_supports_image_input
from deep_agent.agent.media_pipeline import allowed_mimes
from deep_agent.agent.spec import AgentSpec

# MIME → user-facing upload kind (platform text-extraction or image bytes).
_MIME_UPLOAD_KIND: dict[str, str] = {
    "image/jpeg": "image",
    "image/png": "image",
    "image/webp": "image",
    "image/gif": "image",
    "text/plain": "text_file",
    "text/csv": "text_file",
    "application/pdf": "pdf_text",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx_text",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx_text",
}

_UPLOAD_KIND_LABELS: dict[str, str] = {
    "image": "图片",
    "text_file": "TXT / CSV",
    "pdf_text": "PDF（抽文本）",
    "docx_text": "DOCX（抽文本）",
    "xlsx_text": "XLSX（抽文本）",
}

_LLM_MODALITY_LABELS: dict[str, str] = {
    "text": "文本",
    "image": "图像",
    "audio": "音频",
    "video": "视频",
    "pdf": "PDF（原生）",
}


def resolve_llm_input_modalities(spec: AgentSpec) -> list[str]:
    """Return only the LLM-native modalities persisted on ``llm_models``."""
    return list(spec.input_modalities or [])


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
    }


def build_capability_instruction_block(spec: AgentSpec) -> str:
    """Instructions appended to the agent system prompt for this turn."""
    llm_modalities = resolve_llm_input_modalities(spec)
    llm_labels = [_LLM_MODALITY_LABELS.get(m, m) for m in llm_modalities]
    vision = model_supports_image_input(
        db_modalities=spec.input_modalities,
    )

    upload_kinds = upload_capability_kinds()
    upload_labels = [_UPLOAD_KIND_LABELS.get(k, k) for k in upload_kinds]

    lines = [
        "## Session input capabilities (platform-enforced)",
        "",
        f"Active LLM model string: `{spec.model}`.",
        f"LLM native input modalities: {', '.join(llm_labels) if llm_labels else 'unknown'}.",
    ]

    if upload_labels:
        lines.append(
            "Platform upload pipeline (NOT LLM-native multimodal — text is injected into the prompt): "
            + ", ".join(upload_labels)
            + "."
        )
        if "image" in upload_kinds:
            if vision:
                lines.append(
                    "User-attached images are sent as native image bytes when the model supports vision."
                )
            else:
                lines.append(
                    "This model does NOT support native image input; attached images are not sent as pixels "
                    "(only a placeholder unless a future vision tool is enabled)."
                )
        lines.append(
            "PDF / Office uploads are converted to extracted text at upload time, not read as native PDF/Office."
        )
    else:
        lines.append("Platform file upload pipeline: disabled or no MIME types configured.")

    lines.extend(
        [
            "",
            "NOT available in this session unless a dedicated agent tool is later enabled: "
            "audio (mp3/wav), video (mp4), legacy DOC/PPT/PPTX, and other formats outside the upload list.",
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
