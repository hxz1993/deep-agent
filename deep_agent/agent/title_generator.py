"""Generate one safe, bounded session title from the first user turn."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent, UserContent
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from deep_agent.files.storage import FileStore
from deep_agent.schemas.attachment import AttachmentRef
from deep_agent.schemas.context_reference import ContextReference

logger = logging.getLogger(__name__)

TITLE_GENERATION_TIMEOUT_SECONDS = 15.0
TITLE_MAX_OUTPUT_TOKENS = 96
TITLE_MAX_TEXT_CHARS = 500
TITLE_MAX_CONTEXT_FACTS = 24
_STABLE_CONTEXT_KEYS = frozenset(
    {"id", "name", "code", "path", "alias", "key", "type", "status", "total", "count"}
)
_TITLE_PROMPT = (
    "为这次新会话生成一个简短、准确的中文标题（3-7个词）。"
    "输入只包含用户首轮的安全摘要，可能附带一张图片。"
    "仅返回标题文本，不要引号、前缀、解释或末尾标点；专用名词无需翻译。"
)
_TITLE_SETTINGS: ModelSettings = {
    "max_tokens": TITLE_MAX_OUTPUT_TOKENS,
    "temperature": 0.2,
}


@dataclass(frozen=True)
class TitleGenerationResult:
    title: str
    image_used: bool


def _normalize_text(value: str) -> str:
    return " ".join((value or "").split()).strip()


def _title_snippet(text: str) -> str:
    """Extract the human request from historical wrappers when present."""
    match = re.search(
        r"<user(?:_message)?>\s*(.*?)\s*</user(?:_message)?>",
        text or "",
        flags=re.DOTALL | re.IGNORECASE,
    )
    return _normalize_text(match.group(1) if match else text)[:TITLE_MAX_TEXT_CHARS]


def _clean_title(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"thinking.*?thinking", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = cleaned.strip().strip("\"'“”‘’")
    if cleaned.lower().startswith("title:"):
        cleaned = cleaned[6:].strip()
    cleaned = cleaned.strip("\"'“”‘’").rstrip(".。！!？?；;").strip("\"'“”‘’")
    if len(cleaned) > 30:
        cleaned = cleaned[:27] + "..."
    return cleaned.strip() or None


def fallback_title(
    user_text: str,
    context_refs: Sequence[ContextReference] | None = None,
    attachments: Sequence[AttachmentRef] | None = None,
) -> str:
    snippet = _title_snippet(user_text)
    if snippet:
        return snippet[:80]
    labels = [_normalize_text(ref.label or "") for ref in context_refs or ()]
    labels = [value for value in labels if value]
    if labels:
        return ("[上下文] " + "、".join(labels))[:80]
    names = [_normalize_text(ref.name or "") for ref in attachments or ()]
    names = [value for value in names if value]
    if names:
        return ("查看 " + "、".join(names))[:80]
    return "新会话"


def _stable_context_facts(value: Any) -> dict[str, Any]:
    facts: dict[str, Any] = {}

    def visit(item: Any, path: str, depth: int) -> None:
        if depth > 3 or len(facts) >= TITLE_MAX_CONTEXT_FACTS:
            return
        if isinstance(item, Mapping):
            for key, child in item.items():
                key_text = str(key)
                next_path = f"{path}.{key_text}" if path else key_text
                if key_text.casefold() in _STABLE_CONTEXT_KEYS and isinstance(
                    child, (str, int, float, bool)
                ):
                    facts[next_path] = child
                    if len(facts) >= TITLE_MAX_CONTEXT_FACTS:
                        return
                elif isinstance(child, (Mapping, list, tuple)):
                    visit(child, next_path, depth + 1)
        elif isinstance(item, (list, tuple)):
            for index, child in enumerate(item[:10]):
                visit(child, f"{path}[{index}]", depth + 1)

    visit(value, "", 0)
    return facts


def _safe_title_input(
    user_text: str,
    context_refs: Sequence[ContextReference] | None,
    attachments: Sequence[AttachmentRef] | None,
) -> str:
    payload: dict[str, Any] = {"user_text": _title_snippet(user_text)}
    contexts = [
        {
            "type": ref.type,
            "label": ref.label,
            "facts": _stable_context_facts(ref.payload),
        }
        for ref in context_refs or ()
    ]
    if contexts:
        payload["contexts"] = contexts
    files = [
        {"name": ref.name or "未命名附件", "mime": ref.mime or "application/octet-stream"}
        for ref in attachments or ()
    ]
    if files:
        payload["attachments"] = files
    return "首轮输入：\n" + json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _first_image(attachments: Sequence[AttachmentRef] | None) -> AttachmentRef | None:
    return next(
        (ref for ref in attachments or () if (ref.mime or "").lower().startswith("image/")),
        None,
    )


async def generate_title(
    *,
    model: Model | str,
    user_text: str,
    context_refs: Sequence[ContextReference] | None = None,
    attachments: Sequence[AttachmentRef] | None = None,
    file_store: FileStore | None = None,
    request_headers: Mapping[str, str] | None = None,
) -> TitleGenerationResult | None:
    """Run exactly one title request with the effective first-turn model."""

    prompt: list[UserContent] = [_safe_title_input(user_text, context_refs, attachments)]
    image_used = False
    image = _first_image(attachments)
    if image is not None:
        if file_store is None:
            logger.warning("Title image was present but no FileStore is available")
            return None
        try:
            data = await file_store.download(image.id, request_headers or {})
        except Exception as exc:  # noqa: BLE001 - title failure must not fail chat
            logger.warning("Title image download failed: %s", type(exc).__name__)
            return None
        prompt.append(
            BinaryContent(data=data, media_type=image.mime or "application/octet-stream")
        )
        image_used = True

    try:
        agent = Agent(model, instructions=_TITLE_PROMPT, output_type=str)
        result = await asyncio.wait_for(
            agent.run(
                prompt,
                model_settings=_TITLE_SETTINGS,
                usage_limits=UsageLimits(request_limit=1),
            ),
            timeout=TITLE_GENERATION_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning(
            "Title generation timed out after %.1fs", TITLE_GENERATION_TIMEOUT_SECONDS
        )
        return None
    except Exception as exc:  # noqa: BLE001 - fallback title remains authoritative
        logger.warning("Title generation failed: %s", type(exc).__name__)
        logger.debug("Title generation traceback", exc_info=True)
        return None

    raw = result.output if isinstance(result.output, str) else str(result.output or "")
    title = _clean_title(raw)
    return TitleGenerationResult(title=title, image_used=image_used) if title else None
