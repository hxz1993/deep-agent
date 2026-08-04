from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Callable

from pydantic_ai.messages import (
    BaseToolCallPart,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    ThinkingPart,
    ToolReturnPart,
    UserPromptPart,
)

from deep_agent.agent.context_references import (
    context_reference_from_dict,
    context_references_from_content,
    format_context_reference_prompt,
    is_context_reference,
)

try:
    import tiktoken
except ImportError:  # pragma: no cover - production requirements include it
    tiktoken = None


REQUEST_ENVELOPE_TOKENS = 256
MESSAGE_OVERHEAD_TOKENS = 8
PART_OVERHEAD_TOKENS = 4
TOOL_DEFINITION_OVERHEAD_TOKENS = 32
MEDIA_CONTENT_TOKENS = 1_024

_OPENAI_OFFICIAL_HOSTS = {"api.openai.com"}
_ASCII_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_WHITESPACE_RE = re.compile(r"\s+")


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return f"[binary:{len(value)} bytes]"
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if is_dataclass(value):
        return _jsonable(asdict(value))
    return str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x3040 <= code <= 0x30FF
        or 0xAC00 <= code <= 0xD7AF
    )


def conservative_text_tokens(text: str) -> int:
    """Conservative text estimate for unknown/private models.

    CJK and complex Unicode are counted approximately one token per code point.
    ASCII prose is grouped at four characters per token, while punctuation is
    deliberately counted more conservatively for code/JSON-heavy content.
    """
    if not text:
        return 0
    total = 0
    index = 0
    while index < len(text):
        char = text[index]
        word = _ASCII_WORD_RE.match(text, index)
        if word is not None:
            total += math.ceil(len(word.group(0)) / 4)
            index = word.end()
            continue
        whitespace = _WHITESPACE_RE.match(text, index)
        if whitespace is not None:
            total += math.ceil(len(whitespace.group(0)) / 8)
            index = whitespace.end()
            continue
        if _is_cjk(char) or ord(char) > 0xFFFF or unicodedata.combining(char):
            total += 1
        else:
            # Punctuation and symbols dominate token counts in code and JSON.
            total += 1
        index += 1
    return max(1, total)


def _official_openai_model(model: str, base_url: str | None) -> str | None:
    prefix, sep, name = model.partition(":")
    if sep and prefix not in {"openai", "openai-chat"}:
        return None
    model_name = name if sep else model
    if base_url:
        from urllib.parse import urlparse

        host = (urlparse(base_url).hostname or "").lower()
        if host not in _OPENAI_OFFICIAL_HOSTS:
            return None
    return model_name


@dataclass(frozen=True)
class TokenEstimator:
    source: str
    _counter: Callable[[str], int] | None = None

    @classmethod
    def for_model(cls, model: str, base_url: str | None = None) -> "TokenEstimator":
        model_name = _official_openai_model(model, base_url)
        if model_name and tiktoken is not None:
            try:
                encoding = tiktoken.encoding_for_model(model_name)
            except KeyError:
                pass
            else:
                return cls(
                    source="model_tokenizer",
                    _counter=lambda text: len(encoding.encode(text)),
                )
        return cls(source="conservative_fallback")

    @classmethod
    def conservative(cls) -> "TokenEstimator":
        return cls(source="conservative_fallback")

    def text(self, value: Any) -> int:
        text = value if isinstance(value, str) else canonical_json(value)
        if not text:
            return 0
        if self._counter is not None:
            return max(1, self._counter(text))
        return conservative_text_tokens(text)

    def value(self, value: Any) -> int:
        if isinstance(value, dict):
            if is_context_reference(value):
                try:
                    return self.text(
                        format_context_reference_prompt(
                            [context_reference_from_dict(value)]
                        )
                    )
                except ValueError:
                    return self.text(value)
            kind = str(value.get("kind") or value.get("part_kind") or "")
            if kind in {
                "audio-url",
                "binary",
                "document-url",
                "image-url",
                "video-url",
                "attachment-ref",
            }:
                return MEDIA_CONTENT_TOKENS
            return self.text(value)
        if isinstance(value, (list, tuple)):
            refs = context_references_from_content(value)
            if refs:
                text_chunks: list[str] = []
                remaining: list[Any] = []
                for item in value:
                    if isinstance(item, str):
                        text_chunks.append(item)
                    elif isinstance(item, dict) and item.get("kind") == "text-content":
                        text_chunks.append(str(item.get("content", "")))
                    elif not is_context_reference(item):
                        remaining.append(item)
                projected = format_context_reference_prompt(
                    refs,
                    "\n".join(chunk for chunk in text_chunks if chunk),
                )
                return self.text(projected) + sum(
                    self.value(item) for item in remaining
                )
            return sum(self.value(item) for item in value)
        return self.text(value)

    def part(self, part: Any) -> int:
        tokens = PART_OVERHEAD_TOKENS
        if isinstance(part, (SystemPromptPart, UserPromptPart, ThinkingPart)):
            return tokens + self.value(part.content)
        if isinstance(part, BaseToolCallPart):
            return (
                tokens
                + self.text(part.tool_name)
                + self.text(part.args_as_json_str())
            )
        if isinstance(part, ToolReturnPart):
            return tokens + self.text(part.tool_name) + self.value(part.content)
        content = getattr(part, "content", None)
        if content is not None:
            return tokens + self.value(content)
        return tokens + self.text(_jsonable(part))

    def message(self, message: ModelMessage) -> int:
        tokens = MESSAGE_OVERHEAD_TOKENS
        if isinstance(message, ModelRequest) and message.instructions:
            tokens += self.text(message.instructions)
        parts = getattr(message, "parts", ())
        return tokens + sum(self.part(part) for part in parts)

    def messages(self, messages: list[ModelMessage]) -> int:
        return sum(self.message(message) for message in messages)

    def response_output(self, response: ModelResponse) -> int:
        return max(1, sum(self.part(part) for part in response.parts))

    def request_parameters(self, parameters: Any | None) -> int:
        if parameters is None:
            return 0
        total = 0
        for tool in [
            *list(getattr(parameters, "function_tools", ()) or ()),
            *list(getattr(parameters, "output_tools", ()) or ()),
        ]:
            total += TOOL_DEFINITION_OVERHEAD_TOKENS + self.text(_jsonable(tool))
        for native in list(getattr(parameters, "native_tools", ()) or ()):
            total += TOOL_DEFINITION_OVERHEAD_TOKENS + self.text(_jsonable(native))
        output_object = getattr(parameters, "output_object", None)
        if output_object is not None:
            total += TOOL_DEFINITION_OVERHEAD_TOKENS + self.text(_jsonable(output_object))
        instruction_parts = getattr(parameters, "instruction_parts", None)
        if instruction_parts:
            total += sum(self.value(part) for part in instruction_parts)
        prompted_template = getattr(parameters, "prompted_output_template", None)
        if prompted_template:
            total += self.text(prompted_template)
        return total

    def request(
        self,
        messages: list[ModelMessage],
        parameters: Any | None = None,
    ) -> tuple[int, int, int]:
        message_tokens = self.messages(messages)
        parameter_tokens = self.request_parameters(parameters)
        return (
            message_tokens + parameter_tokens + REQUEST_ENVELOPE_TOKENS,
            message_tokens,
            parameter_tokens,
        )
