from __future__ import annotations

import asyncio
from dataclasses import dataclass

from pydantic_ai.models.function import FunctionModel


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
