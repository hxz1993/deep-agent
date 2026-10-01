from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import anyio
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability

from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.deps import AgentDeps
from deep_agent.skills.runtime import ResolvedSkill

from .cache import DocumentRunCache
from .settings import DocumentParsingRuntimeConfig
from .toolset import build_document_toolset


class DocumentCacheLifecycle(AbstractCapability[AgentDeps]):
    async def wrap_run(self, ctx: RunContext[AgentDeps], *, handler):
        cache = DocumentRunCache()
        ctx.deps.document_cache = cache
        try:
            return await handler()
        finally:
            # Runs and stream consumers share this SDK hook, including failure,
            # deferred output and cancellation. Cleanup never relies on after_run.
            with anyio.CancelScope(shield=True):
                await cache.aclose()
            if ctx.deps.document_cache is cache:
                ctx.deps.document_cache = None


@dataclass(frozen=True)
class DocumentRuntime:
    toolset: Any
    lifecycle: DocumentCacheLifecycle


def build_document_runtime(
    config: DocumentParsingRuntimeConfig, resources: dict[str, ResolvedSkill], *,
    context_window_tokens: int | None = None, estimator: TokenEstimator | None = None,
) -> DocumentRuntime:
    return DocumentRuntime(
        build_document_toolset(config, resources, context_window_tokens=context_window_tokens, estimator=estimator),
        DocumentCacheLifecycle(),
    )
