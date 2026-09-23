from __future__ import annotations

import json
import hashlib
import math
import re
import unicodedata
from io import BytesIO
from dataclasses import asdict, dataclass, fields, is_dataclass, replace
from typing import Any, Callable
from uuid import uuid4

from pydantic_ai.usage import RequestUsage
from pydantic_ai_harness.compaction import estimate_context_tokens, estimate_token_count

from pydantic_ai.messages import (
    BaseToolCallPart,
    BinaryContent,
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
USAGE_ANCHOR_METADATA = "deepagent_context_usage"


@dataclass(frozen=True)
class RequestEstimate:
    tokens: int
    message_tokens: int
    parameter_tokens: int
    source: str = "estimated"
    anchor_id: str | None = None
    fallback_reason: str | None = None


def _fingerprint(value: Any) -> str:
    # Hash binary content itself, not just its length; persist only the digest.
    def normalize(item):
        if isinstance(item, bytes):
            return {"sha256": hashlib.sha256(item).hexdigest()}
        if is_dataclass(item):
            return normalize(asdict(item))
        if isinstance(item, dict):
            return {str(k): normalize(v) for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [normalize(v) for v in item]
        if isinstance(item, (set, frozenset)):
            return sorted((normalize(v) for v in item), key=canonical_json)
        return item
    return hashlib.sha256(canonical_json(normalize(value)).encode()).hexdigest()


def _history_fingerprint(messages: list[ModelMessage]) -> str:
    # Usage, event ordinals and response IDs do not change model-visible text.
    # Strip bookkeeping only at the SDK Part boundary, never inside tool JSON
    # or schemas where "metadata" and "timestamp" are legitimate input keys.
    def part_data(part):
        # asdict(part) deep-copies metadata before we can discard it; PTC may
        # carry a bounded but sizable diagnostic document there.
        value = ({field.name: getattr(part, field.name) for field in fields(part)
                  if field.name not in {"metadata", "timestamp"}}
                 if is_dataclass(part) else _jsonable(part))
        return {key: item for key, item in value.items() if key not in {"metadata", "timestamp"}} if isinstance(value, dict) else value
    return _fingerprint([(message.kind, [part_data(part) for part in message.parts]) for message in messages])


def _instructions(messages: list[ModelMessage], parameters: Any | None = None) -> str | None:
    parts = getattr(parameters, "instruction_parts", None)
    if parts is not None:
        return "\n\n".join(part.content for part in parts).strip() or None
    requests = [message for message in messages if isinstance(message, ModelRequest)]
    if not requests:
        return None
    last = requests[-1]
    if last.instructions is not None:
        return last.instructions
    if len(requests) > 1 and all(part.part_kind in {"tool-return", "retry-prompt"} for part in last.parts):
        return requests[-2].instructions
    return None


def _normalized_instructions(messages: list[ModelMessage], parameters: Any | None) -> list[ModelMessage]:
    instruction = _instructions(messages, parameters)
    last_request = next((i for i in range(len(messages) - 1, -1, -1)
                         if isinstance(messages[i], ModelRequest)), None)
    return [replace(message, instructions=instruction if index == last_request else None)
            if isinstance(message, ModelRequest) else message
            for index, message in enumerate(messages)]

_DEEPSEEK_PATCH_SIZE = 14
_DEEPSEEK_DOWNSAMPLE_RATIO = 3
_DEEPSEEK_MAX_IMAGE_TOKENS = 384
_DEEPSEEK_COMPRESS_PAD = 3
_DEEPSEEK_MAX_WIDTH_HEIGHT_RATIO = 8
_DEEPSEEK_MIN_PIXELS = 384 * 384

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


def _ceil_div(value: int, divisor: int) -> int:
    return (value + divisor - 1) // divisor


def _deepseek_grid_tokens(grid_height: int, grid_width: int) -> int:
    tokens = grid_height * (grid_width + 1) + 2
    if grid_height % 2 == 1:
        tokens += grid_width + 1
    tokens += (_ceil_div(grid_height, 2) * (grid_width + 1) % 2) * 2
    return tokens


def _deepseek_solve_resize(
    height: int,
    width: int,
    budget: int,
) -> tuple[int, int, int, int, int]:
    aspect = height / width
    ideal_grid_width = math.sqrt((budget - 2) / aspect + 0.25) - 0.5
    ideal_grid_height = ideal_grid_width * aspect
    if ideal_grid_width < 1:
        solved_grid_width = 1
        solved_grid_height = (budget - 2) // (solved_grid_width + 1)
        if solved_grid_height % 2 == 1:
            solved_grid_height -= 1
        best_width = solved_grid_width * _DEEPSEEK_PATCH_SIZE * _DEEPSEEK_DOWNSAMPLE_RATIO
        best_height = solved_grid_height * _DEEPSEEK_PATCH_SIZE * _DEEPSEEK_DOWNSAMPLE_RATIO
    elif ideal_grid_height < 2:  # retained for parity with the provider algorithm
        solved_grid_height = 2
        solved_grid_width = (budget - 2) // solved_grid_height - 1
        if solved_grid_width <= 1:
            raise ValueError("no DeepSeek image grid fits the token budget")
        best_width = solved_grid_width * _DEEPSEEK_PATCH_SIZE * _DEEPSEEK_DOWNSAMPLE_RATIO
        best_height = solved_grid_height * _DEEPSEEK_PATCH_SIZE * _DEEPSEEK_DOWNSAMPLE_RATIO
    else:
        solved_grid_width = math.trunc(ideal_grid_width)
        solved_grid_height = math.trunc(ideal_grid_height)
        if solved_grid_height % 2 == 1:
            solved_grid_height -= 1
        width_scale = (
            solved_grid_width * _DEEPSEEK_PATCH_SIZE * _DEEPSEEK_DOWNSAMPLE_RATIO / width
        )
        height_scale = (
            solved_grid_height * _DEEPSEEK_PATCH_SIZE * _DEEPSEEK_DOWNSAMPLE_RATIO / height
        )
        scale = min(width_scale, height_scale)
        best_width = math.trunc(width * scale / _DEEPSEEK_PATCH_SIZE) * _DEEPSEEK_PATCH_SIZE
        best_height = math.trunc(height * scale / _DEEPSEEK_PATCH_SIZE) * _DEEPSEEK_PATCH_SIZE
    grid_height = _ceil_div(best_height // _DEEPSEEK_PATCH_SIZE, _DEEPSEEK_DOWNSAMPLE_RATIO)
    grid_width = _ceil_div(best_width // _DEEPSEEK_PATCH_SIZE, _DEEPSEEK_DOWNSAMPLE_RATIO)
    return grid_height, grid_width, best_height, best_width, _deepseek_grid_tokens(grid_height, grid_width)


def _deepseek_resize_once(width: int, height: int) -> tuple[int, int, int, int, int]:
    clamped_width = width
    clamped_height = height
    if clamped_width > clamped_height * _DEEPSEEK_MAX_WIDTH_HEIGHT_RATIO:
        clamped_width = clamped_height * _DEEPSEEK_MAX_WIDTH_HEIGHT_RATIO
    pixels = clamped_width * clamped_height
    if 0 < pixels < _DEEPSEEK_MIN_PIXELS:
        scale = math.sqrt(_DEEPSEEK_MIN_PIXELS / pixels)
        clamped_width = math.trunc(clamped_width * scale)
        clamped_height = math.trunc(clamped_height * scale)
    padded_width = _ceil_div(clamped_width, _DEEPSEEK_PATCH_SIZE) * _DEEPSEEK_PATCH_SIZE
    padded_height = _ceil_div(clamped_height, _DEEPSEEK_PATCH_SIZE) * _DEEPSEEK_PATCH_SIZE
    grid_height = _ceil_div(padded_height // _DEEPSEEK_PATCH_SIZE, _DEEPSEEK_DOWNSAMPLE_RATIO)
    grid_width = _ceil_div(padded_width // _DEEPSEEK_PATCH_SIZE, _DEEPSEEK_DOWNSAMPLE_RATIO)
    budget = _DEEPSEEK_MAX_IMAGE_TOKENS - _DEEPSEEK_COMPRESS_PAD
    result = (
        grid_height,
        grid_width,
        padded_height,
        padded_width,
        _deepseek_grid_tokens(grid_height, grid_width),
    )
    if result[4] > budget:
        result = _deepseek_solve_resize(clamped_height, clamped_width, budget)
        reduced = budget
        while result[4] > budget:
            reduced -= 1
            result = _deepseek_solve_resize(clamped_height, clamped_width, reduced)
    return (*result[:4], result[4] + _DEEPSEEK_COMPRESS_PAD)


def deepseek_image_tokens(width: int, height: int) -> int:
    """DeepSeek v4 provider image-token formula with worst-case alignment padding."""

    if width <= 0 or height <= 0:
        raise ValueError("image dimensions must be positive")
    result = _deepseek_resize_once(width, height)
    for _iteration in range(1, 10):
        next_result = _deepseek_resize_once(result[3], result[2])
        if next_result == result:
            return result[4]
        result = next_result
    raise ValueError(f"DeepSeek image resize did not converge for {width}x{height}")


def _is_deepseek_v4_vision(model: str, provider_type: str | None) -> bool:
    normalized_model = model.lower()
    normalized_provider = (provider_type or "").lower()
    return (
        normalized_provider == "deepseek"
        and "v4" in normalized_model
        and "vision" in normalized_model
    )


def _official_openai_model(
    model: str,
    base_url: str | None,
    provider_type: str | None = None,
) -> str | None:
    if provider_type is not None:
        return model if provider_type == "openai" else None
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
    _image_counter: Callable[[int, int], int] | None = None
    model_identity: str | None = None

    @classmethod
    def for_model(
        cls,
        model: str,
        base_url: str | None = None,
        *,
        provider_type: str | None = None,
    ) -> "TokenEstimator":
        identity = _fingerprint((provider_type, base_url, model))
        image_counter = deepseek_image_tokens if _is_deepseek_v4_vision(model, provider_type) else None
        model_name = _official_openai_model(model, base_url, provider_type)
        if model_name and tiktoken is not None:
            try:
                encoding = tiktoken.encoding_for_model(model_name)
            except KeyError:
                pass
            else:
                return cls(
                    source="model_tokenizer",
                    _counter=lambda text: len(encoding.encode(text)),
                    _image_counter=image_counter,
                    model_identity=identity,
                )
        return cls(source="conservative_fallback", _image_counter=image_counter, model_identity=identity)

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
        if isinstance(value, BinaryContent):
            if value.media_type.startswith("image/") and self._image_counter is not None:
                try:
                    from PIL import Image

                    with Image.open(BytesIO(value.data)) as image:
                        return self._image_counter(*image.size)
                except Exception:
                    return MEDIA_CONTENT_TOKENS
            return MEDIA_CONTENT_TOKENS
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
        parts = getattr(message, "parts", ())
        return tokens + sum(self.part(part) for part in parts)

    def messages(self, messages: list[ModelMessage]) -> int:
        return sum(self.message(message) for message in messages) + self.text(_instructions(messages) or "")

    def response_output(self, response: ModelResponse) -> int:
        return max(1, sum(self.part(part) for part in response.parts))

    def request_parameters(self, parameters: Any | None, *, include_instructions: bool = True) -> int:
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
        if instruction_parts and include_instructions:
            total += self.text("\n\n".join(part.content for part in instruction_parts))
        prompted_template = getattr(parameters, "prompted_output_template", None)
        if prompted_template:
            total += self.text(prompted_template)
        return total

    def request(
        self,
        messages: list[ModelMessage],
        parameters: Any | None = None,
    ) -> tuple[int, int, int]:
        result = self.estimate_request(messages, parameters)
        return result.tokens, result.message_tokens, result.parameter_tokens

    def record_usage_anchor(self, messages: list[ModelMessage], parameters: Any,
                            response: ModelResponse) -> None:
        if not self.model_identity or response.usage.input_tokens <= 0:
            return
        response.metadata = {**(response.metadata or {}), USAGE_ANCHOR_METADATA: {
            "version": 1, "id": uuid4().hex, "model": self.model_identity,
            "history": _history_fingerprint([*messages, response]),
            "parameters": _fingerprint(parameters),
            "instructions": _fingerprint(_instructions(messages, parameters)),
        }}

    def estimate_request(self, messages: list[ModelMessage], parameters: Any | None = None) -> RequestEstimate:
        normalized = _normalized_instructions(messages, parameters)
        message_tokens = self.messages(normalized)
        parameter_tokens = self.request_parameters(
            parameters, include_instructions=not any(isinstance(m, ModelRequest) for m in messages))
        reason = "usage_unavailable"
        anchor_index = None
        anchor = None
        # A newer unusable response must not silently revive an older baseline.
        for index in range(len(messages) - 1, -1, -1):
            response = messages[index]
            if not isinstance(response, ModelResponse):
                continue
            data = (response.metadata or {}).get(USAGE_ANCHOR_METADATA) or {}
            if response.usage.input_tokens <= 0:
                reason = "usage_unavailable"
            elif data.get("version") != 1:
                reason = "anchor_unverified"
            elif data.get("model") != self.model_identity:
                reason = "model_changed"
            elif data.get("history") != _history_fingerprint(messages[:index + 1]):
                reason = "history_changed"
            elif parameters is None:
                reason = "request_parameters_unavailable"
            elif data.get("parameters") != _fingerprint(parameters):
                reason = "request_parameters_changed"
            elif data.get("instructions") != _fingerprint(_instructions(messages, parameters)):
                reason = "instructions_changed"
            else:
                anchor_index, anchor = index, data
            break

        # Only the verified anchor is visible to Harness. Never alter persisted Usage.
        normalized = [replace(message, usage=RequestUsage())
                      if isinstance(message, ModelResponse) and index != anchor_index else message
                      for index, message in enumerate(normalized)]
        if anchor_index is not None:
            # Unchanged instructions were paid for by the anchor. Avoid Harness
            # treating moving the effective instructions to the last request as a change.
            normalized = [replace(m, instructions=None) if isinstance(m, ModelRequest) else m
                          for m in normalized]
        base = estimate_context_tokens(normalized, tokenizer=self.text)
        tail = normalized if anchor_index is None else normalized[anchor_index + 1:]
        # Harness counts text; retain our media, context-ref projection, message
        # envelope and structured-part accounting only for the unmeasured suffix.
        supplement = self.messages(tail) - estimate_token_count(tail, tokenizer=self.text)
        tokens = base + supplement
        if anchor_index is None:
            tokens += parameter_tokens + REQUEST_ENVELOPE_TOKENS
        return RequestEstimate(max(0, tokens), message_tokens, parameter_tokens,
                               "provider_usage_plus_estimate" if anchor else "estimated",
                               anchor.get("id") if anchor else None,
                               None if anchor else reason)
