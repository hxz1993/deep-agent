from __future__ import annotations

import re

from httpx2 import DecodingError
from pydantic_ai.exceptions import UsageLimitExceeded
from deep_agent.agent.context.compaction import ContextWindowExceededError

_LIMIT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"(?:The next request would exceed|Exceeded) the request_limit of (\d+)"),
        "本轮对话模型请求次数已达上限（{0} 次），请简化问题或开启新对话后重试。",
    ),
    (
        re.compile(r"(?:The next request would exceed|Exceeded) the total_tokens_limit of (\d+)"),
        "本轮对话 token 用量已达上限（{0}），请缩短上下文或开启新对话后重试。",
    ),
    (
        re.compile(r"(?:The next request would exceed|Exceeded) the input_tokens_limit of (\d+)"),
        "本轮对话输入 token 已达上限（{0}），请缩短上下文或开启新对话后重试。",
    ),
    (
        re.compile(r"(?:The next request would exceed|Exceeded) the output_tokens_limit of (\d+)"),
        "本轮对话输出 token 已达上限（{0}），请简化问题或开启新对话后重试。",
    ),
    (
        re.compile(r"The next tool call\(s\) would exceed the tool_calls_limit of (\d+)"),
        "本轮对话工具调用次数已达上限（{0} 次），请简化问题或开启新对话后重试。",
    ),
]


def format_stream_error(exc: Exception) -> str:
    """Map pydantic_ai usage-limit errors to user-facing Chinese messages."""
    if isinstance(exc, DecodingError):
        return "模型响应传输解码失败，本轮已停止。这不是结构化输出格式校验错误，请重试。"
    if isinstance(exc, UsageLimitExceeded):
        text = str(exc)
        for pattern, template in _LIMIT_PATTERNS:
            match = pattern.search(text)
            if match:
                return template.format(match.group(1))
    if isinstance(exc, ContextWindowExceededError):
        return str(exc)
    return str(exc)
