"""Opt-in, credential-free identity snapshot shared by a single Agent Run."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from contextlib import asynccontextmanager

import anyio
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability

from deep_agent.agent.deps import AgentDeps
from deep_agent.sandbox.context_env import EXECUTION_CONTEXT_ENV
from deep_agent.security.current_user import (
    CurrentUserAuthenticationError,
    CurrentUserUnavailableError,
    get_current_user_profile_resolver,
)

PROFILE_TIMEOUT_SECONDS = 5.0


@dataclass
class ExecutionContextState:
    task: asyncio.Task[str] | None = None

    async def aclose(self) -> None:
        task, self.task = self.task, None
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def _empty_context(user_id: str | None) -> dict:
    return {
        "user": {"id": user_id, "username": None, "nickname": None,
                 "display_name": None, "email": None, "avatar_url": None},
        "app": None, "space": None, "language": None,
        "resolution": "unavailable", "error": None,
    }


async def _resolve_context(deps: AgentDeps) -> str:
    value = _empty_context(deps.user)
    headers = (
        deps.execution_request_headers
        if deps.execution_request_headers is not None else deps.request_headers
    )
    try:
        async with asyncio.timeout(PROFILE_TIMEOUT_SECONDS):
            profile = await get_current_user_profile_resolver().profile_for_headers(
                deps.user or "", headers,
                local_username=deps.local_username, allow_empty=True,
            )
        value.update(
            user={"id": deps.user, "username": profile.username,
                  "nickname": profile.nickname, "display_name": profile.username,
                  "email": profile.email, "avatar_url": profile.avatar_url},
            app=(headers.get("app") if deps.execution_request_headers is not None else deps.app_id),
            space=(headers.get("space") if deps.execution_request_headers is not None else deps.space_id),
            language=headers.get("language"),
            resolution=profile.resolution,
        )
    except TimeoutError:
        value["error"] = {"code": "profile_timeout", "message": "用户资料查询超时"}
    except CurrentUserAuthenticationError:
        value["error"] = {"code": "profile_authentication_failed", "message": "用户资料身份验证失败"}
    except CurrentUserUnavailableError:
        value["error"] = {"code": "profile_unavailable", "message": "暂时无法获取用户资料"}
    except Exception:
        # Never expose service bodies, credentials or exception strings to code.
        value["error"] = {"code": "profile_unavailable", "message": "暂时无法获取用户资料"}
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


async def execution_context_env(
    deps: AgentDeps, *, include_context: bool = False,
) -> dict[str, str]:
    if not include_context:
        return {}
    if deps.execution_context_state is None:
        deps.execution_context_state = ExecutionContextState()
    state = deps.execution_context_state
    if state.task is None:
        state.task = asyncio.create_task(_resolve_context(deps))
    # One cancelled tool must not cancel another tool's identity lookup.
    return {EXECUTION_CONTEXT_ENV: await asyncio.shield(state.task)}


class ExecutionContextLifecycle(AbstractCapability[AgentDeps]):
    async def wrap_run(self, ctx: RunContext[AgentDeps], *, handler):
        # AgentRunner owns a full user Run, including provider-overflow recovery
        # which can invoke the SDK more than once. Direct SDK callers own here.
        if ctx.deps.execution_context_state is not None:
            return await handler()
        async with execution_context_scope(ctx.deps):
            return await handler()


@asynccontextmanager
async def execution_context_scope(deps: AgentDeps):
    state = ExecutionContextState()
    deps.execution_context_state = state
    try:
        yield
    finally:
        with anyio.CancelScope(shield=True):
            await state.aclose()
        if deps.execution_context_state is state:
            deps.execution_context_state = None
