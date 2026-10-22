"""Generate one safe, bounded session title from the first user turn."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from pydantic_ai import Agent, capture_run_messages
from pydantic_ai.messages import BinaryContent, ModelResponse, UserContent
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from deep_agent.files.storage import FileStore
from deep_agent.agent.observability import log_event
from deep_agent.schemas.attachment import AttachmentRef
from deep_agent.schemas.context_reference import ContextReference
from deep_agent.models.http_transport import model_http_settings

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
    "thinking": False,
}


def _title_model_settings(model: Model | str, provider_type: str | None) -> ModelSettings:
    settings: ModelSettings = dict(_TITLE_SETTINGS)
    provider = provider_type or getattr(model, "system", None)
    while isinstance(model, WrapperModel):
        model = model.wrapped
    if isinstance(model, Model) and model.settings:
        settings["extra_headers"] = dict(model.settings.get("extra_headers", {}))
    # New aliases may not yet have an SDK thinking profile. Use the provider's
    # native off switch without mutating the model shared with the main run.
    if provider == "deepseek" and isinstance(model, OpenAIResponsesModel):
        settings["openai_reasoning_effort"] = "none"
    elif provider in {"deepseek", "moonshotai"} and isinstance(model, OpenAIChatModel):
        settings["extra_body"] = {"thinking": {"type": "disabled"}}
    return model_http_settings(settings)


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
    provider_type: str | None = None,
    diagnostic_fields: Mapping[str, Any] | None = None,
    usage_collector: list[ModelResponse] | None = None,
) -> TitleGenerationResult | None:
    """Run exactly one title request with the effective first-turn model."""

    started_at = time.perf_counter()
    fields = dict(diagnostic_fields or {})

    def record(event: str, **facts: Any) -> None:
        log_event(event, **{
            **fields,
            "duration_ms": round((time.perf_counter() - started_at) * 1000),
            **facts,
        })

    record("title_generation_started")
    prompt: list[UserContent] = [_safe_title_input(user_text, context_refs, attachments)]
    image_used = False
    image = _first_image(attachments)
    if image is not None:
        if file_store is None:
            record("title_generation_failed", phase="image_download", reason="file_store_unavailable")
            logger.warning("Title image was present but no FileStore is available")
            return None
        try:
            data = await file_store.download(image.id, request_headers or {})
        except asyncio.CancelledError:
            record("title_generation_cancelled", phase="image_download")
            raise
        except Exception as exc:  # noqa: BLE001 - title failure must not fail chat
            record("title_generation_failed", phase="image_download", error_type=type(exc).__name__)
            logger.warning("Title image download failed: %s", type(exc).__name__)
            return None
        prompt.append(
            BinaryContent(data=data, media_type=image.mime or "application/octet-stream")
        )
        image_used = True

    captured = []
    def collect_usage():
        if usage_collector is not None:
            usage_collector.extend(message for message in captured if isinstance(message, ModelResponse))

    try:
        agent = Agent(model, instructions=_TITLE_PROMPT, output_type=str)
        with capture_run_messages() as captured:
            result = await asyncio.wait_for(
                agent.run(
                    prompt,
                    model_settings=_title_model_settings(model, provider_type),
                    usage_limits=UsageLimits(request_limit=1),
                ),
                timeout=TITLE_GENERATION_TIMEOUT_SECONDS,
            )
    except TimeoutError:
        collect_usage()
        record("title_generation_failed", phase="model", reason="timeout", error_type="TimeoutError")
        logger.warning(
            "Title generation timed out after %.1fs", TITLE_GENERATION_TIMEOUT_SECONDS
        )
        return None
    except asyncio.CancelledError:
        collect_usage()
        record("title_generation_cancelled", phase="model")
        raise
    except Exception as exc:  # noqa: BLE001 - fallback title remains authoritative
        collect_usage()
        reason = "output_token_limit" if str(exc).startswith("Model token limit (") else "model_error"
        record("title_generation_failed", phase="model", reason=reason,
               error_type=type(exc).__name__, http_status=getattr(exc, "status_code", None))
        logger.warning("Title generation failed: %s", type(exc).__name__)
        logger.debug("Title generation traceback", exc_info=True)
        return None

    collect_usage()
    raw = result.output if isinstance(result.output, str) else str(result.output or "")
    title = _clean_title(raw)
    if title:
        record("title_generation_completed", input_tokens=result.usage.input_tokens,
               output_tokens=result.usage.output_tokens)
    else:
        record("title_generation_failed", phase="output", reason="empty_title")
    return TitleGenerationResult(title=title, image_used=image_used) if title else None
