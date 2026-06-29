from __future__ import annotations

import asyncio
from dataclasses import dataclass

from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel


def text_only_model(text: str, **kwargs: object) -> TestModel:
    """Return text only; ``call_tools=[]`` avoids TestModel's default of calling every agent tool."""
    return TestModel(call_tools=[], custom_output_text=text, **kwargs)


def tool_call_model(*tools: str, text: str = "done", **kwargs: object) -> TestModel:
    """Invoke specific tools, then return ``text`` as the final assistant message."""
    return TestModel(call_tools=list(tools), custom_output_text=text, **kwargs)


@dataclass
class SlowStreamTracker:
    total_chunks: int
    yielded_chunks: int = 0


def build_slow_stream_model(
    *,
    chunks: list[str],
    delay_s: float = 0.05,
) -> tuple[FunctionModel, SlowStreamTracker]:
    tracker = SlowStreamTracker(total_chunks=len(chunks))

    async def stream_fn(messages, agent_info):  # noqa: ARG001
        for chunk in chunks:
            await asyncio.sleep(delay_s)
            tracker.yielded_chunks += 1
            yield chunk

    return FunctionModel(stream_function=stream_fn), tracker
