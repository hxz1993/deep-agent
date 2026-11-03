"""Run-scoped image caches, reservations and cleanup; never an authority store."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import anyio
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import BinaryContent, ImageUrl

from deep_agent.agent.deps import AgentDeps
from deep_agent.skills.runtime import ResolvedSkill

from .limits import MAX_CONTEXT_IMAGES, MAX_CONTEXT_IMAGE_BYTES, MAX_IMAGE_SOURCE_BYTES


def _images_in(value):
    if isinstance(value, BinaryContent):
        if value.media_type.startswith("image/"):
            yield value
    elif isinstance(value, ImageUrl):
        yield value
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _images_in(item)


class ImageViewState:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.pending: dict[object, tuple[int, int]] = {}
        self.delivered: dict[int, tuple[BinaryContent, int]] = {}
        self.cache: OrderedDict[tuple, Any] = OrderedDict()
        self.cache_sizes: dict[tuple, int] = {}
        self.tasks: dict[tuple, asyncio.Task] = {}
        self.waiters: dict[asyncio.Task, int] = {}
        self.closed = False

    def context_totals(self, ctx):
        seen = set()
        count = size = 0
        for message in ctx.messages:
            for part in message.parts:
                for image in _images_in(getattr(part, "content", None)):
                    # The same occurrence is represented once in an SDK
                    # content tree; identical bytes in another occurrence count.
                    seen.add(id(image))
                    count += 1
                    if isinstance(image, BinaryContent):
                        size += len(image.data)
                    elif image.url.startswith("data:"):
                        header, _, content = image.url.partition(",")
                        if ";base64" in header.lower():
                            size += max(0, len(content) * 3 // 4 - len(content) + len(content.rstrip("=")))
                        else:
                            from urllib.parse import unquote_to_bytes
                            size += len(unquote_to_bytes(content))
                    else:
                        # A pre-existing remote URL has no inspectable bytes.
                        # Charge the source ceiling rather than treating unknown
                        # size as zero or making an unauthorised extra fetch.
                        size += MAX_IMAGE_SOURCE_BYTES
        for identity, (image, step) in list(self.delivered.items()):
            if identity in seen or ctx.run_step > step:
                # The SDK now owns this returned image. If compaction later
                # removes or copies it, use the actual next-step messages,
                # never resurrect the pre-compaction copy from reservations.
                self.delivered.pop(identity, None)
            else:
                count += 1
                size += len(image.data)
        return count, size

    async def reserve(self, ctx, count):
        token = object()
        async with self.lock:
            if self.closed:
                raise ModelRetry("本轮图片查看已结束，请在新请求中重试")
            current_count, current_bytes = self.context_totals(ctx)
            if current_count + sum(n for n, _ in self.pending.values()) + count > MAX_CONTEXT_IMAGES:
                raise ModelRetry(f"当前上下文与待查看图片合计最多 {MAX_CONTEXT_IMAGES} 张；请减少本次图片或PDF页数")
            if current_bytes + sum(n for _, n in self.pending.values()) > MAX_CONTEXT_IMAGE_BYTES:
                raise ModelRetry("当前上下文图片已超过 32 MB，请压缩上下文后再查看")
            self.pending[token] = (count, 0)
        return token

    async def deliver(self, ctx, token, images):
        async with self.lock:
            if self.closed:
                raise ModelRetry("本轮图片查看已结束")
            current_count, current_bytes = self.context_totals(ctx)
            others = [value for key, value in self.pending.items() if key is not token]
            if current_count + sum(n for n, _ in others) + len(images) > MAX_CONTEXT_IMAGES:
                raise ModelRetry(f"当前上下文与待查看图片合计最多 {MAX_CONTEXT_IMAGES} 张")
            size = sum(len(image.data) for image in images)
            if current_bytes + sum(n for _, n in others) + size > MAX_CONTEXT_IMAGE_BYTES:
                raise ModelRetry("当前上下文与待查看图片合计超过 32 MB，请减少图片或PDF页数")
            self.pending.pop(token, None)
            self.delivered.update((id(image), (image, ctx.run_step)) for image in images)

    async def cached(self, key, loader, *, size_of, max_cache_bytes):
        if self.closed:
            raise ModelRetry("本轮图片查看已结束")
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        task = self.tasks.get(key)
        if task is None or task.cancelling() or task.cancelled():
            task = asyncio.create_task(loader(), name="image-view-load")
            self.tasks[key] = task
        self.waiters[task] = self.waiters.get(task, 0) + 1
        try:
            value = await asyncio.shield(task)
            size = size_of(value)
            if not self.closed and size <= max_cache_bytes:
                self.cache[key] = value
                self.cache_sizes[key] = size
                while sum(n for cached_key, n in self.cache_sizes.items() if cached_key[0] == key[0]) > max_cache_bytes:
                    old = next(cached_key for cached_key in self.cache if cached_key[0] == key[0])
                    self.cache.pop(old)
                    self.cache_sizes.pop(old, None)
            return value
        finally:
            remaining = self.waiters.get(task, 1) - 1
            if remaining:
                self.waiters[task] = remaining
            else:
                self.waiters.pop(task, None)
                try:
                    if not task.done():
                        task.cancel()
                        await _drain(task)
                finally:
                    if self.tasks.get(key) is task:
                        self.tasks.pop(key, None)

    async def aclose(self):
        self.closed = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        try:
            if tasks:
                await _drain(*tasks)
        finally:
            self.tasks.clear()
            self.waiters.clear()
            self.cache.clear()
            self.cache_sizes.clear()
            self.pending.clear()
            self.delivered.clear()


async def _drain(*tasks):
    cleanup = asyncio.gather(*tasks, return_exceptions=True)
    cancelled_again = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            cancelled_again = True
    cleanup.result()
    if cancelled_again:
        raise asyncio.CancelledError


def state_for(ctx: RunContext[AgentDeps]) -> ImageViewState:
    if ctx.deps.image_view_state is None:
        ctx.deps.image_view_state = ImageViewState()
    return ctx.deps.image_view_state


class ImageViewLifecycle(AbstractCapability[AgentDeps]):
    async def wrap_run(self, ctx: RunContext[AgentDeps], *, handler):
        state = ImageViewState()
        ctx.deps.image_view_state = state
        try:
            return await handler()
        finally:
            try:
                with anyio.CancelScope(shield=True):
                    await state.aclose()
            finally:
                if ctx.deps.image_view_state is state:
                    ctx.deps.image_view_state = None


@dataclass(frozen=True)
class ViewImageRuntime:
    toolset: Any
    lifecycle: ImageViewLifecycle


def build_view_image_runtime(
    resources: dict[str, ResolvedSkill], *, web_authorized: bool,
    python_available: bool, image_capable: bool,
) -> ViewImageRuntime | None:
    if not image_capable:
        return None
    from .toolset import build_view_image_toolset
    return ViewImageRuntime(
        build_view_image_toolset(resources, web_authorized=web_authorized,
                                 python_available=python_available),
        ImageViewLifecycle(),
    )
